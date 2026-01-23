# src/logic/sniffer.py
import json
import time
import logging
from typing import Optional, Tuple, Dict

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By

class BrowserSniffer:
    """
    瀏覽器自動嗅探器 (Phase 3.12: Logging Edition)
    特色: 整合 logging 系統，將詳細除錯資訊寫入 debug.log
    """
    def __init__(self):
        # 取得專屬 Logger
        self.logger = logging.getLogger("Sniffer")
        # 抑制第三方庫的雜訊
        logging.getLogger('WDM').setLevel(logging.NOTSET)
        logging.getLogger('selenium').setLevel(logging.WARNING)

    def extract_stream_url(self, target_url: str) -> Tuple[Optional[str], Dict]:
        self.logger.info(f"開始嗅探: {target_url}")
        
        options = Options()
        # options.add_argument("--headless=new") 

        base_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        options.add_argument(f"--user-agent={base_ua}")
        
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_argument("--disable-blink-features=AutomationControlled")
        
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--mute-audio")
        options.add_argument("--log-level=3")
        options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})

        driver = None
        found_url = None
        found_headers = {}

        # 啟動重試
        max_retries = 3
        for attempt in range(max_retries):
            try:
                self.logger.debug(f"啟動瀏覽器嘗試 {attempt+1}/{max_retries}")
                service = Service(ChromeDriverManager().install())
                driver = webdriver.Chrome(service=service, options=options)
                try:
                    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
                        "source": "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
                    })
                    driver.execute_cdp_cmd('Network.enable', {})
                except: pass
                driver.set_page_load_timeout(60)
                break
            except Exception as e:
                self.logger.warning(f"瀏覽器啟動失敗: {e}")
                if driver: 
                    try: driver.quit()
                    except: pass
                time.sleep(2)
                if attempt == max_retries - 1:
                    self.logger.error("放棄: 無法啟動瀏覽器")
                    return None, {}

        if not driver: return None, {}

        try:
            driver.get(target_url)
            time.sleep(5) 

            self._try_click_play(driver)
            try:
                iframes = driver.find_elements(By.TAG_NAME, "iframe")
                for frame in iframes:
                    try:
                        driver.switch_to.frame(frame)
                        self._try_click_play(driver)
                        driver.switch_to.default_content()
                    except:
                        driver.switch_to.default_content()
            except: pass

            driver.execute_script("window.scrollTo(0, 300);")
            time.sleep(8) 

            # --- Log 分析 ---
            logs = driver.get_log('performance')
            req_map = {}
            extra_map = {}
            video_candidates = []

            for entry in logs:
                try:
                    message = json.loads(entry['message'])
                    method = message.get('message', {}).get('method', '')
                    params = message.get('message', {}).get('params', {})
                    req_id = params.get('requestId')
                    if not req_id: continue

                    if method == 'Network.requestWillBeSent':
                        request = params.get('request', {})
                        url = request.get('url', '')
                        if url: req_map[req_id] = url
                            
                    elif method == 'Network.requestWillBeSentExtraInfo':
                        headers = params.get('headers', {})
                        if headers: extra_map[req_id] = headers

                    elif method == 'Network.responseReceived':
                        response = params.get('response', {})
                        mime_type = response.get('mimeType', '').lower()
                        target_mimes = ['video/', 'mpegurl', 'application/x-mpegurl']
                        if any(tm in mime_type for tm in target_mimes):
                            if req_id in req_map and not req_map[req_id].startswith('blob:'):
                                video_candidates.append(req_id)
                except: continue

            if video_candidates:
                m3u8_ids = [rid for rid in video_candidates if ".m3u8" in req_map.get(rid, "")]
                target_id = m3u8_ids[-1] if m3u8_ids else video_candidates[-1]
                
                found_url = req_map.get(target_id)
                raw_headers = extra_map.get(target_id, {})
                
                # 標準化
                for k, v in raw_headers.items():
                    if k.startswith(':'): continue
                    k_lower = k.lower()
                    if k_lower == 'cookie': found_headers['Cookie'] = v
                    elif k_lower == 'user-agent': found_headers['User-Agent'] = v
                    elif k_lower == 'referer': found_headers['Referer'] = v
                    elif k_lower == 'origin': found_headers['Origin'] = v
                    elif k_lower == 'authorization': found_headers['Authorization'] = v

                if 'User-Agent' not in found_headers:
                    found_headers['User-Agent'] = base_ua
                    
                if 'Cookie' in found_headers:
                    self.logger.info(f"Cookie 捕獲成功 (len={len(found_headers['Cookie'])})")
                else:
                    self.logger.warning("未捕獲到 Cookie，可能導致 403")

                self.logger.info(f"嗅探成功: {found_url}")
            else:
                self.logger.error("未發現影片請求")

        except Exception as e:
            self.logger.error(f"嗅探過程發生錯誤: {e}", exc_info=True)
        finally:
            if driver:
                try: driver.quit()
                except: pass

        return found_url, found_headers

    def _try_click_play(self, driver):
        try:
            selectors = [".vjs-big-play-button", ".plyr__control--overlaid", ".art-control-play", "button[aria-label='Play']", ".dplayer-mobile-play", ".prism-big-play-btn", "div[title='Play']", ".html5-main-video"]
            for css in selectors:
                elements = driver.find_elements(By.CSS_SELECTOR, css)
                for btn in elements:
                    if btn.is_displayed():
                        driver.execute_script("arguments[0].click();", btn)
                        time.sleep(0.5)
            driver.execute_script("var vids=document.querySelectorAll('video');vids.forEach(v=>{v.muted=true;v.play().catch(e=>{});});")
        except: pass