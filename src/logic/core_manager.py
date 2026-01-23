# -*- coding: utf-8 -*-
# src/logic/core_manager.py

import os
import shutil
import subprocess
from pathlib import Path
from typing import Tuple, Optional

class CoreManager:
    """
    負責 yt-dlp.exe 的版本偵測、路徑管理與更新
    """
    def __init__(self):
        # 設定 bin 目錄路徑 (相對於專案根目錄)
        self.bin_dir = Path(os.getcwd()) / "bin"
        self.exe_path = self.bin_dir / "yt-dlp.exe"
        self._ensure_bin_dir()

    def _ensure_bin_dir(self):
        """確保 bin 資料夾存在"""
        if not self.bin_dir.exists():
            self.bin_dir.mkdir(parents=True, exist_ok=True)

    def get_core_status(self) -> Tuple[bool, str]:
        """
        檢查核心是否存在與版本
        Returns: (exists: bool, message: str)
        """
        if not self.exe_path.exists():
            return False, "未偵測到核心 (請手動更新)"
        
        try:
            # 執行 yt-dlp.exe --version
            # creationflags=0x08000000 (CREATE_NO_WINDOW) 防止彈出 cmd 黑窗
            result = subprocess.run(
                [str(self.exe_path), "--version"],
                capture_output=True,
                text=True,
                check=True,
                creationflags=0x08000000 
            )
            version = result.stdout.strip()
            return True, f"Core Ver: {version}"
        except Exception as e:
            return True, f"偵測錯誤: {str(e)}"

    def update_core(self, new_file_path: str) -> Tuple[bool, str]:
        """
        執行核心熱替換 (Hot-Swap)
        1. 驗證新檔
        2. 備份/更名舊檔 (避開 Windows 鎖定)
        3. 複製新檔
        """
        new_path = Path(new_file_path)
        if not new_path.exists() or not new_path.name.endswith(".exe"):
            return False, "無效的來源檔案"

        try:
            # 如果舊檔存在，先將其更名為 .old (Windows 允許更名正在使用的檔案，但不允許刪除)
            if self.exe_path.exists():
                backup_path = self.exe_path.with_suffix(".old")
                if backup_path.exists():
                    os.remove(backup_path) # 嘗試刪除舊的備份
                
                # 將目前的 exe 更名備份
                self.exe_path.rename(backup_path)
            
            # 複製新檔進去
            shutil.copy2(new_path, self.exe_path)
            
            return True, "核心更新成功！"
        except OSError as e:
            return False, f"權限不足或檔案被鎖定: {e}"
        except Exception as e:
            return False, f"更新失敗: {e}"

    def clean_old_core(self):
        """清理殘留的 .old 檔案 (可在程式啟動時呼叫)"""
        backup_path = self.exe_path.with_suffix(".old")
        if backup_path.exists():
            try:
                os.remove(backup_path)
            except:
                pass # 刪不掉就算了，下次再刪