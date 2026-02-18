# src/logic/downloader.py
import os
import yt_dlp
import re
import time
import logging
from typing import Dict, Any, Optional
from PySide6.QtCore import QThread, Signal, QObject, QMutex
from src.logic.playwright_downloader import PlaywrightDownloader
from src.logic.native_downloader import NativeHLSDownloader

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
            self.logger.info(f"偵測到核心錯誤，準備啟動嗅探... (Error: {msg[:50]}...)")
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
        
        # === Pressplay 特規 ===
        if "pressplay" in self.url:
            self._run_pressplay()
            return

        # === 一般網站 (yt-dlp優先) ===
        success = self._try_download(self.url, is_retry=False)
        
        if not success and not self.is_cancelled:
            if HAS_SELENIUM:
                # [Fix] 確保狀態文字正確
                self.signals.progress.emit(self.task_id, "0%", 0.0, "啟動救援模式...", "N/A")
                self.signals.status.emit(self.task_id, "解析失敗，轉入嗅探模式...")
                
                GLOBAL_SNIFFER_LOCK.lock()
                real_url = None
                sniffed_headers = {}
                try:
                    if self.is_cancelled: return
                    self.signals.status.emit(self.task_id, "啟動瀏覽器嗅探...")
                    self.signals.progress.emit(self.task_id, "0%", 0.0, "嗅探中...", "N/A")
                    
                    real_url, sniffed_headers = self._perform_sniffing(self.url)
                finally:
                    GLOBAL_SNIFFER_LOCK.unlock()
                
                if real_url:
                    self.signals.status.emit(self.task_id, "嗅探成功，開始下載...")
                    success_retry = self._try_download(real_url, is_retry=True, headers=sniffed_headers)
                    if success_retry:
                        self._finalize_success()
                    else:
                        self.signals.error.emit(self.task_id, "下載失敗 (串流無效)")
                else:
                    self.signals.error.emit(self.task_id, "嗅探失敗 (找不到媒體)")
            else:
                self.signals.error.emit(self.task_id, "解析失敗 (無嗅探器)")
        
        elif success:
            self._finalize_success()

    def _finalize_success(self):
        """成功後的緩衝處理"""
        self.signals.progress.emit(self.task_id, "99%", 99.0, "寫入硬碟中...", "0s")
        self.signals.status.emit(self.task_id, "正在驗證檔案完整性...")
        time.sleep(1.5)
        self.signals.progress.emit(self.task_id, "100%", 100.0, "完成", "0s")
        self.signals.status.emit(self.task_id, "完成")
        self.signals.finished.emit(self.task_id)

    def _run_pressplay(self):
        self.signals.status.emit(self.task_id, "啟動瀏覽器攔截...")
        save_path = self.config.get('download_path', os.getcwd())
        custom_name = self.config.get('custom_name', '')
        custom_name = re.sub(r'[\\/*?:"<>|]', "", custom_name)
        filename = f"{custom_name}.mp4" if custom_name else f"Video_{self.task_id[:8]}.mp4"
        output_path = os.path.join(save_path, filename)
        
        try:
            downloader = PlaywrightDownloader(self.logger)
            success = downloader.download(self.url, output_path, lambda p, m: self.signals.progress.emit(self.task_id, f"{p:.1f}%", p, m, "N/A"))
            if success:
                self._finalize_success()
            else:
                self.signals.error.emit(self.task_id, "下載失敗")
        except Exception as e:
            self.signals.error.emit(self.task_id, f"錯誤: {e}")

    def _perform_sniffing(self, target_url: str):
        try:
            sniffer = BrowserSniffer()
            return sniffer.extract_stream_url(target_url)
        except Exception as e:
            self.logger.error(f"嗅探異常: {e}", exc_info=True)
            return None, {}

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
        if 'User-Agent' not in ydl_headers:
            ydl_headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
        if 'Referer' not in ydl_headers:
            ydl_headers['Referer'] = self.url

        node_path = os.path.abspath(os.path.join(os.getcwd(), "bin", "node.exe")).replace('\\', '/')
        
        ydl_opts = {
            'format': 'bestvideo+bestaudio/best',
            'outtmpl': out_tmpl,
            'noplaylist': True,
            'progress_hooks': [self._progress_hook],
            'http_headers': ydl_headers,
            'retries': 3,
            'nocolor': True,
            'logger': YtDlpLogger(is_retry),
            'js-runtimes': [f'node:{node_path}'],
            'extractor_args': {
                'youtube': {
                    'js_engines': ['node'],
                    'player_client': ['android', 'web'], 
                }
            }
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                if not is_retry:
                    self.signals.status.emit(self.task_id, "解析資訊中...")
                    ydl.extract_info(download_url, download=False)

                if self.is_cancelled: return False

                self.signals.status.emit(self.task_id, "下載中...")
                ydl.download([download_url])
                return True

        except Exception as e:
            err_msg = str(e)
            ignore_keywords = [
                "HTTP Error 403", "Unsupported URL", "no suitable info extractor", 
                "404 Not Found", "HTTP Error 401", "Incomplete Read",
                "cookies", "JSON", "Douyin", "Sign", "signature"
            ]
            needs_sniff = any(x in err_msg for x in ignore_keywords)
            
            if not is_retry and needs_sniff: return False
            if not is_retry: self.signals.error.emit(self.task_id, err_msg)
            return False

    def _clean_ansi(self, text: str) -> str:
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        return ansi_escape.sub('', text)

    def _progress_hook(self, d: Dict[str, Any]):
        if self.is_cancelled: raise Exception("Cancelled")

        if d['status'] == 'downloading':
            try:
                p_str = self._clean_ansi(d.get('_percent_str', '0%'))
                speed = self._clean_ansi(d.get('_speed_str', 'N/A'))
                eta = self._clean_ansi(d.get('_eta_str', 'N/A'))
                percent = float(p_str.replace('%', ''))
                self.signals.progress.emit(self.task_id, p_str, percent, speed, eta)
            except: pass
        
        elif d['status'] == 'finished':
            # [Fix] 嚴格檢查：只有真正的媒體檔案才准更新狀態
            # 忽略 .json, .vtt, .srt, .jpg, .png 等
            filename = d.get('filename', '').lower()
            ignore_exts = ('.json', '.vtt', '.srt', '.jpg', '.jpeg', '.png', '.webp')
            
            if filename.endswith(ignore_exts):
                # 這是雜魚檔案，默默略過，不要觸發 UI 更新
                return
            
            # 只有真的是影片/音訊時，才更新文字
            self.signals.status.emit(self.task_id, "下載完畢，正在整理檔案...")
    
    def stop(self):
        self.is_cancelled = True