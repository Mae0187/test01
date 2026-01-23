# src/logic/downloader.py
import os
import yt_dlp
import re
import time
import logging
import shutil
import tempfile
from typing import Dict, Any, Optional
from urllib.parse import urlparse
from PySide6.QtCore import QThread, Signal, QObject, QMutex

GLOBAL_SNIFFER_LOCK = QMutex()

try:
    from src.logic.sniffer import BrowserSniffer
    HAS_SELENIUM = True
except ImportError:
    HAS_SELENIUM = False

class WorkerSignals(QObject):
    progress = Signal(str, str, float, str, str)
    status = Signal(str, str)
    finished = Signal(str)
    error = Signal(str, str)

class YtDlpLogger:
    def __init__(self, is_retry: bool):
        self.is_retry = is_retry
        self.logger = logging.getLogger("YtDlp")

    def debug(self, msg):
        if not msg.startswith('[debug] '):
            self.logger.debug(msg)

    def warning(self, msg):
        # 忽略常見的 HLS 警告
        if "Deprecated Feature" in msg or "cookies" in msg or "live" in msg: return
        self.logger.warning(f"[YtDlp Warn] {msg}")

    def error(self, msg):
        if "Deprecated Feature" in msg or "cookies" in msg: return
        
        self.logger.debug(f"[YtDlp Error Raw] {msg}")
        if not self.is_retry:
            self.logger.info(f"偵測到錯誤，準備啟動嗅探救援... (Error: {msg[:50]}...)")
            return
        print(f"[Core Error] {msg}")
        self.logger.error(f"{msg}")

class DownloadWorker(QThread):
    def __init__(self, task_id: str, url: str, config: Dict[str, Any]):
        super().__init__()
        self.task_id = task_id
        self.url = url
        self.config = config
        self.signals = WorkerSignals()
        self.is_cancelled = False
        self.logger = logging.getLogger(f"Worker-{task_id[:4]}")

    def run(self):
        self.logger.info(f"任務啟動: {self.url}")
        success = self._try_download(self.url, is_retry=False)
        
        if not success and not self.is_cancelled:
            if HAS_SELENIUM:
                self.signals.status.emit(self.task_id, "排隊等待嗅探...")
                self.logger.info("請求嗅探鎖...")
                
                GLOBAL_SNIFFER_LOCK.lock()
                try:
                    if self.is_cancelled: return
                    self.signals.status.emit(self.task_id, "啟動深度嗅探...")
                    self.logger.info("已獲取鎖，開始嗅探")
                    real_url, sniffed_headers = self._perform_sniffing(self.url)
                finally:
                    GLOBAL_SNIFFER_LOCK.unlock()
                    self.logger.info("釋放嗅探鎖")
                
                if real_url:
                    self.signals.status.emit(self.task_id, "獲取成功，開始下載...")
                    self.logger.info(f"嗅探成功，目標: {real_url}")
                    
                    success_retry = self._try_download(
                        real_url, 
                        is_retry=True, 
                        headers=sniffed_headers 
                    )
                    
                    if success_retry:
                        self.signals.status.emit(self.task_id, "完成")
                        self.signals.finished.emit(self.task_id)
                        self.logger.info("下載完成")
                    else:
                        self.signals.error.emit(self.task_id, "下載失敗 (請查看詳細日誌)")
                        self.logger.error("重試下載失敗")
                else:
                    self.signals.error.emit(self.task_id, "嗅探失敗 (找不到影片)")
                    self.logger.error("嗅探回傳空值")
            else:
                self.signals.error.emit(self.task_id, "解析失敗 (建議安裝 Selenium)")

        elif success and not self.is_cancelled:
            self.signals.status.emit(self.task_id, "完成")
            self.signals.finished.emit(self.task_id)
            self.logger.info("直接下載完成")

    def _perform_sniffing(self, target_url: str):
        try:
            sniffer = BrowserSniffer()
            return sniffer.extract_stream_url(target_url)
        except Exception as e:
            self.logger.error(f"嗅探器異常: {e}", exc_info=True)
            return None, {}

    def _detect_ffmpeg_path(self) -> Optional[str]:
        cwd = os.getcwd()
        possible_paths = [
            cwd,                            
            os.path.join(cwd, 'bin'),       
            os.path.join(cwd, 'ffmpeg', 'bin'),
        ]
        for path in possible_paths:
            exe_path = os.path.join(path, 'ffmpeg.exe')
            if os.path.exists(exe_path):
                self.logger.info(f"已定位 FFmpeg: {exe_path}")
                return path 
        if shutil.which('ffmpeg'):
            self.logger.info("已定位 FFmpeg (系統 PATH)")
            return None 
        self.logger.warning("未偵測到 FFmpeg，HLS 下載可能會失敗")
        return None
    
    def _create_temp_cookie_file(self, raw_cookie: str, target_url: str) -> Optional[str]:
        try:
            parsed = urlparse(target_url)
            domain = parsed.hostname
            if not domain: return None
            # 確保 cookie domain 格式正確
            if domain.startswith('www.'): domain = domain[3:]
            domain_flag = 'TRUE'
            if not domain.startswith('.'): domain = '.' + domain

            # 使用 Temp 目錄，避開中文路徑問題
            temp_dir = tempfile.gettempdir()
            filename = f"cookies_{int(time.time())}_{self.task_id[:4]}.txt"
            filepath = os.path.join(temp_dir, filename)
            
            expiry = int(time.time() + 3600*24) 
            
            # 這裡我們不進行過度過濾，因為 yt-dlp 可以處理較大的 Cookie 檔案
            # 只做基本的清理
            parts = raw_cookie.split(';')
            
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write("# Netscape HTTP Cookie File\n")
                for part in parts:
                    part = part.strip()
                    if '=' in part:
                        k, v = part.split('=', 1)
                        k, v = k.strip(), v.strip()
                        # 基本的 Latin-1 檢查，防止寫入亂碼
                        try:
                            k.encode('latin-1')
                            v.encode('latin-1')
                        except UnicodeEncodeError:
                            continue
                        f.write(f"{domain}\t{domain_flag}\t/\tTRUE\t{expiry}\t{k}\t{v}\n")
            return filepath
        except Exception as e:
            self.logger.error(f"Cookie 轉檔失敗: {e}")
            return None

    def _sanitize_headers_strict(self, headers: Dict[str, str]) -> Dict[str, str]:
        clean_headers = {}
        for k, v in headers.items():
            if not k or not v: continue
            try:
                # 確保 Header 是純 ASCII/Latin-1
                str(k).encode('latin-1')
                str(v).encode('latin-1')
                clean_headers[str(k)] = str(v)
            except UnicodeEncodeError:
                continue
        return clean_headers

    def _try_download(self, download_url: str, is_retry: bool, headers: Dict = None) -> bool:
        save_path = self.config.get('download_path', os.getcwd())
        custom_name = self.config.get('custom_name', '')
        
        out_tmpl = os.path.join(save_path, '%(title)s.%(ext)s')
        if custom_name:
             out_tmpl = os.path.join(save_path, f"{custom_name}.%(ext)s")
        elif is_retry:
             out_tmpl = os.path.join(save_path, f"Video_{self.task_id[:8]}.%(ext)s")

        raw_headers = {}
        if headers: raw_headers.update(headers)
        
        # 1. 處理 Cookie：轉為檔案，並從 Header 移除以避免衝突
        temp_cookie_file = None
        if 'Cookie' in raw_headers:
            raw_cookie = raw_headers['Cookie']
            self.logger.info("🍪 生成 Cookie 檔案以確保穩定性...")
            temp_cookie_file = self._create_temp_cookie_file(raw_cookie, download_url)
            del raw_headers['Cookie'] # 重要：移除 Header 中的 Cookie，強制使用檔案

        # 2. 清洗 Headers
        final_headers = self._sanitize_headers_strict(raw_headers)
        ffmpeg_loc = self._detect_ffmpeg_path()
        
        # 3. yt-dlp 設定 (The Clone Protocol)
        ydl_opts = {
            'format': 'bestvideo+bestaudio/best',
            'outtmpl': out_tmpl,
            'noplaylist': True,
            'progress_hooks': [self._progress_hook],
            'http_headers': final_headers, # 傳遞 User-Agent 和 Referer
            'retries': 10,
            'fragment_retries': 10,
            'nocolor': True,
            'logger': YtDlpLogger(is_retry),
            
            # [關鍵策略]
            # 使用 FFmpeg 下載，但透過 yt-dlp 管理
            'hls_prefer_native': False, 
            
            # 傳遞參數給 FFmpeg 解鎖 .pms
            'downloader_args': {
                'ffmpeg_i': ['-allowed_extensions', 'ALL']
            },
            
            'nocheckcertificate': True,
            'ignoreerrors': True,
        }

        # 掛載 Cookie 檔案
        if temp_cookie_file:
            ydl_opts['cookiefile'] = temp_cookie_file
            self.logger.info(f"✅ 掛載 Cookie 檔案: {os.path.basename(temp_cookie_file)}")
        elif '_USE_COOKIE_FILE' in raw_headers: 
            fallback = raw_headers.get('_USE_COOKIE_FILE')
            if fallback and os.path.exists(fallback):
                ydl_opts['cookiefile'] = fallback
                if '_USE_COOKIE_FILE' in final_headers:
                    del final_headers['_USE_COOKIE_FILE']

        if ffmpeg_loc:
            ydl_opts['ffmpeg_location'] = ffmpeg_loc

        # 4. 執行下載
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                if not is_retry:
                    self.signals.status.emit(self.task_id, "解析資訊中...")
                    ydl.extract_info(download_url, download=False)

                if self.is_cancelled: return False

                self.signals.status.emit(self.task_id, "下載中...")
                ret_code = ydl.download([download_url])
                
                if ret_code == 0:
                    return True
                else:
                    self.logger.warning(f"yt-dlp 回傳非零代碼: {ret_code}")
                    return False

        except Exception as e:
            self.logger.error(f"下載失敗: {e}")
            return False
        
        finally:
            # 清理 Cookie 檔案
            if temp_cookie_file and os.path.exists(temp_cookie_file):
                try: os.remove(temp_cookie_file)
                except: pass

    def _clean_ansi(self, text: str) -> str:
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        return ansi_escape.sub('', text)

    def _progress_hook(self, d: Dict[str, Any]):
        if self.is_cancelled:
            raise Exception("Cancelled")

        if d['status'] == 'downloading':
            try:
                p_str = self._clean_ansi(d.get('_percent_str', '0%'))
                speed = self._clean_ansi(d.get('_speed_str', 'N/A'))
                eta = self._clean_ansi(d.get('_eta_str', 'N/A'))
                try:
                    percent = float(p_str.replace('%', ''))
                except:
                    percent = 0.0
                self.signals.progress.emit(self.task_id, p_str, percent, speed, eta)
            except ValueError:
                pass
        elif d['status'] == 'finished':
            self.signals.progress.emit(self.task_id, "100%", 100.0, "完成", "0s")

    def stop(self):
        self.is_cancelled = True