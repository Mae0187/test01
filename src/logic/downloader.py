# -*- coding: utf-8 -*-
# src/logic/downloader.py
# [VibeCoding] Phase 78: Final Signal Fix (Version Check)
# 修正重點：確保 WorkerSignals 定義包含 task_id，解決 RuntimeError

import os
import logging
import yt_dlp
from PySide6.QtCore import QObject, Signal, QRunnable, Slot

# 嘗試匯入 Playwright 下載器 (備援用)
try:
    from src.logic.playwright_downloader import PlaywrightDownloader
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

# 定義訊號類別 (關鍵修正：所有訊號第一參數皆為 str 類型的 task_id)
class WorkerSignals(QObject):
    # progress: (task_id, percentage, message)
    progress = Signal(str, int, str)
    
    # finished: (task_id, success, message_or_path)
    finished = Signal(str, bool, str)
    
    # error: (task_id, error_message)
    error = Signal(str, str)
    
    # cancelled: (task_id)
    cancelled = Signal(str)

# 定義下載工人
class DownloadWorker(QRunnable):
    def __init__(self, task_id, url, config):
        super().__init__()
        self.task_id = task_id
        self.url = url
        self.config = config
        self.signals = WorkerSignals()
        self.is_cancelled = False
        
        # 設定獨立 Logger
        self.logger = logging.getLogger(f"Worker-{task_id[-4:]}")
        self.logger.info(f"[DownloadWorker] 載入 Phase 78 修復版 (Signal Fix) - ID: {task_id}")
        
        # 決定輸出路徑
        self.download_dir = config.get('download_dir', os.getcwd())
        self.output_template = os.path.join(self.download_dir, '%(title)s.%(ext)s')

    @Slot()
    def run(self):
        """執行緒啟動點"""
        self.logger.info(f"任務啟動: {self.url}")
        
        try:
            # 1. 優先嘗試 yt-dlp (強制 MP4)
            if self._try_ytdlp():
                return
            
            # 2. 備援：Playwright 嗅探
            if HAS_PLAYWRIGHT:
                self.logger.info("轉用 Playwright 嗅探模式...")
                # 修正：帶上 task_id
                self.signals.progress.emit(self.task_id, 0, "正在啟動嗅探器...")
                
                temp_output = os.path.join(self.download_dir, f"download_{self.task_id}.mp4")
                sniffer = PlaywrightDownloader(self.logger)
                
                def sniffer_progress(pct, msg):
                    if self.is_cancelled: return
                    # 修正：帶上 task_id
                    self.signals.progress.emit(self.task_id, int(pct), msg)

                success = sniffer.download(self.url, temp_output, sniffer_progress)
                
                if success:
                    self.signals.finished.emit(self.task_id, True, temp_output)
                else:
                    self.signals.error.emit(self.task_id, "所有下載方式皆失敗")
            else:
                self.signals.error.emit(self.task_id, "yt-dlp 失敗，且未安裝 Playwright")

        except Exception as e:
            if "下載已取消" in str(e):
                self.signals.cancelled.emit(self.task_id)
            else:
                self.logger.error(f"未預期的錯誤: {e}", exc_info=True)
                self.signals.error.emit(self.task_id, str(e))

    def cancel(self):
        """外部呼叫取消"""
        self.is_cancelled = True

    def _try_ytdlp(self) -> bool:
        """使用 yt-dlp 核心下載"""
        
        def ydl_progress_hook(d):
            if self.is_cancelled:
                raise Exception("下載已取消")
            
            if d['status'] == 'downloading':
                try:
                    p = d.get('_percent_str', '0%').replace('%', '')
                    pct = float(p)
                    speed = d.get('_speed_str', 'N/A')
                    eta = d.get('_eta_str', 'N/A')
                    msg = f"下載中: {pct:.1f}% (速度: {speed}, 剩餘: {eta})"
                    # 修正：帶上 task_id
                    self.signals.progress.emit(self.task_id, int(pct), msg)
                except:
                    pass
            elif d['status'] == 'finished':
                # 修正：帶上 task_id
                self.signals.progress.emit(self.task_id, 99, "正在轉檔與合併...")

        ydl_opts = {
            'outtmpl': self.output_template,
            'format': 'bestvideo+bestaudio/best',
            
            # --- 強制 MP4 ---
            'merge_output_format': 'mp4',
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }],
            # ---------------
            
            'progress_hooks': [ydl_progress_hook],
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'ffmpeg_location': os.getcwd(),
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                self.logger.info(f"[yt-dlp] 開始下載: {self.url}")
                info = ydl.extract_info(self.url, download=True)
                filename = ydl.prepare_filename(info)
                
                # 修正副檔名
                final_filename = os.path.splitext(filename)[0] + ".mp4"
                
                # 修正：帶上 task_id
                self.signals.finished.emit(self.task_id, True, final_filename)
                return True

        except Exception as e:
            if "下載已取消" in str(e):
                raise e
            
            self.logger.warning(f"yt-dlp 下載失敗: {e}")
            return False