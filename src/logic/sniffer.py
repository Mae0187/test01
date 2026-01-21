# src/logic/sniffer.py
import json
import time
import logging
from typing import Optional
from urllib.parse import urlparse

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

class BrowserSniffer:
    """
    瀏覽器自動嗅探器
    用途: 啟動無頭瀏覽器，攔截網頁載入過程中的 m3u8/mp4 請求
    """
    def __init__(self):
        self.logger = logging.getLogger("BrowserSniffer")

    def extract_stream_url(self, target_url: str) -> Optional[str]:
        """
        開啟網址並嘗試提取真實串流連結
        :param target_url: 目標網頁網址
        :return: 找到的 .m3u8 或 .mp4 網址，若無則回傳 None
        """
        options = Options()
        options.add_argument("--headless")  # 無頭模式 (不顯示視窗)
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--mute-audio") # 靜音
        options.add_argument("--log-level=3") # 減少雜訊輸出
        
        # 關鍵：開啟效能日誌 (Performance Logging) 以便監聽 Network
        options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})

        driver = None
        found_url = None

        try:
            # 自動下載並設定 ChromeDriver
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
            
            # 設定頁面載入逾時
            driver.set_page_load_timeout(30)
            
            self.logger.info(f"Sniffing target: {target_url}")
            driver.get(target_url)

            # 等待一段時間讓影片載入 (預設 5-8 秒)
            # 有些網站需要 JS 跑一陣子才會發出 m3u8 請求
            time.sleep(6) 

            # 抓取瀏覽器效能日誌 (包含 Network 請求)
            logs = driver.get_log('performance')
            
            for entry in logs:
                try:
                    message = json.loads(entry['message'])
                    message_params = message.get('message', {}).get('params', {})
                    
                    # 檢查 request 或是 response 的 URL
                    request_url = ""
                    if 'request' in message_params:
                        request_url = message_params['request'].get('url', "")
                    elif 'response' in message_params:
                        request_url = message_params['response'].get('url', "")

                    if not request_url:
                        continue

                    # 過濾規則：找 .m3u8 或 .mp4
                    # 排除 blob: 開頭的 (通常無法直接下載)
                    if ".m3u8" in request_url and not request_url.startswith("blob:"):
                        found_url = request_url
                        break # 找到第一個就收工 (通常是主播放清單)
                    
                    # 備用：有些是用 mp4 直連
                    if ".mp4" in request_url and not request_url.startswith("blob:") and found_url is None:
                        found_url = request_url
                        
                except Exception:
                    continue
                    
        except Exception as e:
            self.logger.error(f"Sniffing failed: {e}")
        finally:
            if driver:
                driver.quit()

        return found_url