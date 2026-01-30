# -*- coding: utf-8 -*-
# src/logic/playwright_downloader.py
# [VibeCoding] Phase 75: Human-in-the-Loop (Interactive Mode)
# 策略：程式只負責開啟視窗與監聽，驗證與播放完全由使用者手動操作
# 這是解決 Cloudflare "無限迴圈" 與 "隱形挑戰" 的最終物理手段

import os
import sys
import time
import shutil
import logging
import asyncio
from typing import Callable, Optional

# 載入 Playwright
from playwright.async_api import async_playwright

# 載入 Native 下載器
from src.logic.native_downloader import NativeHLSDownloader

logger = logging.getLogger("PlaywrightDL")

class PlaywrightDownloader:
    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger("PlaywrightDL")
        self.is_cancelled = False
        
    def _find_chrome_executable(self):
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expanduser(r"~\AppData\Local\Google\Chrome\Application\chrome.exe"),
        ]
        for path in candidates:
            if os.path.exists(path): return path
        return None

    def download(self, target_url: str, output_path: str, progress_callback: Optional[Callable] = None) -> bool:
        """同步入口"""
        try:
            return asyncio.run(self._download_async(target_url, output_path, progress_callback))
        except Exception as e:
            self.logger.error(f"Playwright 流程異常: {e}", exc_info=True)
            return False

    async def _download_async(self, target_url: str, output_path: str, progress_callback) -> bool:
        self.logger.info(f"[PlaywrightDL] 啟動 Phase 75 (人機合一模式): {target_url}")
        
        found_m3u8 = None
        found_cookies = []
        found_headers = {}
        
        # 使用永久 Profile，這樣您下次就不用再驗證一次
        user_data_dir = os.path.join(os.getcwd(), "browser_data", "permanent_user")
        if not os.path.exists(user_data_dir): os.makedirs(user_data_dir)

        chrome_path = self._find_chrome_executable()
        
        # 參數：隱藏自動化特徵，讓瀏覽器看起來跟您平常開的一模一樣
        args = [
            "--disable-infobars",
            "--start-maximized",
            "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled", 
        ]

        async with async_playwright() as p:
            try:
                # 1. 啟動瀏覽器
                self.logger.info("🔓 正在開啟瀏覽器...請準備接手操作")
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    executable_path=chrome_path,
                    channel="chrome",
                    headless=False, # 必須顯示視窗
                    args=args,
                    viewport=None,
                    ignore_default_args=["--enable-automation"], # 移除黃條
                    # 模擬標準 Win10 Chrome
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                )

                page = context.pages[0] if context.pages else await context.new_page()

                # 2. 注入極簡隱身 (只隱藏 webdriver)
                await context.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")

                # 3. 設定監聽器 (這是唯一的自動化部分)
                async def handle_request(request):
                    nonlocal found_m3u8, found_headers
                    if found_m3u8: return
                    
                    url = request.url
                    # 只要抓到 M3U8 就鎖定
                    if ".m3u8" in url or "application/vnd.apple.mpegurl" in request.headers.get("content-type", ""):
                        if not url.startswith("blob:"):
                            self.logger.info(f"🎯 偵測到 M3U8: {url}")
                            found_m3u8 = url
                            found_headers = await request.all_headers()

                page.on("request", handle_request)

                # 4. 前往頁面
                self.logger.info("🚀 進入目標網頁...")
                try:
                    await page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
                except: pass

                # 5. [關鍵階段] 完全交給使用者
                self.logger.info("🛑 程式已暫停操作。")
                self.logger.info("👉 請在彈出的瀏覽器中：1.手動過驗證 2.點擊播放影片")
                self.logger.info("⏳ 等待您完成操作 (給予 10 分鐘)...")
                
                wait_start = time.time()
                while time.time() - wait_start < 600: # 10分鐘超長等待
                    if self.is_cancelled: return False
                    
                    if found_m3u8:
                        self.logger.info(f"✅ 太棒了！程式已捕獲影片連結！")
                        found_cookies = await context.cookies()
                        break
                    
                    # 這裡不做任何自動點擊，避免干擾您
                    await asyncio.sleep(1)

                await context.close()

            except Exception as e:
                self.logger.error(f"瀏覽器操作錯誤: {e}")
                return False

        # 6. 接力下載
        if found_m3u8:
            self.logger.info("🔄 啟動接力下載...")
            
            clean_headers = {}
            for k, v in found_headers.items():
                if k.lower() in ['user-agent', 'referer', 'origin', 'authorization']:
                    clean_headers[k] = v
            
            if found_cookies:
                cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in found_cookies])
                clean_headers['Cookie'] = cookie_str
            
            native = NativeHLSDownloader(self.logger)
            
            def native_progress(pct, msg):
                if progress_callback: progress_callback(pct, msg)

            ts_output = output_path.replace(".mp4", ".ts")
            success = native.download(
                m3u8_url=found_m3u8,
                headers=clean_headers,
                output_path=ts_output,
                base_url=target_url,
                progress_callback=native_progress
            )
            
            if success:
                self.logger.info("📦 下載完成，轉檔中...")
                self._convert_to_mp4(ts_output, output_path)
                return True
            else:
                self.logger.error("❌ Native 下載失敗")
                return False
        else:
            self.logger.error("❌ 您似乎沒有成功播放影片，或者超時了")
            return False

    def _convert_to_mp4(self, input_ts, output_mp4):
        import subprocess
        ffmpeg_path = os.path.join(os.getcwd(), "bin", "ffmpeg.exe")
        cmd = [ffmpeg_path, "-y", "-i", input_ts, "-c", "copy", "-bsf:a", "aac_adtstoasc", output_mp4]
        try:
            subprocess.run(cmd, creationflags=0x08000000, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if os.path.exists(input_ts): os.remove(input_ts)
        except:
            if os.path.exists(input_ts): os.rename(input_ts, output_mp4)