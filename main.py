# -*- coding: utf-8 -*-
# main.py
import sys
import shutil
import os
import ctypes
import logging
from PySide6.QtWidgets import QApplication, QStyleFactory
from PySide6.QtGui import QIcon, QFont
from src.ui.main_window import MainWindow

# 1. 這是 Windows Taskbar 識別的關鍵 ID
APP_ID = u'vibecoding.ytdlp.downloader.gui.v1'

# 強制清理邏輯層緩存，確保每次啟動都是最新代碼 
cache_path = os.path.join(os.path.dirname(__file__), 'src', 'logic', '__pycache__')
if os.path.exists(cache_path):
    shutil.rmtree(cache_path, ignore_errors=True)

def setup_logging():
    """
    [ARCHITECT] 初始化全域日誌系統
    策略：雙流分流 (Dual-Stream)
    1. debug.log -> 記錄所有細節 (DEBUG級別)，用於事後分析。
    2. Console   -> 只記錄重要訊息 (INFO級別)，保持 CMD 乾淨。
    """
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    
    # 1. 檔案處理器 (詳細記錄)
    # mode='w' 表示每次重啟程式都會清空舊的 log，避免檔案無限膨脹
    file_handler = logging.FileHandler('debug.log', encoding='utf-8', mode='w')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(log_format))
    
    # 2. 控制台處理器 (精簡顯示)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter('%(message)s')) # 控制台不顯示時間戳，只顯示訊息
    
    # 設定 Root Logger
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    logging.info("=== Yt-Dlp GUI 系統啟動 ===")
    logging.info(f"工作目錄: {os.getcwd()}")

def resource_path(relative_path):
    """
    [關鍵函式]
    PyInstaller 會把 --add-data 的檔案解壓到 sys._MEIPASS
    這個函式負責去那裡把檔案路徑找出來
    """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

def setup_taskbar_icon():
    if sys.platform == 'win32':
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except AttributeError:
            pass

def main():
    # 1. 先初始化日誌，捕捉啟動過程
    setup_logging()
    
    setup_taskbar_icon()
    app = QApplication(sys.argv)
    
    # 2. 全域字型設定 (與 MainWindow 保持一致)
    font = QFont("Segoe UI", 10)
    app.setFont(font)
    
    # 3. 從暫存區載入 Icon
    icon_name = "01.ico"
    icon_path = resource_path(icon_name) # 這裡對應 --add-data
    
    app_icon = None
    if os.path.exists(icon_path):
        app_icon = QIcon(icon_path)
        app.setWindowIcon(app_icon)
        logging.info(f"圖示載入成功: {icon_path}")
    else:
        logging.warning(f"警告: 找不到圖示檔案 ({icon_path})")
    
    # 4. 設定樣式
    if "WindowsVista" in QStyleFactory.keys():
        QApplication.setStyle(QStyleFactory.create("WindowsVista"))
    
    # 5. 啟動主視窗
    window = MainWindow()
    if app_icon:
        window.setWindowIcon(app_icon)
        
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()