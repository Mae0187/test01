# -*- coding: utf-8 -*-
import sys
import datetime # [NEW] 用於生成時間戳記
from pathlib import Path
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                               QLineEdit, QPushButton, QLabel, QComboBox, 
                               QProgressBar, QTextEdit, QFileDialog, QMessageBox, QApplication,
                               QInputDialog) # [NEW] 引入輸入對話框
from PySide6.QtCore import Qt, Slot

from config import UI_CONFIG
from src.logic.core_manager import CoreManager
from src.logic.downloader import DownloadWorker

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(UI_CONFIG["APP_NAME"])
        self.resize(*UI_CONFIG["WINDOW_SIZE"])
        
        self.core_manager = CoreManager()
        self.setup_ui()
        self.check_environment()

    def setup_ui(self):
        """介面佈局 (保持上次的優化版)"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        # 1. 網址區
        layout.addWidget(QLabel("影片網址:"))
        url_layout = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText(UI_CONFIG.get("STRINGS", {}).get("HINT_URL", "請輸入網址..."))
        self.url_input.setMinimumHeight(35)
        
        self.paste_btn = QPushButton("貼上網址")
        self.paste_btn.setMinimumHeight(35)
        self.paste_btn.setFixedWidth(80)
        self.paste_btn.clicked.connect(self.handle_paste)
        
        url_layout.addWidget(self.url_input)
        url_layout.addWidget(self.paste_btn)
        layout.addLayout(url_layout)

        # 2. 設定區
        settings_layout = QHBoxLayout()
        self.format_combo = QComboBox()
        self.format_combo.addItems(UI_CONFIG.get("FORMATS", ["MP4", "MP3"]))
        self.format_combo.setMinimumHeight(30)
        settings_layout.addWidget(QLabel("格式:"))
        settings_layout.addWidget(self.format_combo, stretch=1)
        
        self.path_input = QLineEdit()
        default_path = UI_CONFIG.get("SAVE_PATH", str(Path.home() / "Desktop"))
        self.path_input.setText(default_path)
        self.path_input.setReadOnly(True)
        self.path_input.setMinimumHeight(30)
        
        self.path_btn = QPushButton("瀏覽...")
        self.path_btn.setMinimumHeight(30)
        self.path_btn.clicked.connect(self.handle_path_selection)
        
        settings_layout.addWidget(QLabel("儲存:"))
        settings_layout.addWidget(self.path_input, stretch=2)
        settings_layout.addWidget(self.path_btn)
        layout.addLayout(settings_layout)

        # 3. 動作區
        action_layout = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setMinimumHeight(35)
        action_layout.addWidget(self.progress_bar, stretch=1)
        
        self.download_btn = QPushButton("開始下載")
        self.download_btn.setMinimumHeight(35)
        self.download_btn.setFixedWidth(100)
        self.download_btn.setStyleSheet("font-weight: bold;")
        self.download_btn.clicked.connect(self.handle_download_start)
        action_layout.addWidget(self.download_btn)
        
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setMinimumHeight(35)
        self.stop_btn.setFixedWidth(80)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.handle_stop)
        action_layout.addWidget(self.stop_btn)
        layout.addLayout(action_layout)

        # 4. 日誌區
        layout.addWidget(QLabel("執行日誌:"))
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text)
        
        self.status_label = QLabel("系統就緒")
        self.statusBar().addWidget(self.status_label)

    def check_environment(self):
        valid, version = self.core_manager.get_core_status()
        if valid:
            self.append_log(f"[SYSTEM] 核心檢測通過: {version}")
        else:
            self.append_log(f"[ERROR] 找不到核心，請確認 bin 資料夾")
            self.download_btn.setEnabled(False)

    def handle_paste(self):
        clipboard = QApplication.clipboard()
        text = clipboard.text()
        if text:
            self.url_input.setText(text)
            self.status_label.setText("已貼上網址")

    def handle_path_selection(self):
        folder = QFileDialog.getExistingDirectory(self, "選擇儲存位置", self.path_input.text())
        if folder:
            self.path_input.setText(folder)

    def handle_download_start(self):
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "警告", "網址不能為空")
            return

        self.set_ui_downloading(True)
        self.progress_bar.setValue(0)
        self.log_text.clear()
        
        # 初始下載：不指定檔名，讓核心先嘗試抓取
        self.start_worker(url, custom_name=None)

    def start_worker(self, url, custom_name=None):
        """啟動 Worker 的統一入口"""
        self.worker = DownloadWorker(
            url=url,
            path=self.path_input.text(),
            format_mode=self.format_combo.currentIndex(),
            yt_dlp_path=self.core_manager.exe_path,
            custom_name=custom_name # [NEW] 傳遞檔名
        )
        self.worker.progress.connect(self.update_progress)
        self.worker.log.connect(self.append_log)
        self.worker.finished.connect(self.handle_finished)
        # [NEW] 連接嗅探成功訊號
        self.worker.sniff_found.connect(self.handle_sniff_found)
        self.worker.start()

    # [NEW] 處理嗅探成功 -> 彈出重新命名視窗
    @Slot(str)
    def handle_sniff_found(self, real_url):
        self.append_log("[UI] 偵測到串流連結，等待使用者命名...")
        
        # 產生預設檔名: Video_20231027_1530
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"Video_{timestamp}"

        # 彈出視窗
        new_name, ok = QInputDialog.getText(
            self, 
            "重新命名檔案", 
            "嗅探成功！請為檔案命名 (無需副檔名)：", 
            QLineEdit.Normal, 
            default_name
        )
        
        if ok and new_name:
            self.append_log(f"[UI] 使用者設定檔名: {new_name}")
            # 使用新檔名重新啟動 Worker
            self.start_worker(real_url, custom_name=new_name)
        else:
            self.append_log("[UI] 使用者取消命名，使用預設檔名續傳...")
            self.start_worker(real_url, custom_name=None)

    def handle_stop(self):
        if hasattr(self, 'worker') and self.worker.isRunning():
            self.worker.stop()
            self.append_log("[INFO] 正在停止...")
            self.stop_btn.setEnabled(False)

    def set_ui_downloading(self, downloading: bool):
        self.download_btn.setEnabled(not downloading)
        self.stop_btn.setEnabled(downloading)
        self.url_input.setEnabled(not downloading)
        self.paste_btn.setEnabled(not downloading)
        self.path_btn.setEnabled(not downloading)
        self.format_combo.setEnabled(not downloading)

    @Slot(str)
    def update_progress(self, val):
        try:
            p = int(float(val.replace("%", "")))
            self.progress_bar.setValue(p)
        except:
            pass

    @Slot(str)
    def append_log(self, text):
        self.log_text.append(text)
        self.log_text.verticalScrollBar().setValue(self.log_text.verticalScrollBar().maximum())

    @Slot(bool, str)
    def handle_finished(self, success, msg):
        self.set_ui_downloading(False)
        if success:
            QMessageBox.information(self, "完成", msg)
            self.status_label.setText("下載成功")
        else:
            QMessageBox.critical(self, "失敗", msg)
            self.status_label.setText("下載失敗")