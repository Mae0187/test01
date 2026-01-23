# src/logic/downloader.py
import os
import yt_dlp
import re
import time
import logging
import shutil
from typing import Dict, Any, Optional
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
        if "Deprecated Feature" in msg and "cookies" in msg: return
        self.logger.warning(f"[YtDlp Warn] {msg}")

    def error(self, msg):
        if "Deprecated Feature" in msg and "cookies" in msg: return
        
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

    def _try_download(self, download_url: str, is_retry: bool, headers: Dict = None) -> bool:
        save_path = self.config.get('download_path', os.getcwd())
        custom_name = self.config.get('custom_name', '')
        
        out_tmpl = os.path.join(save_path, '%(title)s.%(ext)s')
        if custom_name:
             out_tmpl = os.path.join(save_path, f"{custom_name}.%(ext)s")
        elif is_retry:
             out_tmpl = os.path.join(save_path, f"Video_{self.task_id[:8]}.%(ext)s")

        ydl_headers = {}
        if headers: ydl_headers.update(headers)
        
        # 確保 User-Agent 存在
        if 'User-Agent' not in ydl_headers:
            ydl_headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
        
        # [修正] 移除手動 Referer 設定，完全信任 Sniffer 抓到的 Headers
        # if 'Referer' not in ydl_headers and is_retry:
        #    ydl_headers['Referer'] = self.url

        ffmpeg_loc = self._detect_ffmpeg_path()
        
        ydl_opts = {
            'format': 'bestvideo+bestaudio/best',
            'outtmpl': out_tmpl,
            'noplaylist': True,
            'progress_hooks': [self._progress_hook],
            'http_headers': ydl_headers, 
            'retries': 3,
            'nocolor': True,
            'logger': YtDlpLogger(is_retry),
            'hls_use_mpegts': True,
            'nocheckcertificate': True,
            'ignoreerrors': True,
            'concurrent_fragment_downloads': 4,
            
            # [回歸 Native] 
            # 移除 external_downloader 設定
            # 因為現在 Headers 正確了，yt-dlp 的原生下載器應該能正常運作
        }
        
        # 掛載 Cookie 檔案
        if '_USE_COOKIE_FILE' in ydl_headers:
            cookie_file = ydl_headers.pop('_USE_COOKIE_FILE')
            if os.path.exists(cookie_file):
                ydl_opts['cookiefile'] = cookie_file
                self.logger.info(f"掛載 Cookie 檔案: {cookie_file}")
                # 清除 header 中的 cookie 避免衝突
                if 'Cookie' in ydl_opts['http_headers']:
                    del ydl_opts['http_headers']['Cookie']
        
        if ffmpeg_loc:
            ydl_opts['ffmpeg_location'] = ffmpeg_loc

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
            err_msg = str(e)
            
            if not is_retry:
                self.logger.warning(f"初次下載失敗 (準備嗅探): {err_msg}")
                return False
            
            if "Deprecated" not in err_msg:
                self.logger.error(f"最終重試失敗: {err_msg}")
            
            return False

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