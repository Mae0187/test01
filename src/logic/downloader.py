# -*- coding: utf-8 -*-
import sys
import os
import subprocess
from typing import List, Optional
from PySide6.QtCore import QThread, Signal
from config import UI_CONFIG

class DownloadWorker(QThread):
    progress = Signal(str)
    log = Signal(str)
    finished = Signal(bool, str)
    # [NEW] 新增訊號：通知 UI 嗅探成功，並傳回真實網址
    sniff_found = Signal(str)

    def __init__(self, url: str, path: str, format_mode: int, yt_dlp_path: Optional[str] = None, custom_name: Optional[str] = None):
        super().__init__()
        self.url = url
        self.save_path = path
        self.format_mode = format_mode
        self.custom_name = custom_name  # [NEW] 接收自訂檔名
        
        self.yt_dlp_path = yt_dlp_path if yt_dlp_path else UI_CONFIG.get("YT_DLP_PATH", "bin/yt-dlp.exe")
        self._process = None
        self._is_running = True

    def build_command(self, target_url: str) -> List[str]:
        # [NEW] 檔名邏輯判斷
        if self.custom_name:
            # 如果有自訂檔名，強制使用該檔名 (保留副檔名自動偵測)
            # 使用 f"{name}.%(ext)s" 確保 yt-dlp 自動填入正確的 mp4/mkv/mp3
            output_template = f"{self.save_path}/{self.custom_name}.%(ext)s"
        else:
            # 原本的邏輯 (自動抓標題)
            output_template = f"{self.save_path}/%(title)s.%(ext)s"

        cmd = [
            str(self.yt_dlp_path),
            target_url,
            "--encoding", "utf-8", 
            "-o", output_template,
            "--no-mtime", 
            "--progress", 
            "--newline",
        ]
        
        if self.format_mode == 0:   # MP4
            cmd.extend(["-S", "vcodec:h264,res,acodec:m4a", "--merge-output-format", "mp4"])
        elif self.format_mode == 1: # MP3
            cmd.extend(["-x", "--audio-format", "mp3"])
        elif self.format_mode == 2: # MKV
            cmd.extend(["-f", "bestvideo+bestaudio/best", "--merge-output-format", "mkv"])
        elif self.format_mode == 3: # Stream
            cmd.extend(["--downloader", "ffmpeg"])
        
        return cmd

    def _execute_download(self, target_url: str) -> int:
        if not self._is_running: return -1
        
        cmd = self.build_command(target_url)
        # 顯示當前使用的檔名樣板，方便 Debug
        template_display = self.custom_name if self.custom_name else "%(title)s"
        self.log.emit(f"[ENGINE] 啟動核心... (Target: {template_display})")
        
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )
            self._process = process

            while True:
                if not self._is_running:
                    process.terminate()
                    break
                    
                line = process.stdout.readline()
                if line:
                    line = line.strip()
                    if "[download]" in line and "%" in line:
                        parts = line.split()
                        for part in parts:
                            if "%" in part:
                                self.progress.emit(part) 
                                break
                    else:
                        self.log.emit(line)
                
                if process.poll() is not None:
                    break

            stdout, stderr = process.communicate()
            
            if process.returncode != 0 and stderr:
                if "Unsupported URL" in stderr or "no suitable info extractor" in stderr:
                    self.log.emit("[WARNING] 需要啟動嗅探救援...")
                    return 999 
                else:
                    self.log.emit(f"[ERROR] {stderr.strip()}")
            
            return process.returncode

        except Exception as e:
            self.log.emit(f"[EXCEPTION] {str(e)}")
            return -1

    def run(self):
        # 1. 第一次嘗試 (標準下載)
        code = self._execute_download(self.url)
        
        if code == 0:
            self.progress.emit("100%")
            self.finished.emit(True, "下載完成")
            return

        # 2. 失敗 -> 啟動嗅探
        if code == 999:
            self.log.emit("-" * 30)
            self.log.emit("[AUTO-SNIFF] 啟動智慧嗅探 (Selenium)...")
            
            try:
                from src.logic.sniffer import BrowserSniffer
                sniffer = BrowserSniffer()
                real_url = sniffer.extract_stream_url(self.url)
                
                if real_url:
                    self.log.emit(f"[SUCCESS] 嗅探成功! 真實位址: {real_url}")
                    # [MODIFIED] 不再自動下載，而是發送訊號給 UI，請求重新命名
                    self.sniff_found.emit(real_url)
                    # 結束這個 Worker，等待 UI 啟動一個新的 (帶有檔名的) Worker
                else:
                    self.finished.emit(False, "嗅探失敗：找不到有效串流")
                    
            except ImportError:
                self.finished.emit(False, "缺少依賴：請安裝 Selenium")
            except Exception as e:
                self.finished.emit(False, f"嗅探錯誤: {str(e)}")
        else:
            self.finished.emit(False, "下載失敗")

    def stop(self):
        self._is_running = False
        if self._process:
            self._process.terminate()
            self.log.emit("[INFO] 已停止")