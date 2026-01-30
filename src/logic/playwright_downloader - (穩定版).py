# -*- coding: utf-8 -*-
# src/logic/playwright_downloader.py
# [VibeCoding] Phase 42: Content Fingerprint (MD5 Deduplication + Iframe Deep Probe)

import os
import time
import m3u8
import shutil
import logging
import base64
import json
import uuid
import subprocess
import random
import hashlib # [Phase 42] 新增：用於計算數位指紋
from urllib.parse import urljoin
from typing import Callable, Optional
from playwright.sync_api import sync_playwright

try:
    from Crypto.Cipher import AES
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

class PlaywrightDownloader:
    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger("Playwright")
        self.is_cancelled = False
        
        # 狀態容器
        self.key_candidates = [] 
        self.confirmed_key = None
        self.playlist = None
        self.playlist_url = ""
        self.segments_map = {}   
        self.segment_counter = 0
        self.output_dir = ""
        self.profile_dir = "" 
        self.is_persistent = False
        self.video_duration = None 
        
        # [Phase 42] 指紋資料庫：用來儲存已下載切片的 MD5
        self.seen_fingerprints = set()
        
        # 鎖定檔路徑
        self.lock_file = os.path.join(os.getcwd(), "pressplay_global.lock")
        self.locked_by_me = False

    def _is_pid_alive(self, pid: int) -> bool:
        try:
            cmd = f'tasklist /FI "PID eq {pid}"'
            output = subprocess.check_output(cmd, shell=True).decode('big5', errors='ignore')
            return str(pid) in output
        except:
            return False

    def _acquire_lock(self, progress_callback):
        self.logger.info("[Queue] 正在排隊等待執行權...")
        while True:
            if self.is_cancelled: return False
            try:
                fd = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{os.getpid()}".encode())
                os.close(fd)
                self.locked_by_me = True
                self.logger.info("🟢 已取得執行權，準備開始...")
                return True
            except FileExistsError:
                try:
                    with open(self.lock_file, 'r') as f:
                        content = f.read().strip()
                    if content.isdigit():
                        locking_pid = int(content)
                        if not self._is_pid_alive(locking_pid):
                            self.logger.warning(f"⚠️ 發現殭屍鎖 (PID {locking_pid} 已死亡)，強制移除！")
                            try: os.remove(self.lock_file)
                            except: pass
                            continue
                except: pass

                if progress_callback:
                    progress_callback(0, "⏳ 排隊中 (等待其他 Pressplay 任務)...")
                time.sleep(2)
            except Exception as e:
                self.logger.error(f"鎖定機制錯誤: {e}")
                time.sleep(1)

    def _release_lock(self):
        if self.locked_by_me and os.path.exists(self.lock_file):
            try: os.remove(self.lock_file)
            except: pass
            self.locked_by_me = False

    def _get_duration_from_page(self, page):
        """強力偵測影片時長 (支援 Iframe)"""
        try:
            dur = page.evaluate("""
                () => {
                    let v = document.querySelector('video');
                    if (v && v.duration > 0) return v.duration;
                    const frames = document.querySelectorAll('iframe');
                    for (let f of frames) {
                        try {
                            v = f.contentDocument?.querySelector('video');
                            if (v && v.duration > 0) return v.duration;
                        } catch(e) {}
                    }
                    return 0;
                }
            """)
            if dur and float(dur) > 0:
                return float(dur)
        except: pass
        return None

    def download(self, target_url: str, output_path: str, progress_callback: Optional[Callable] = None) -> bool:
        if not HAS_CRYPTO:
            self.logger.error("❌ 缺少 pycryptodome")
            return False

        is_pressplay = "pressplay" in target_url
        if is_pressplay:
            if not self._acquire_lock(progress_callback): return False

        base_profile_dir = os.path.join(os.getcwd(), "browser_profiles")
        if not os.path.exists(base_profile_dir): os.makedirs(base_profile_dir)

        if is_pressplay:
            self.profile_dir = os.path.join(base_profile_dir, "pressplay_main")
            self.is_persistent = True
            self.logger.info(f"[Playwright] 啟動 Phase 42 (指紋去重 + 記憶登入)...")
        else:
            worker_id = str(uuid.uuid4())[:8]
            self.profile_dir = os.path.join(base_profile_dir, f"profile_{worker_id}")
            self.is_persistent = False
            self.logger.info(f"[Playwright] 啟動 Phase 42 (隔離模式)...")
        
        self.output_dir = output_path + "_temp"
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)

        try:
            with sync_playwright() as p:
                browser_args = [
                    "--disable-blink-features=AutomationControlled", 
                    "--mute-audio",
                    "--autoplay-policy=no-user-gesture-required",
                    "--disable-quic",
                    "--disable-web-security",
                    "--disable-features=IsolateOrigins,site-per-process",
                    "--aggressive-cache-discard",       
                ]

                context = p.chromium.launch_persistent_context(
                    self.profile_dir,
                    headless=False,
                    args=browser_args,
                    viewport={"width": 1280, "height": 720},
                    service_workers='block',            
                    bypass_csp=True,
                    ignore_https_errors=True
                )

                page = context.pages[0] if context.pages else context.new_page()

                try:
                    client = context.new_cdp_session(page)
                    client.send("Network.enable")
                    if not self.is_persistent:
                        client.send("Network.clearBrowserCookies")
                    client.send("Network.clearBrowserCache")
                    client.send("Network.setCacheDisabled", {"cacheDisabled": True})
                except Exception as e:
                    self.logger.error(f"❌ CDP 初始化失敗: {e}")
                    return False

                # === CDP 攔截邏輯 (加入指紋識別) ===
                def on_cdp_response(event):
                    if self.is_cancelled: return
                    try:
                        resp = event.get("response", {})
                        request_id = event.get("requestId")
                        url = resp.get("url", "")
                        mime = resp.get("mimeType", "").lower()
                        status = resp.get("status", 0)

                        if status != 200: return

                        if ".m3u8" in url and not self.playlist:
                            try:
                                body_data = client.send("Network.getResponseBody", {"requestId": request_id})
                                body = body_data.get("body", "")
                                content = base64.b64decode(body).decode() if body_data.get("base64Encoded") else body
                                if "#EXTINF" in content:
                                    self.playlist = m3u8.loads(content, uri=url)
                                    self.playlist_url = url
                                    # 初始化指紋庫
                                    self.seen_fingerprints.clear()
                                    self.logger.info(f"📜 [CDP] 鎖定 M3U8: {len(self.playlist.segments)} 切片")
                            except: pass

                        is_segment = False
                        if any(ext in url for ext in [".pms", ".ts", ".m4s", "segment"]): is_segment = True
                        elif "video/mp2t" in mime: is_segment = True

                        if is_segment:
                            # 1. 網址去重 (第一道防線)
                            if url in self.segments_map: return

                            try:
                                body_data = client.send("Network.getResponseBody", {"requestId": request_id})
                                body = body_data.get("body", "")
                                is_base64 = body_data.get("base64Encoded", False)
                                final_bytes = base64.b64decode(body) if is_base64 else body.encode('latin1')

                                if len(final_bytes) > 1024:
                                    # [Phase 42] 2. 內容指紋去重 (核心防線)
                                    # 計算檔案的 MD5 雜湊值
                                    file_hash = hashlib.md5(final_bytes).hexdigest()
                                    
                                    if file_hash in self.seen_fingerprints:
                                        # 如果這個內容已經下載過，那就是重複的片段 (例如重播的片頭)
                                        # 直接丟棄，不要寫入硬碟
                                        return
                                    
                                    # 加入指紋庫
                                    self.seen_fingerprints.add(file_hash)

                                    # 存檔
                                    self.segment_counter += 1
                                    fname = f"raw_{self.segment_counter:05d}.tmp"
                                    fpath = os.path.join(self.output_dir, fname)
                                    with open(fpath, "wb") as f:
                                        f.write(final_bytes)
                                    
                                    self.segments_map[url] = {'filename': fname}
                                    
                                    if self.segment_counter % 5 == 0:
                                        print(f"📥 [CDP] 已抓取 {self.segment_counter} 個切片", end='\r')
                            except: pass
                            
                    except Exception: pass

                client.on("Network.responseReceived", on_cdp_response)

                self.logger.info("🚀 前往頁面...")
                page.goto(target_url, timeout=0)

                # === 5. 循環監控 ===
                check_interval = 0
                max_idle = 600
                key_fetched_via_js = False

                while True:
                    if self.is_cancelled: break
                    
                    if not self.video_duration and check_interval % 2 == 0:
                        dur = self._get_duration_from_page(page)
                        if dur:
                            self.video_duration = dur
                            self.logger.info(f"⏱️ 偵測到影片時長: {self.video_duration:.2f} 秒")

                    if self.playlist and not self.confirmed_key and not key_fetched_via_js:
                        try:
                            target_key_uri = None
                            if self.playlist.keys:
                                key_info = self.playlist.keys[0]
                                if key_info and key_info.uri:
                                    target_key_uri = key_info.uri
                                    if not target_key_uri.startswith("http"):
                                        target_key_uri = urljoin(self.playlist_url, target_key_uri)
                            
                            if target_key_uri:
                                self.logger.info(f"🔫 [JS] 發動主動奪鑰: {target_key_uri}")
                                key_b64 = page.evaluate(f"""
                                    async () => {{
                                        try {{
                                            const resp = await fetch('{target_key_uri}');
                                            if (resp.status !== 200) return null;
                                            const buf = await resp.arrayBuffer();
                                            if (buf.byteLength !== 16) return 'BAD_LEN:' + buf.byteLength;
                                            let binary = '';
                                            const bytes = new Uint8Array(buf);
                                            for (let i = 0; i < bytes.byteLength; i++) {{
                                                binary += String.fromCharCode(bytes[i]);
                                            }}
                                            return btoa(binary);
                                        }} catch (e) {{ return 'ERR:' + e.toString(); }}
                                    }}
                                """)
                                
                                if key_b64 and not key_b64.startswith("ERR") and not key_b64.startswith("BAD"):
                                    self.confirmed_key = base64.b64decode(key_b64)
                                    self.logger.info(f"🎉 [JS] 奪鑰成功！Hex: {self.confirmed_key.hex()}")
                                    key_fetched_via_js = True
                                else:
                                    self.logger.warning(f"⚠️ [JS] 奪鑰失敗: {key_b64}")
                            else:
                                self.logger.info("ℹ️ Playlist 中未發現 Key 定義 (判定為無加密)")
                                key_fetched_via_js = True 
                        except Exception as e:
                            self.logger.error(f"❌ JS 注入錯誤: {e}")

                    # 自動加速
                    if check_interval % 2 == 0:
                        try:
                            page.evaluate("""
                                () => {
                                    const videos = [
                                        document.querySelector('video'),
                                        ...Array.from(document.querySelectorAll('iframe'))
                                            .map(f => f.contentDocument?.querySelector('video'))
                                            .filter(Boolean)
                                    ];
                                    videos.forEach(v => {
                                        if (v) {
                                            if (v.playbackRate < 16.0) v.playbackRate = 16.0;
                                            v.muted = true;
                                            if (v.paused) v.play();
                                            if (v.currentTime > 20 && !window._rewound) {
                                                v.currentTime = 0;
                                                window._rewound = true;
                                            }
                                        }
                                    });
                                }
                            """)
                        except: pass

                    saved = self.segment_counter
                    total = len(self.playlist.segments) if self.playlist else 0
                    
                    if progress_callback:
                        status = "CDP下載中"
                        if not self.confirmed_key: status = "⚡嘗試奪鑰..."
                        else: status = "✅金鑰已鎖定"
                        if self.video_duration: status += f" | ⏱️{int(self.video_duration)}s"
                        
                        p = (saved / total * 90) if total > 0 else 0
                        msg = f"{status} | 切片:{saved}/{total}"
                        progress_callback(p, msg)

                    if total > 0 and saved >= total:
                        if self.confirmed_key or key_fetched_via_js:
                            self.logger.info("✅ 下載完成！")
                            time.sleep(2)
                            break
                        else:
                            if check_interval % 5 == 0:
                                self.logger.warning("⚠️ 切片齊全但 Key 狀態未確認，正在重試...")
                                key_fetched_via_js = False 
                    
                    try:
                        if page.evaluate("""
                            () => {
                                const videos = [
                                    document.querySelector('video'),
                                    ...Array.from(document.querySelectorAll('iframe'))
                                        .map(f => f.contentDocument?.querySelector('video'))
                                        .filter(Boolean)
                                ];
                                return videos.some(v => v.ended);
                            }
                        """):
                            break
                    except: pass

                    time.sleep(1)
                    check_interval += 1
                    if check_interval > max_idle: break

                if not self.video_duration:
                    self.logger.info("🔍 最後嘗試獲取影片時長...")
                    dur = self._get_duration_from_page(page)
                    if dur:
                        self.video_duration = dur
                        self.logger.info(f"✅ 補考成功！影片時長: {self.video_duration:.2f} 秒")
                    else:
                        self.logger.warning("⚠️ 警告：無法偵測影片時長，將跳過修剪步驟")

                self.logger.info("🛑 關閉瀏覽器...")
                try: context.close()
                except: pass

            # === 6. 後處理解密 ===
            if self.is_cancelled: 
                self._cleanup_profile()
                self._release_lock()
                return False
            
            final_key = self.confirmed_key
            self.logger.info(f"🔐 開始解密與合併 (Key: {'有' if final_key else '無'})...")
            
            if final_key:
                with open(os.path.join(self.output_dir, "key.bin"), "wb") as f:
                    f.write(final_key)

            files = sorted([f for f in os.listdir(self.output_dir) if f.startswith("raw_") and f.endswith(".tmp")])
            ts_output = output_path.replace(".mp4", ".ts")
            
            with open(ts_output, 'wb') as outfile:
                for fname in files:
                    fpath = os.path.join(self.output_dir, fname)
                    with open(fpath, 'rb') as infile:
                        data = infile.read()
                    
                    decrypted_data = data
                    if final_key and len(data) % 16 == 0:
                        try:
                            seq_str = fname.split('_')[1].split('.')[0]
                            iv = int(seq_str).to_bytes(16, byteorder='big')
                            cipher = AES.new(final_key, AES.MODE_CBC, iv)
                            decrypted_data = cipher.decrypt(data)
                        except: pass
                    
                    outfile.write(decrypted_data)

            try: shutil.rmtree(self.output_dir)
            except: pass
            
            self._cleanup_profile()
            self._release_lock()

            self._convert_and_trim_mp4(ts_output, output_path)
            return True

        except Exception as e:
            self.logger.error(f"[Playwright] Critical Error: {e}", exc_info=True)
            self._cleanup_profile()
            self._release_lock()
            return False

    def _cleanup_profile(self):
        if self.is_persistent:
            self.logger.info("🔒 保留 Pressplay 設定檔 (維持登入狀態)")
            return

        if self.temp_profile_dir and os.path.exists(self.temp_profile_dir):
            try:
                self.logger.info(f"🧹 清理暫存設定檔: {self.temp_profile_dir}")
                time.sleep(1)
                shutil.rmtree(self.temp_profile_dir, ignore_errors=True)
            except Exception as e:
                self.logger.warning(f"⚠️ 清理設定檔失敗 (可忽略): {e}")

    def _convert_and_trim_mp4(self, input_ts, output_mp4):
        import subprocess
        ffmpeg_path = os.path.join(os.getcwd(), "bin", "ffmpeg.exe")
        if not os.path.exists(ffmpeg_path):
            if os.path.exists(output_mp4): os.remove(output_mp4)
            os.rename(input_ts, output_mp4)
            return
        
        full_mp4 = output_mp4.replace(".mp4", "_full_untouched.mp4")
        
        self.logger.info("🔄 步驟 1/2: 封裝為完整 MP4 (修復時間軸)...")
        cmd_1 = [
            ffmpeg_path, "-y", "-i", input_ts,
            "-c", "copy",
            "-bsf:a", "aac_adtstoasc",
            full_mp4
        ]
        
        try:
            subprocess.run(cmd_1, creationflags=0x08000000, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if os.path.exists(input_ts): os.remove(input_ts)
            
            if self.video_duration:
                trim_duration = self.video_duration + 0.1
                self.logger.info(f"✂️ 步驟 2/2: 精準修剪 MP4 (保留前 {trim_duration:.2f} 秒)...")
                
                cmd_2 = [
                    ffmpeg_path, "-y", "-i", full_mp4,
                    "-t", str(trim_duration),
                    "-c", "copy",
                    output_mp4
                ]
                subprocess.run(cmd_2, creationflags=0x08000000, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
                if os.path.exists(full_mp4): os.remove(full_mp4)
                self.logger.info("✅ 影片處理完成 (已修剪)！")
            else:
                self.logger.warning("⚠️ 未取得時長，略過修剪步驟")
                if os.path.exists(output_mp4): os.remove(output_mp4)
                os.rename(full_mp4, output_mp4)
                self.logger.info("✅ 影片處理完成 (未修剪)！")
            
        except Exception as e:
            self.logger.error(f"FFmpeg 轉檔/修剪失敗: {e}")
            if os.path.exists(full_mp4) and not os.path.exists(output_mp4):
                os.rename(full_mp4, output_mp4)