# -*- coding: utf-8 -*-
# main.py
import sys
import os
import ctypes
from PySide6.QtWidgets import QApplication, QStyleFactory
from PySide6.QtGui import QIcon
from src.ui.main_window import MainWindow

# 1. 這是 Windows Taskbar 識別的關鍵 ID
APP_ID = u'vibecoding.ytdlp.downloader.gui.v1'

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
    setup_taskbar_icon()
    app = QApplication(sys.argv)
    
    # 2. 從暫存區載入 Icon
    icon_path = resource_path("01.ico") # 這裡對應 --add-data
    
    # 3. 設定 App 與 Window 圖示
    if os.path.exists(icon_path):
        app_icon = QIcon(icon_path)
        app.setWindowIcon(app_icon)
    
    if "WindowsVista" in QStyleFactory.keys():
        QApplication.setStyle(QStyleFactory.create("WindowsVista"))
    
    window = MainWindow()
    if os.path.exists(icon_path):
        window.setWindowIcon(app_icon)
        
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()