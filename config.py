# -*- coding: utf-8 -*-
from pathlib import Path
from typing import Dict, Any
import os

BASE_DIR = Path(os.getcwd())

# [ANCHOR: FLAT_CONFIG]
# 扁平化配置，移除嵌套以簡化讀取
UI_CONFIG: Dict[str, Any] = {
    "APP_NAME": "yt-dlp 影音下載神器",
    "APP_VERSION": "v0.3.3 (Auto-Resume)",
    "WINDOW_SIZE": (680, 550), # 調整為適合經典介面的大小
    
    # 核心路徑與設定
    "YT_DLP_PATH": str(BASE_DIR / "bin" / "yt-dlp.exe"),
    "SAVE_PATH": str(Path.home() / "Desktop"),
    
    "FORMATS": [
        "最佳畫質+音質 (MP4)", 
        "純音訊 (MP3)", 
        "原始畫質 (MKV)",
        "直播/串流 (Stream Best)"
    ],
    
    "STRINGS": {
        "HINT_URL": "請輸入 YouTube / Anime1 / Twitch 網址...",
    }
}