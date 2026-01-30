# src/logic/sniffer.py
# [VibeCoding] Phase 59: UDP Minimalist (Fixing Missing Checkbox)

import logging
import os
import time
import shutil
import asyncio
import random
from typing import Optional, Tuple, Dict

# 嘗試載入 undetected_playwright
try:
    from undetected_playwright.async_api import async_playwright
    HAS_UNDETECTED = True
except ImportError:
    HAS_UNDETECTED = False
    from playwright.async_api import async_playwright

class BrowserSniffer:
    def __init__(self):
        self.logger = logging.getLogger("Sniffer")
        self.default_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

    def _find_chrome_executable(self):
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expanduser(r"~\AppData\Local\Google\Chrome\Application\chrome.exe"),
        ]
        for path in candidates:
            if os.path.exists(path): return path
        return None

    def extract_stream_url(self, target_url: str) -> Tuple[Optional[str], Dict]:
        """同步入口"""
        if not HAS_UNDETECTED:
            self.logger.warning("⚠️ 警告: 未安裝 undetected-playwright，功能可能受限")
        
        try:
            return asyncio.run(self._async_extract(target_url))
        except Exception as e:
            self.logger.error(f"非同步迴圈錯誤: {e}")
            return None, {}

    async def _async_extract(self, target_url: str) -> Tuple[Optional[str], Dict]:
        self.logger.info(f"[Sniffer] 啟動 Phase 59 (修復驗證框消失): {target_url}")
        
        found_url = None
        found_headers = {}
        
        # 建立暫存 Profile
        user_data_dir = os.path.join(os.getcwd(), "browser_data", f"udp_fix_{int(time.time())}")
        if not os.path.exists(user_data_dir): os.makedirs(user_data_dir)

        chrome_path = self._find_chrome_executable()
        
        # [核心修正] 極簡化參數，移除可能導致渲染失敗的指令
        args = [
            "--no-default-browser-check",
            "--disable-infobars",
            "--start-maximized",
            "--disable-popup-blocking",
            # "--remote-debugging-port=0" # [刪除] 這可能導致 UP 崩潰或畫面異常
        ]

        async with async_playwright() as p:
            try:
                # 啟動瀏覽器
                browser = await p.chromium.launch(
                    executable_path=chrome_path,
                    headless=False,
                    args=args,
                    # [關鍵] 讓 UP 處理自動化特徵，不要手動隱藏 enable-automation 造成衝突
                    # ignore_default_args=["--enable-automation"] 
                )
                
                # 建立 Context
                context = await browser.new_context(
                    viewport=None,
                    user_agent=self.default_ua,
                    locale="zh-TW"
                )

                page = await context.new_page()

                # 事件監聽
                candidates = []
                async def handle_request(request):
                    url = request.url
                    if '.m3u8' in url or '.mp4' in url:
                        if not url.startswith('blob:'):
                            candidates.append({
                                'url': url, 
                                'headers': await request.all_headers()
                            })
                
                page.on("request", handle_request)

                self.logger.info("🚀 前往頁面...")
                try:
                    # 增加超時時間，避免網路卡頓
                    await page.goto(target_url, wait_until="domcontentloaded", timeout=90000)
                except: pass

                self.logger.info("⏳ 等待載入 (5秒)...")
                await asyncio.sleep(5) 

                self.logger.info("👀 監聽中...")
                
                for i in range(120):
                    if found_url: break
                    
                    # 偵測是否有驗證框 (Just a moment...)
                    title = await page.title()
                    if "Just a moment" in title:
                        # 如果在驗證頁面，檢查是否有 iframe
                        iframes = page.frames
                        if len(iframes) > 1:
                            if i % 10 == 0: self.logger.info("🛡️ 仍在驗證畫面，若有打勾框請點擊...")
                        else:
                            if i % 10 == 0: self.logger.info("⚠️ 驗證畫面載入中 (若一片白請稍候)...")

                    # 檢查候選
                    while candidates:
                        item = candidates.pop(0)
                        url = item['url']
                        
                        if any(x in url for x in ['.png', '.jpg', '.css', '.js', 'favicon']): continue
                        if "777tv" in target_url and ".m3u8" not in url: continue

                        self.logger.info(f"🧐 驗證: {url[-50:]}...")
                        
                        is_valid = False
                        if ".m3u8" in url or ".mp4" in url: is_valid = True
                        
                        if is_valid:
                            self.logger.info(f"✅ 鎖定目標！")
                            found_url = url
                            
                            cookies = await context.cookies()
                            cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
                            
                            found_headers = item['headers']
                            clean_headers = {}
                            for k, v in found_headers.items():
                                if k.startswith(':'): continue
                                k_lower = k.lower()
                                if k_lower in ['user-agent', 'referer', 'origin', 'authorization']:
                                    clean_headers[k] = v
                            
                            clean_headers['Cookie'] = cookie_str
                            if 'User-Agent' not in clean_headers:
                                clean_headers['User-Agent'] = self.default_ua
                                
                            found_headers = clean_headers
                            break
                    
                    await asyncio.sleep(1)

                await browser.close()
                try: shutil.rmtree(user_data_dir, ignore_errors=True)
                except: pass

            except Exception as e:
                self.logger.error(f"Undetected 流程錯誤: {e}")
                
        return found_url, found_headers