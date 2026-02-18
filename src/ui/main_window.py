# src/ui/main_window.py
import sys
import uuid
import os
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QLineEdit, QPushButton, QLabel, QComboBox, 
    QProgressBar, QFileDialog, QMessageBox, QApplication,
    QTableWidget, QTableWidgetItem, QHeaderView, QMenu, QAbstractItemView, QFrame
)
from PySide6.QtCore import Qt, Slot, QSize
from PySide6.QtGui import QAction, QFont, QIcon, QFontMetrics

# 載入配置與新版核心
from config import UI_CONFIG
from src.logic.queue_manager import QueueManager

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        
        # [FIX] 確保 FFmpeg 能被找到 (解決轉檔失敗問題)
        # 將專案下的 bin 資料夾加入臨時環境變數 PATH
        base_dir = os.getcwd()
        bin_dir = os.path.join(base_dir, "bin")
        if os.path.exists(bin_dir) and bin_dir not in os.environ["PATH"]:
            os.environ["PATH"] += os.pathsep + bin_dir
            print(f"System PATH updated: Added {bin_dir}")

        # 使用新版 Config 的 key (小寫)
        app_name = UI_CONFIG.get("app_name", "Yt-Dlp GUI")
        self.setWindowTitle(f"{app_name} [Batch Mode]")
        self.resize(*UI_CONFIG.get("window_size", (780, 650)))
        
        # [CORE] 初始化 QueueManager
        self.queue_manager = QueueManager(max_concurrent=UI_CONFIG.get("max_concurrent", 3))
        self._connect_backend_signals()
        
        self.setup_ui()
        
        # 狀態列初始化
        self.status_label = QLabel("系統就緒 - FFmpeg 支援已啟用")
        self.status_label.setStyleSheet("font-size: 12px; color: gray;")
        self.statusBar().addWidget(self.status_label)
        
        self.task_counter = 0

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # [STYLE] 您指定的經典樣式
        ADJUSTED_STYLE = """
            QLineEdit {
                height: 45px;
                font-size: 14px;
                padding: 4px 10px;
                border: 1px solid #CCC;
                border-radius: 4px;
            }
            QComboBox {
                height: 45px;
                font-size: 14px;
                padding: 4px 10px;
                border: 1px solid #CCC;
                border-radius: 4px;
            }
            QPushButton {
                height: 45px;
                font-size: 15px;
                font-weight: bold;
                border-radius: 4px;
                padding: 0 15px;
            }
            QLabel {
                font-size: 15px;
                font-weight: bold;
                color: #333;
            }
            /* [FIX] 進度條樣式 */
            QProgressBar {
                border: 1px solid #BBB;
                border-radius: 4px;
                text-align: center;
                color: black;
                font-weight: bold;
                background-color: #EEE;
                font-size: 15px;
            }
            QProgressBar::chunk {
                background-color: #28a745;
                border-radius: 3px;
            }
        """
        
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(18)
        main_layout.setContentsMargins(25, 25, 25, 25)

        # ==========================================
        # [輸入與設定區]
        # ==========================================
        top_container = QFrame()
        top_container.setStyleSheet(ADJUSTED_STYLE)
        
        top_layout = QVBoxLayout(top_container)
        top_layout.setSpacing(15)
        top_layout.setContentsMargins(0, 0, 0, 0)

        # --- 第一列：網址 ---
        row1 = QHBoxLayout()
        lbl_url = QLabel("網址:")
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("請輸入影片連結 (Ctrl+V)...")
        self.url_input.returnPressed.connect(self.add_task_to_ui) # Enter 觸發加入
        
        row1.addWidget(lbl_url)
        row1.addWidget(self.url_input)
        top_layout.addLayout(row1)

        # --- 第二列：檔名 + 加入按鈕 ---
        row2 = QHBoxLayout()
        lbl_name = QLabel("檔名:")
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("自訂檔名 (選填)")
        
        self.add_btn = QPushButton("加入排程 (+)")
        self.add_btn.setStyleSheet("background-color: #0078D4; color: white; border: none;")
        self.add_btn.setFixedWidth(160)
        self.add_btn.setCursor(Qt.PointingHandCursor)
        self.add_btn.clicked.connect(self.add_task_to_ui)
        
        row2.addWidget(lbl_name)
        row2.addWidget(self.name_input)
        row2.addWidget(self.add_btn)
        top_layout.addLayout(row2)

        # --- 第三列：全域設定 ---
        row3 = QHBoxLayout()
        
        lbl_fmt = QLabel("統一格式:")
        self.format_combo = QComboBox()
        # 兼容新舊 Config Key
        formats = UI_CONFIG.get("formats", UI_CONFIG.get("FORMATS", ["MP4", "MP3"]))
        self.format_combo.addItems(formats)
        self.format_combo.setFixedWidth(220)
        
        lbl_path = QLabel("儲存位置:")
        self.path_input = QLineEdit()
        # 兼容新舊 Config Key
        default_path = UI_CONFIG.get("default_download_path", UI_CONFIG.get("SAVE_PATH", str(Path.home() / "Desktop")))
        self.path_input.setText(default_path)
        self.path_input.setReadOnly(True)
        self.path_input.setStyleSheet("background-color: #f9f9f9; color: #555;")
        
        self.path_btn = QPushButton("瀏覽...")
        self.path_btn.clicked.connect(self.handle_path_selection)
        
        row3.addWidget(lbl_fmt)
        row3.addWidget(self.format_combo)
        row3.addSpacing(15)
        row3.addWidget(lbl_path)
        row3.addWidget(self.path_input)
        row3.addWidget(self.path_btn)
        top_layout.addLayout(row3)
        
        main_layout.addWidget(top_container)

        # ==========================================
        # [表格區域]
        # ==========================================
        self.table = QTableWidget()
        self.table.setColumnCount(3) 
        self.table.setHorizontalHeaderLabels(["ID", "任務內容 (檔名/網址)", "進度"])
        
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers) # 禁止編輯
        
        self.table.setStyleSheet("""
            QHeaderView::section {
                background-color: #E0E0E0;
                padding: 4px;
                border: 1px solid #CCC;
                font-size: 14px;
                font-weight: bold;
                height: 35px;
            }
            QTableWidget {
                font-size: 14px;
                selection-background-color: #CCE8FF;
                selection-color: black;
                border: 1px solid #CCC;
            }
        """)
        
        header = self.table.horizontalHeader()
        
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 50)
        
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        self.table.setColumnWidth(2, 220) 
        
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)
        
        main_layout.addWidget(self.table)

        # ==========================================
        # [底部區域]
        # ==========================================
        control_layout = QHBoxLayout()
        
        self.btn_start_all = QPushButton("開始批量下載 ▶")
        self.btn_start_all.setMinimumHeight(55)
        self.btn_start_all.setCursor(Qt.PointingHandCursor)
        self.btn_start_all.setStyleSheet("font-size: 16px; font-weight: bold; background-color: #28a745; color: white;")
        self.btn_start_all.clicked.connect(self.start_batch_download)
        
        self.btn_clear = QPushButton("清除已完成")
        self.btn_clear.setMinimumHeight(55)
        self.btn_clear.setStyleSheet("font-size: 14px;")
        self.btn_clear.clicked.connect(self.clear_finished_tasks)
        
        control_layout.addWidget(self.btn_start_all, stretch=3)
        control_layout.addWidget(self.btn_clear, stretch=1)
        
        main_layout.addLayout(control_layout)

    # --- 邏輯整合區 (Logic Integration) ---

    def _connect_backend_signals(self):
        """連接 QueueManager 的訊號到 UI 更新函數"""
        qm = self.queue_manager
        qm.task_status_changed.connect(self.update_task_status_text)
        qm.task_progress_updated.connect(self.update_task_progress)
        qm.task_error_occurred.connect(self.handle_task_error)
        qm.task_completed.connect(self.handle_task_completed)
        qm.queue_finished.connect(self.on_all_tasks_finished)

    def handle_path_selection(self):
        folder = QFileDialog.getExistingDirectory(self, "選擇儲存位置", self.path_input.text())
        if folder:
            self.path_input.setText(folder)

    # [UI FIX] 改良版文字動態調整
    def set_progress_text(self, p_bar: QProgressBar, text: str):
        """
        動態調整文字大小，但不小於 11px，避免過小難讀。
        超出範圍時使用 '...' 截斷。
        """
        if not p_bar: return

        base_style_template = """
            QProgressBar {{ 
                height: 25px;
                margin: 5px; 
                border: 1px solid #BBB;
                border-radius: 4px;
                text-align: center;
                color: black;
                font-weight: bold;
                background-color: #EEE;
                font-size: {size}px;
            }}
            QProgressBar::chunk {{
                background-color: #28a745;
                border-radius: 3px;
            }}
        """

        # 取得可用寬度 (扣除左右邊距約 20px)
        available_width = p_bar.width() - 20
        if available_width <= 0: available_width = 100

        font_family = "Segoe UI"
        max_size = 16 
        min_size = 12  # [UI FIX] 提升最小字體限制
        best_size = min_size
        
        # 1. 嘗試尋找適合的大小 (優先使用較大字體)
        for size in range(max_size, min_size - 1, -1):
            font = QFont(font_family, size)
            font.setBold(True)
            fm = QFontMetrics(font)
            text_width = fm.horizontalAdvance(text)
            
            if text_width <= available_width:
                best_size = size
                break
        
        # 2. 如果縮到最小還是爆開，進行文字截斷 (...)
        final_text = text
        font = QFont(font_family, best_size)
        font.setBold(True)
        fm = QFontMetrics(font)
        
        if fm.horizontalAdvance(text) > available_width:
            final_text = fm.elidedText(text, Qt.ElideRight, available_width)

        # 3. 套用樣式與文字
        p_bar.setStyleSheet(base_style_template.format(size=best_size))
        p_bar.setFormat(final_text)

    def add_task_to_ui(self):
        url = self.url_input.text().strip()
        name = self.name_input.text().strip()
        
        if not url:
            QMessageBox.warning(self, "提示", "網址不能為空")
            return

        # [FIX] 自動補上 https:// (解決 Selenium 報錯 "invalid argument")
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url

        # [FIX] 檢查並建立下載資料夾
        save_path = self.path_input.text().strip()
        if not os.path.exists(save_path):
            try:
                os.makedirs(save_path)
                print(f"Created download directory: {save_path}")
            except Exception as e:
                QMessageBox.warning(self, "路徑錯誤", f"無法建立資料夾: {e}")
                return

        # [CORE] 生成唯一 ID
        task_id = str(uuid.uuid4())

        # 1. 新增 UI 行
        row_idx = self.table.rowCount()
        self.table.insertRow(row_idx)
        self.table.setRowHeight(row_idx, 50)

        # Col 0: ID (視覺顯示行號，但藏入 task_id)
        item_id = QTableWidgetItem(str(row_idx + 1))
        item_id.setTextAlignment(Qt.AlignCenter)
        item_id.setData(Qt.UserRole, task_id)
        self.table.setItem(row_idx, 0, item_id)
        
        # Col 1: 內容
        display_text = f"{name}\n{url}" if name else url
        item_content = QTableWidgetItem(display_text)
        item_content.setToolTip(url)
        self.table.setItem(row_idx, 1, item_content)
        
        # Col 2: 進度條 (初始設定)
        p_bar = QProgressBar()
        p_bar.setValue(0)
        p_bar.setAlignment(Qt.AlignCenter)
        self.set_progress_text(p_bar, "等待中...") 
        
        self.table.setCellWidget(row_idx, 2, p_bar)
        
        # 2. 加入後端 Queue
        config = {
            "download_path": save_path,
            "format": self.format_combo.currentText(),
            "custom_name": name
        }
        self.queue_manager.add_task(task_id, url, config)
        
        # 3. 重置輸入
        self.url_input.clear()
        self.name_input.clear()
        self.url_input.setFocus()
        self.status_label.setText(f"已加入任務: {row_idx + 1}")

    def start_batch_download(self):
        if self.table.rowCount() == 0:
            QMessageBox.warning(self, "提示", "清單是空的")
            return
            
        self.btn_start_all.setEnabled(False)
        self.btn_start_all.setText("下載執行中...")
        self.btn_start_all.setStyleSheet("background-color: #666; color: white; font-size: 16px; font-weight: bold;")
        
        self.queue_manager.start_processing()

    def show_context_menu(self, pos):
        menu = QMenu()
        
        # [NEW] 停止任務選項
        stop_action = QAction("停止任務 (保留項目)", self)
        stop_action.triggered.connect(self.stop_selected_task)
        menu.addAction(stop_action)

        # 移除任務選項
        delete_action = QAction("移除任務", self)
        delete_action.triggered.connect(self.remove_selected_task)
        menu.addAction(delete_action)
        
        menu.exec(self.table.mapToGlobal(pos))

    # [NEW] 停止任務的邏輯
    def stop_selected_task(self):
        rows = sorted(set(index.row() for index in self.table.selectedIndexes()), reverse=True)
        if not rows: return

        for row in rows:
            item = self.table.item(row, 0)
            if item:
                task_id = item.data(Qt.UserRole)
                # 呼叫後端取消，但不移除 Row
                self.queue_manager.cancel_task(task_id)
                
                # UI 即時反饋
                p_bar = self.table.cellWidget(row, 2)
                if p_bar:
                    self.set_progress_text(p_bar, "正在停止...")
        
        self.status_label.setText(f"已對 {len(rows)} 個任務發出停止信號")

    def remove_selected_task(self):
        rows = sorted(set(index.row() for index in self.table.selectedIndexes()), reverse=True)
        for row in rows:
            item = self.table.item(row, 0)
            if item:
                task_id = item.data(Qt.UserRole)
                self.queue_manager.cancel_task(task_id)
            
            self.table.removeRow(row)
            
        self.renumber_tasks()
        self.status_label.setText("已移除選取任務")

    def renumber_tasks(self):
        row_count = self.table.rowCount()
        for i in range(row_count):
            item = self.table.item(i, 0)
            if item:
                item.setText(str(i + 1))

    def clear_finished_tasks(self):
        rows_to_remove = []
        for i in range(self.table.rowCount()):
            p_bar = self.table.cellWidget(i, 2)
            if p_bar and p_bar.value() == 100:
                rows_to_remove.append(i)
        
        if not rows_to_remove:
            QMessageBox.information(self, "提示", "沒有已完成的任務")
            return

        for row in sorted(rows_to_remove, reverse=True):
            item = self.table.item(row, 0)
            if item:
                task_id = item.data(Qt.UserRole)
                self.queue_manager.cancel_task(task_id)
            self.table.removeRow(row)
            
        self.renumber_tasks()

    def find_row_by_task_id(self, task_id: str) -> Optional[int]:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.data(Qt.UserRole) == task_id:
                return row
        return None

    @Slot(str, str)
    def update_task_status_text(self, task_id, status_msg):
        row = self.find_row_by_task_id(task_id)
        if row is not None:
            p_bar = self.table.cellWidget(row, 2)
            if p_bar:
                self.set_progress_text(p_bar, status_msg)

    @Slot(str, str, float, str, str)
    def update_task_progress(self, task_id, p_str, percent, speed, eta):
        row = self.find_row_by_task_id(task_id)
        if row is not None:
            p_bar = self.table.cellWidget(row, 2)
            if p_bar:
                p_bar.setValue(int(percent))
                msg = f"{speed} / {p_str}"
                self.set_progress_text(p_bar, msg)

    @Slot(str)
    def handle_task_completed(self, task_id):
        row = self.find_row_by_task_id(task_id)
        if row is not None:
            p_bar = self.table.cellWidget(row, 2)
            if p_bar:
                p_bar.setValue(100)
                self.set_progress_text(p_bar, "下載完成")

    @Slot(str, str)
    def handle_task_error(self, task_id, error_msg):
        row = self.find_row_by_task_id(task_id)
        if row is not None:
            p_bar = self.table.cellWidget(row, 2)
            if p_bar:
                self.set_progress_text(p_bar, "錯誤: " + error_msg)

    @Slot()
    def on_all_tasks_finished(self):
        self.btn_start_all.setEnabled(True)
        self.btn_start_all.setText("開始批量下載 ▶")
        self.btn_start_all.setStyleSheet("font-size: 16px; font-weight: bold; background-color: #28a745; color: white;")
        QMessageBox.information(self, "完成", "所有任務處理完畢！")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    font = QFont("Segoe UI", 10)
    app.setFont(font)
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec())