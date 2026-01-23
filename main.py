"""
Yt-Dlp GUI Downloader - Smart Sniffer Edition
支援 Bahamut 和 Pressplay 自動嗅探下載
"""

import sys
import os
from pathlib import Path
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QProgressBar,
    QComboBox, QFileDialog, QMessageBox, QGroupBox, QGridLayout
)
from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QIcon

# 導入核心管理器
from src.logic.core_manager import CoreManager


class MainWindow(QMainWindow):
    """主視窗"""
    
    def __init__(self):
        super().__init__()
        self.core_manager = CoreManager()
        self.init_ui()
        self.connect_signals()
    
    def init_ui(self):
        """初始化 UI"""
        self.setWindowTitle("Yt-Dlp GUI Downloader - Smart Sniffer Edition")
        self.setMinimumSize(900, 700)
        
        # 建立中央 Widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # 主佈局
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(15)
        main_layout.setContentsMargins(20, 20, 20, 20)
        
        # ===== 平台選擇區 =====
        platform_group = QGroupBox("1. 選擇平台")
        platform_layout = QHBoxLayout()
        
        self.platform_combo = QComboBox()
        self.platform_combo.addItems(["Bahamut (動畫瘋)", "Pressplay (訂閱平台)"])
        self.platform_combo.setMinimumHeight(35)
        
        platform_layout.addWidget(QLabel("平台:"))
        platform_layout.addWidget(self.platform_combo)
        platform_layout.addStretch()
        
        platform_group.setLayout(platform_layout)
        main_layout.addWidget(platform_group)
        
        # ===== 影片 URL 輸入區 =====
        url_group = QGroupBox("2. 輸入影片網址")
        url_layout = QVBoxLayout()
        
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("貼上影片頁面網址 (例如: https://ani.gamer.com.tw/animeVideo.php?sn=xxxxx)")
        self.url_input.setMinimumHeight(35)
        
        url_layout.addWidget(self.url_input)
        url_group.setLayout(url_layout)
        main_layout.addWidget(url_group)
        
        # ===== 登入資訊區 (選填) =====
        login_group = QGroupBox("3. 登入資訊 (選填 - Pressplay 需要)")
        login_layout = QGridLayout()
        
        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("使用者名稱 / Email")
        self.username_input.setMinimumHeight(30)
        
        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("密碼")
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setMinimumHeight(30)
        
        login_layout.addWidget(QLabel("帳號:"), 0, 0)
        login_layout.addWidget(self.username_input, 0, 1)
        login_layout.addWidget(QLabel("密碼:"), 1, 0)
        login_layout.addWidget(self.password_input, 1, 1)
        
        login_group.setLayout(login_layout)
        main_layout.addWidget(login_group)
        
        # ===== 輸出路徑區 =====
        output_group = QGroupBox("4. 輸出路徑")
        output_layout = QHBoxLayout()
        
        self.output_path_input = QLineEdit()
        self.output_path_input.setPlaceholderText("選擇儲存位置...")
        self.output_path_input.setMinimumHeight(35)
        self.output_path_input.setText(str(Path.home() / "Downloads" / "video.mp4"))
        
        self.browse_btn = QPushButton("瀏覽...")
        self.browse_btn.setMinimumHeight(35)
        self.browse_btn.setMinimumWidth(100)
        self.browse_btn.clicked.connect(self.browse_output_path)
        
        output_layout.addWidget(self.output_path_input)
        output_layout.addWidget(self.browse_btn)
        
        output_group.setLayout(output_layout)
        main_layout.addWidget(output_group)
        
        # ===== 控制按鈕區 =====
        button_layout = QHBoxLayout()
        
        self.sniff_btn = QPushButton("🔍 開始嗅探影片")
        self.sniff_btn.setMinimumHeight(45)
        self.sniff_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-size: 14px;
                font-weight: bold;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:disabled {
                background-color: #cccccc;
            }
        """)
        self.sniff_btn.clicked.connect(self.start_sniff)
        
        self.download_btn = QPushButton("⬇️ 開始下載")
        self.download_btn.setMinimumHeight(45)
        self.download_btn.setEnabled(False)
        self.download_btn.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                font-size: 14px;
                font-weight: bold;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #0b7dda;
            }
            QPushButton:disabled {
                background-color: #cccccc;
            }
        """)
        self.download_btn.clicked.connect(self.start_download)
        
        self.cancel_btn = QPushButton("⏹️ 取消下載")
        self.cancel_btn.setMinimumHeight(45)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #f44336;
                color: white;
                font-size: 14px;
                font-weight: bold;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #da190b;
            }
            QPushButton:disabled {
                background-color: #cccccc;
            }
        """)
        self.cancel_btn.clicked.connect(self.cancel_download)
        
        button_layout.addWidget(self.sniff_btn)
        button_layout.addWidget(self.download_btn)
        button_layout.addWidget(self.cancel_btn)
        
        main_layout.addLayout(button_layout)
        
        # ===== 進度條 =====
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimumHeight(30)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("等待中... (%p%)")
        main_layout.addWidget(self.progress_bar)
        
        # ===== 日誌區 =====
        log_group = QGroupBox("執行日誌")
        log_layout = QVBoxLayout()
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(200)
        self.log_text.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                color: #d4d4d4;
                font-family: 'Consolas', 'Monaco', monospace;
                font-size: 11px;
            }
        """)
        
        log_layout.addWidget(self.log_text)
        log_group.setLayout(log_layout)
        main_layout.addWidget(log_group)
        
        # 初始日誌
        self.append_log("=== Yt-Dlp GUI Downloader 已啟動 ===")
        self.append_log("支援平台: Bahamut 動畫瘋、Pressplay 訂閱平台")
        self.append_log("請輸入影片網址並點擊「開始嗅探」\n")
    
    def connect_signals(self):
        """連接訊號"""
        self.core_manager.log_signal.connect(self.append_log)
        self.core_manager.sniff_finished_signal.connect(self.on_sniff_finished)
        self.core_manager.download_progress_signal.connect(self.on_download_progress)
        self.core_manager.download_finished_signal.connect(self.on_download_finished)
    
    # ===== Slots =====
    
    def browse_output_path(self):
        """選擇輸出路徑"""
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "選擇儲存位置",
            str(Path.home() / "Downloads" / "video.mp4"),
            "影片檔案 (*.mp4 *.mkv *.avi);;所有檔案 (*.*)"
        )
        if file_path:
            self.output_path_input.setText(file_path)
    
    def start_sniff(self):
        """開始嗅探"""
        # 取得輸入
        platform_text = self.platform_combo.currentText()
        video_url = self.url_input.text().strip()
        
        # 驗證輸入
        if not video_url:
            QMessageBox.warning(self, "錯誤", "請輸入影片網址！")
            return
        
        # 判斷平台
        platform = 'bahamut' if 'Bahamut' in platform_text else 'pressplay'
        
        # 取得登入資訊 (如果有)
        credentials = None
        username = self.username_input.text().strip()
        password = self.password_input.text().strip()
        if username and password:
            credentials = {'username': username, 'password': password}
        
        # 重置狀態
        self.core_manager.reset()
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("嗅探中...")
        
        # 禁用按鈕
        self.sniff_btn.setEnabled(False)
        self.download_btn.setEnabled(False)
        
        # 開始嗅探
        self.append_log("\n" + "="*50)
        self.append_log("開始嗅探影片資訊...")
        self.core_manager.start_sniff(platform, video_url, credentials)
    
    @Slot(bool, str)
    def on_sniff_finished(self, success: bool, message: str):
        """嗅探完成回調"""
        self.sniff_btn.setEnabled(True)
        
        if success:
            self.append_log(f"\n✅ {message}")
            self.download_btn.setEnabled(True)
            self.progress_bar.setFormat("嗅探完成！可以開始下載")
            
            QMessageBox.information(self, "成功", "影片資訊已取得！\n點擊「開始下載」繼續。")
        else:
            self.append_log(f"\n❌ {message}")
            self.progress_bar.setFormat("嗅探失敗")
            
            QMessageBox.critical(self, "失敗", f"嗅探失敗:\n{message}")
    
    def start_download(self):
        """開始下載"""
        output_path = self.output_path_input.text().strip()
        
        if not output_path:
            QMessageBox.warning(self, "錯誤", "請選擇輸出路徑！")
            return
        
        # 禁用按鈕
        self.download_btn.setEnabled(False)
        self.sniff_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        
        # 開始下載
        self.append_log("\n" + "="*50)
        self.append_log("開始下載影片...")
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("下載中... (0%)")
        
        self.core_manager.start_download(output_path)
    
    @Slot(int, int, str)
    def on_download_progress(self, current: int, total: int, status: str):
        """下載進度回調"""
        if total > 0:
            percentage = int((current / total) * 100)
            self.progress_bar.setValue(percentage)
            self.progress_bar.setFormat(f"{status} ({percentage}%)")
    
    @Slot(bool, str)
    def on_download_finished(self, success: bool, message: str):
        """下載完成回調"""
        # 恢復按鈕
        self.sniff_btn.setEnabled(True)
        self.download_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        
        if success:
            self.append_log(f"\n✅ {message}")
            self.progress_bar.setValue(100)
            self.progress_bar.setFormat("下載完成！(100%)")
            
            # 詢問是否開啟檔案
            reply = QMessageBox.question(
                self,
                "完成",
                f"下載完成！\n\n檔案位置: {self.output_path_input.text()}\n\n是否開啟資料夾？",
                QMessageBox.Yes | QMessageBox.No
            )
            
            if reply == QMessageBox.Yes:
                self.open_output_folder()
        else:
            self.append_log(f"\n❌ {message}")
            self.progress_bar.setFormat("下載失敗")
            
            QMessageBox.critical(self, "失敗", f"下載失敗:\n{message}")
    
    def cancel_download(self):
        """取消下載"""
        reply = QMessageBox.question(
            self,
            "確認",
            "確定要取消下載嗎？",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            self.core_manager.cancel_download()
            self.append_log("\n⚠️ 使用者取消下載")
            self.cancel_btn.setEnabled(False)
            self.sniff_btn.setEnabled(True)
    
    def open_output_folder(self):
        """開啟輸出資料夾"""
        output_path = self.output_path_input.text()
        if os.path.exists(output_path):
            folder_path = os.path.dirname(output_path)
            os.startfile(folder_path)  # Windows
    
    def append_log(self, message: str):
        """添加日誌"""
        self.log_text.append(message)
        # 自動滾動到底部
        cursor = self.log_text.textCursor()
        cursor.movePosition(cursor.End)
        self.log_text.setTextCursor(cursor)


def main():
    """主程式入口"""
    app = QApplication(sys.argv)
    
    # 設定應用程式資訊
    app.setApplicationName("Yt-Dlp GUI Downloader")
    app.setOrganizationName("Smart Sniffer")
    
    # 建立並顯示主視窗
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()