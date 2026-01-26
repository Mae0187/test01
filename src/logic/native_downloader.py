# -*- coding: utf-8 -*-
# src/logic/native_downloader.py
# [VibeCoding] Phase 5: Native HLS Downloader with TLS Masquerading (curl_cffi)

import os
import m3u8
import shutil
import time
import logging
import binascii
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
from typing import Dict, Callable, Optional

# === [核心變更] 引入 curl_cffi 取代標準 requests ===
try:
    from curl_cffi import requests
    HAS_CURL = True
except ImportError:
    HAS_CURL = False

try:
    from Crypto.Cipher import AES
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

class NativeHLSDownloader:
    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger("NativeHLS")
        self.is_cancelled = False
        self.key_cache = {} 
        
        # 初始化 Session (啟用瀏覽器偽裝)
        if HAS_CURL:
            self.session = requests.Session(impersonate="chrome120")
        else:
            self.session = None

    def download(self, m3u8_url: str, headers: Dict, output_path: str, page_url: str,
                 progress_callback: Optional[Callable[[float, str], None]] = None) -> bool:
        
        if not HAS_CURL:
            self.logger.error("❌ 缺少 curl_cffi 庫。請執行: pip install curl_cffi")
            return False
        if not HAS_CRYPTO:
            self.logger.error("❌ 缺少 pycryptodome 庫。")
            return False

        self.logger.info(f"[Native] 啟動 Phase 5 (TLS 偽裝模式): {m3u8_url}")

        # === Header 設定 ===
        # curl_cffi 會自動處理大部分指紋，我們只需要補上關鍵的 Referer
        clean_headers = {
            k: v for k, v in headers.items() 
            if k.lower() not in ['host', 'content-length', 'upgrade-insecure-requests', 'accept-encoding']
        }
        
        # 強制 Referer 與 Origin
        clean_headers['Referer'] = page_url
        parsed_uri = urlparse(page_url)
        clean_headers['Origin'] = f"{parsed_uri.scheme}://{parsed_uri.netloc}"
        
        # 更新 Session Headers
        self.session.headers.update(clean_headers)
        
        try:
            # 1. 解析 M3U8
            content = self._get_url_content(m3u8_url)
            if not content: return False
            
            playlist = m3u8.loads(content, uri=m3u8_url)
            
            if playlist.is_variant:
                # 選擇最佳畫質
                best_playlist = max(playlist.playlists, key=lambda p: p.stream_info.bandwidth or 0)
                variant_url = best_playlist.absolute_uri
                self.logger.info(f"[Native] 轉向子列表: {variant_url}")
                content = self._get_url_content(variant_url)
                playlist = m3u8.loads(content, uri=variant_url)

            segments = playlist.segments
            total_segments = len(segments)
            if total_segments == 0:
                raise Exception("M3U8 列表為空")

            self.logger.info(f"[Native] 發現 {total_segments} 個切片，偽裝 Chrome 120 下載中...")

            # 2. 準備暫存
            temp_dir = output_path + "_temp"
            if not os.path.exists(temp_dir):
                os.makedirs(temp_dir)

            # 3. 並發下載
            # curl_cffi 的並發性能極強，但為了保險起見，我們維持在 4-6
            with ThreadPoolExecutor(max_workers=5) as executor:
                future_to_idx = {}
                
                for idx, seg in enumerate(segments):
                    if self.is_cancelled: break
                    
                    seg_url = seg.absolute_uri
                    fname = f"seg_{idx:05d}.ts"
                    save_path = os.path.join(temp_dir, fname)
                    
                    # 處理加密 Key
                    key_info = None
                    if seg.key and seg.key.method == 'AES-128':
                        key_uri = seg.key.absolute_uri
                        iv = seg.key.iv
                        if not iv:
                            iv = idx.to_bytes(16, byteorder='big')
                        elif isinstance(iv, str) and iv.startswith('0x'):
                            iv = binascii.unhexlify(iv[2:].zfill(32))
                        key_info = {'uri': key_uri, 'iv': iv}
                    
                    # 檢查是否已下載
                    if not os.path.exists(save_path) or os.path.getsize(save_path) == 0:
                        future = executor.submit(self._download_and_decrypt, seg_url, save_path, key_info)
                        future_to_idx[future] = idx
                    
                # 進度監控
                completed_count = total_segments - len(future_to_idx)
                for future in as_completed(future_to_idx):
                    if self.is_cancelled: return False
                    try:
                        future.result()
                        completed_count += 1
                        if progress_callback:
                            percent = (completed_count / total_segments) * 95
                            progress_callback(percent, f"下載中 ({completed_count}/{total_segments})")
                    except Exception as e:
                        # 記錄具體錯誤
                        self.logger.error(f"[Native] 下載失敗: {e}")

            # 驗證成功率
            success_count = 0
            for idx in range(total_segments):
                fname = os.path.join(temp_dir, f"seg_{idx:05d}.ts")
                if os.path.exists(fname) and os.path.getsize(fname) > 0:
                    success_count += 1
            
            if success_count < total_segments * 0.8: # 要求至少 80% 成功
                self.logger.error(f"[Native] 嚴重失敗: 僅成功下載 {success_count}/{total_segments}。")
                return False

            if self.is_cancelled: return False

            # 4. 合併
            self.logger.info("[Native] 合併檔案...")
            ts_output = output_path.replace(".mp4", ".ts")
            with open(ts_output, 'wb') as outfile:
                for idx in range(total_segments):
                    fname = os.path.join(temp_dir, f"seg_{idx:05d}.ts")
                    if os.path.exists(fname):
                        with open(fname, 'rb') as infile:
                            shutil.copyfileobj(infile, outfile)
            
            try: shutil.rmtree(temp_dir)
            except: pass
            
            self._convert_to_mp4(ts_output, output_path)
            return True

        except Exception as e:
            self.logger.error(f"[Native] 嚴重錯誤: {e}", exc_info=True)
            return False

    def _get_url_content(self, url):
        try:
            resp = self.session.get(url, timeout=15)
            # curl_cffi 的 raise_for_status 用法略有不同，但這裡通用
            if resp.status_code != 200:
                raise Exception(f"HTTP {resp.status_code}")
            return resp.text
        except Exception as e:
            self.logger.error(f"[Native] M3U8 請求失敗: {e}")
            return None

    def _get_key(self, key_uri):
        if key_uri in self.key_cache: return self.key_cache[key_uri]
        try:
            resp = self.session.get(key_uri, timeout=15)
            if resp.status_code != 200: raise Exception(f"Key HTTP {resp.status_code}")
            self.key_cache[key_uri] = resp.content
            return resp.content
        except Exception as e:
            raise Exception(f"Key download failed: {key_uri}")

    def _download_and_decrypt(self, url, save_path, key_info=None):
        last_error = None
        for _ in range(3): # 重試 3 次
            try:
                resp = self.session.get(url, timeout=20)
                if resp.status_code != 200:
                    raise Exception(f"HTTP {resp.status_code}")
                
                data = resp.content
                
                # AES 解密
                if key_info:
                    key_bytes = self._get_key(key_info['uri'])
                    cipher = AES.new(key_bytes, AES.MODE_CBC, key_info['iv'])
                    try:
                        data = cipher.decrypt(data)
                    except: pass 

                with open(save_path, 'wb') as f:
                    f.write(data)
                return
            except Exception as e:
                last_error = str(e)
                time.sleep(1)
        
        raise Exception(f"Failed: {last_error}")

    def _convert_to_mp4(self, input_ts, output_mp4):
        ffmpeg_path = os.path.join(os.getcwd(), "bin", "ffmpeg.exe")
        if not os.path.exists(ffmpeg_path):
            if os.path.exists(output_mp4): os.remove(output_mp4)
            os.rename(input_ts, output_mp4)
            return

        import subprocess
        cmd = [ffmpeg_path, "-y", "-i", input_ts, "-c", "copy", "-bsf:a", "aac_adtstoasc", output_mp4]
        try:
            subprocess.run(cmd, creationflags=0x08000000, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if os.path.exists(input_ts): os.remove(input_ts)
        except: pass

    def stop(self):
        self.is_cancelled = True