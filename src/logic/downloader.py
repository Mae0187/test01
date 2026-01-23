# src/logic/downloader.py
import os
import yt_dlp
import re
import time
import logging
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
    """
    自定義 Logger: 實現雙流日誌策略
    - 檔案: 記錄所有細節 (包含失敗重試)
    - CMD: 只顯示嚴重錯誤 (過濾掉自動救援過程中的假警報)
    """
    def __init__(self, is_retry: bool):
        self.is_retry = is_retry
        self.logger = logging.getLogger("YtDlp") # 取得全域 Logger

    def debug(self, msg):
        # yt-dlp 的 debug 訊息寫入檔案，但不顯示在 CMD
        if not msg.startswith('[debug] '):
            self.logger.debug(msg)

    def warning(self, msg):
        if "Deprecated Feature" in msg and "cookies" in msg: return
        self.logger.warning(f"[YtDlp Warn] {msg}")

    def error(self, msg):
        if "Deprecated Feature" in msg and "cookies" in msg: return
        
        # 1. 無論如何，先寫入 Log 檔案 (保留證據)
        self.logger.debug(f"[YtDlp Error Raw] {msg}")

        # 2. 判斷是否要在 CMD 顯示
        if not self.is_retry and ("Unsupported URL" in msg or "403 Forbidden" in msg or "HTTP Error" in msg):
            # 這是「預期中」的錯誤 (即將觸發嗅探)，CMD 保持安靜
            self.logger.info(f"偵測到 403/Unsupported，準備啟動嗅探救援... (Error: {msg[:50]}...)")
            return
        
        # 3. 真正的錯誤，印出來
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
        
        # 1. 第一次嘗試
        success = self._try_download(self.url, is_retry=False)
        
        if not success and not self.is_cancelled:
            # 2. 失敗 -> 啟動嗅探
            if HAS_SELENIUM:
                self.signals.status.emit(self.task_id, "排隊等待嗅探...")
                self.logger.info("請求嗅探鎖...")
                
                GLOBAL_SNIFFER_LOCK.lock()
                try:
                    if self.is_cancelled: return

                    self.signals.status.emit(self.task_id, "啟動深度嗅探...")
                    real_url, sniffed_headers = self._perform_sniffing(self.url)
                    
                finally:
                    GLOBAL_SNIFFER_LOCK.unlock()
                    self.logger.info("釋放嗅探鎖")
                
                if real_url:
                    self.signals.status.emit(self.task_id, "獲取成功，開始下載...")
                    self.logger.info(f"嗅探成功，目標: {real_url}")
                    
                    # 3. 重試下載
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
                        self.signals.error.emit(self.task_id, "下載失敗 (403/404)")
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
        
        has_referer = 'Referer' in ydl_headers
        if not has_referer:
            ydl_headers['Referer'] = self.url

        ydl_opts = {
            'format': 'bestvideo+bestaudio/best',
            'outtmpl': out_tmpl,
            'noplaylist': True,
            'progress_hooks': [self._progress_hook],
            'http_headers': ydl_headers,
            'retries': 3,
            'nocolor': True,
            'logger': YtDlpLogger(is_retry), # 注入我們的雙流 Logger
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
            needs_sniff = any(x in err_msg for x in ["HTTP Error 403", "Unsupported URL", "no suitable info extractor", "404 Not Found", "HTTP Error 401"])
            
            if not is_retry and needs_sniff:
                return False
            
            if not is_retry:
                self.signals.error.emit(self.task_id, err_msg)
                self.logger.error(f"下載錯誤: {err_msg}")
            else:
                if "Deprecated" not in err_msg:
                    print(f"[Core Error] Final Retry Failed: {err_msg}")
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
                percent = float(p_str.replace('%', ''))
                self.signals.progress.emit(self.task_id, p_str, percent, speed, eta)
            except ValueError:
                pass
        elif d['status'] == 'finished':
            self.signals.progress.emit(self.task_id, "100%", 100.0, "完成", "0s")

    def stop(self):
        self.is_cancelled = True