# config.py
import os
from pathlib import Path
from typing import Dict, Any

# 專案根目錄定位
BASE_DIR = Path(os.getcwd())

# [ANCHOR: UI_CONFIG_V2]
# 配合 Phase 3.2 QueueManager 與 MainWindow 的配置
UI_CONFIG: Dict[str, Any] = {
    # --- 應用程式基礎資訊 ---
    "app_name": "Yt-Dlp GUI Downloader (Batch Edition)",
    "version": "v1.2.0 (Queue Manager)",
    "window_size": (780, 650),  # 調整為適合批量列表的寬度
    "icon_path": "icon.ico",    # 介面圖示 (Phase 3.3 打包關鍵)
    "theme_color": "#28a745",   # 進度條與按鈕的主題色 (成功綠)

    # --- 核心路徑與環境 ---
    # 優先指引到 bin/yt-dlp.exe，若不存在可於 Logic 層 fallback 到系統 PATH
    "yt_dlp_path": str(BASE_DIR / "bin" / "yt-dlp.exe"),
    
    # 預設下載路徑：使用使用者 User Profile 的 Desktop
    "default_download_path": os.path.join(os.environ['USERPROFILE'], 'Desktop'),

    # --- 下載邏輯參數 ---
    "max_concurrent": 3,  # 同時下載的最大任務數 (Queue Manager 使用)
    
    # --- 預留格式選項 (未來可擴充下拉選單) ---
    "formats": [
        "最佳畫質+音質 (MP4)", 
        "純音訊 (MP3)", 
        "原始畫質 (MKV)"
    ],
    
    # --- 提示文字 ---
    "strings": {
        "hint_url": "請輸入 YouTube / Twitch / 影音連結...",
        "status_waiting": "等待中",
        "status_downloading": "下載中",
        "status_done": "完成",
        "status_error": "錯誤"
    }
}