# src/logic/native_downloader.py
import os
import requests
import m3u8
import shutil
import subprocess
import time
import random
import threading
from Crypto.Cipher import AES
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Dict, Callable

# 嘗試引入 curl_cffi，若無則降級使用 requests
try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False

class NativeHLSDownloader:
    def __init__(self, logger=None):
        self.logger = logger
        self.is_cancelled = False
        self.session = None
        # 偽裝成真實瀏覽器的 Headers
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive",
            "Referer": "https://www.google.com/"
        }

    def stop(self):
        self.is_cancelled = True

    def _get_session(self):
        if self.session: return self.session
        if HAS_CURL_CFFI:
            # 隨機切換一點指紋版本
            self.session = cffi_requests.Session(impersonate="chrome120")
        else:
            self.session = requests.Session()
        self.session.headers.update(self.headers)
        return self.session

    def download(self, m3u8_url: str, output_path: str, headers: Dict = None, page_url: str = None, progress_callback: Callable = None) -> bool:
        """
        主下載邏輯：智能兩段式變速 (Hybrid Mode - Optimized for Stability)
        Phase 1: 穩健衝刺 (8線程 + 微延遲，避免觸發 800 片段限制)
        Phase 2: 持久補漏 (抗壓模式)
        """
        session = self._get_session()
        if headers: session.headers.update(headers)
        if page_url: session.headers["Referer"] = page_url

        temp_dir = os.path.join(os.path.dirname(output_path), "_temp_" + str(int(time.time())))
        if not os.path.exists(temp_dir): os.makedirs(temp_dir)

        try:
            self.logger.info(f"[Native] 解析 M3U8: {m3u8_url}")
            r = session.get(m3u8_url, timeout=15)
            if r.status_code != 200:
                self.logger.error(f"[Native] M3U8 讀取失敗: {r.status_code}")
                return False

            playlist = m3u8.loads(r.text)
            if not playlist.segments:
                self.logger.error("[Native] M3U8 中沒有影片片段")
                return False

            # --- 處理加密 Key ---
            key = None
            iv = None
            if playlist.keys and playlist.keys[0] and playlist.keys[0].uri:
                key_obj = playlist.keys[0]
                key_uri = key_obj.uri
                if not key_uri.startswith(('http', 'https')):
                    base_uri = m3u8_url.rsplit('/', 1)[0]
                    key_uri = f"{base_uri}/{key_uri}"

                self.logger.info(f"[Native] 下載金鑰: {key_uri}")
                key_r = session.get(key_uri, timeout=15)
                if key_r.status_code == 200:
                    key = key_r.content
                    if key_obj.iv:
                        iv = bytes.fromhex(key_obj.iv.replace("0x", ""))
                else:
                    self.logger.error(f"[Native] Key 下載失敗")
                    return False

            # --- 準備任務 ---
            base_uri = m3u8_url.rsplit('/', 1)[0]
            all_tasks = []
            
            for i, seg in enumerate(playlist.segments):
                seg_url = seg.uri
                if not seg_url.startswith(('http', 'https')):
                    seg_url = f"{base_uri}/{seg_url}"
                
                current_iv = iv
                if key and not current_iv:
                    seq_num = seg.media_sequence if seg.media_sequence else i
                    current_iv = seq_num.to_bytes(16, 'big')

                fname = f"seg_{i:05d}.ts"
                fpath = os.path.join(temp_dir, fname)
                all_tasks.append((seg_url, fpath, key, current_iv))

            total_segs = len(all_tasks)
            self.logger.info(f"[Native] 任務準備就緒: {total_segs} 片段。啟動智能變速引擎 (穩健版)...")

            # --- 智能變速迴圈 ---
            round_idx = 0
            consecutive_no_progress = 0
            
            while True:
                if self.is_cancelled: break
                round_idx += 1
                
                # 1. 找出未完成的任務
                pending = [t for t in all_tasks if not (os.path.exists(t[1]) and os.path.getsize(t[1]) > 0)]
                
                if not pending:
                    self.logger.info("[Native] 所有片段下載完成！")
                    break

                # 2. 智能檔位切換 (Intelligent Gear Shift) - [TWEAKED]
                if round_idx == 1:
                    # === R1: 穩健衝刺 (Steady Blitz) ===
                    # [修改] 降速以求穩。16線程->8線程，0延遲->0.1s延遲
                    # 這樣通常能繞過 "800片段" 的觸發閾值
                    workers = 8           
                    sleep_time = 0.1      
                    timeout = 15
                    mode_name = "🚄 穩健衝刺"
                else:
                    # === R2+: 抗壓補漏 (Endurance Mode) ===
                    workers = 3           
                    sleep_time = 1.5      
                    timeout = 30
                    mode_name = "🛡️ 抗壓補漏"
                    
                    cooldown = min(10 + (round_idx * 5), 60)
                    self.logger.warning(f"[Native] {mode_name}: 檢測到 R{round_idx-1} 有殘留 ({len(pending)}個)，休息 {cooldown} 秒避風頭...")
                    time.sleep(cooldown)

                self.logger.info(f"[Native] 第 {round_idx} 輪: {mode_name} | 線程: {workers} | 剩餘: {len(pending)}")

                # 3. 執行下載
                success_in_this_round = 0
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = {
                        executor.submit(
                            self._download_segment_core, 
                            t[0], t[1], t[2], t[3], session, sleep_time, timeout
                        ): t[1] for t in pending
                    }
                    
                    completed_so_far = 0
                    for future in as_completed(futures):
                        if self.is_cancelled: break
                        if future.result():
                            success_in_this_round += 1
                        
                        completed_so_far += 1
                        current_total_done = total_segs - len(pending) + success_in_this_round
                        if progress_callback:
                            percent = (current_total_done / total_segs) * 99
                            mode_icon = "🚄" if round_idx == 1 else "🛡️"
                            msg = f"{mode_icon} {current_total_done}/{total_segs} (R{round_idx})"
                            progress_callback(percent, msg)

                # 4. 死局判斷
                if success_in_this_round > 0:
                    consecutive_no_progress = 0
                else:
                    consecutive_no_progress += 1
                
                if consecutive_no_progress >= 5 and round_idx > 1:
                    self.logger.error("[Native] IP 可能已被永久封鎖，停止任務。")
                    break

            if self.is_cancelled: return False

            # --- 合併與轉檔 ---
            final_count = len([t for t in all_tasks if os.path.exists(t[1]) and os.path.getsize(t[1]) > 0])
            if final_count < total_segs:
                if final_count > total_segs * 0.8:
                    self.logger.warning(f"[Native] 警告: 仍有缺片 ({final_count}/{total_segs})，強行合併...")
                else:
                    self.logger.error(f"[Native] 嚴重失敗: 缺片過多，放棄合併")
                    return False

            files = [t[1] for t in all_tasks if os.path.exists(t[1])]
            merged_ts = os.path.join(temp_dir, "merged.ts")
            with open(merged_ts, 'wb') as outfile:
                for f in files:
                    with open(f, 'rb') as infile: shutil.copyfileobj(infile, outfile)

            if progress_callback: progress_callback(99.5, "正在修復轉檔...")
            success = self._convert_to_mp4(merged_ts, output_path)
            
            try: shutil.rmtree(temp_dir, ignore_errors=True)
            except: pass
            
            return success

        except Exception as e:
            self.logger.error(f"[Native] 錯誤: {e}", exc_info=True)
            return False

    def _download_segment_core(self, url, save_path, key, iv, session, sleep_time, timeout) -> bool:
        """
        核心下載單元
        """
        if sleep_time > 0:
            time.sleep(random.uniform(sleep_time * 0.8, sleep_time * 1.2))
        
        try:
            r = session.get(url, timeout=timeout)
            if r.status_code != 200: return False
            
            data = r.content

            # [Fix 1] 移除偽裝頭 (PNG/JPG)
            if len(data) > 10 and (data.startswith(b'\x89PNG') or data.startswith(b'\xFF\xD8\xFF')):
                sync_offset = -1
                for i in range(min(len(data), 4096)):
                    if data[i] == 0x47 and (i+188 < len(data) and data[i+188] == 0x47):
                        sync_offset = i
                        break
                if sync_offset > 0: data = data[sync_offset:]

            # 解密
            if key and iv:
                try:
                    cipher = AES.new(key, AES.MODE_CBC, iv)
                    data = cipher.decrypt(data)
                except: pass

            # [Fix 2] 解密後再次檢查偽裝頭
            if len(data) > 10 and (data.startswith(b'\x89PNG') or data.startswith(b'\xFF\xD8\xFF')):
                 sync_offset = -1
                 for i in range(min(len(data), 4096)):
                    if data[i] == 0x47 and (i+188 < len(data) and data[i+188] == 0x47):
                        sync_offset = i
                        break
                 if sync_offset > 0: data = data[sync_offset:]

            with open(save_path, 'wb') as f: f.write(data)
            return True
        except:
            return False

    def _convert_to_mp4(self, ts_path, mp4_path):
        ffmpeg_exe = "ffmpeg"
        if os.path.exists("bin/ffmpeg.exe"): ffmpeg_exe = "bin/ffmpeg.exe"
        
        cmd = [
            ffmpeg_exe, "-y", "-fflags", "+genpts", "-ignore_unknown",
            "-i", ts_path, "-c", "copy", "-bsf:a", "aac_adtstoasc", mp4_path
        ]
        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, startupinfo=startupinfo)
            return True
        except:
            return False
