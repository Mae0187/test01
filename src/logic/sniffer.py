# src/logic/sniffer.py
import json
import time
import os
import logging
import re
from typing import Optional, Tuple, Dict
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By

class BrowserSniffer:
    """
    瀏覽器自動嗅探器 (Phase 3.47: Latin-1 Firewall)
    修正:
    1. 【編碼防火牆】強制過濾所有 Header 值，剔除任何非 Latin-1 (中文/特殊符號) 字元。
       這解決了 'UnicodeEncodeError: latin-1 codec can't encode' 的崩潰問題。
    2. 保持強大的 Cookie 捕獲與 Header 合併邏輯。
    """
    def __init__(self):
        logging.getLogger('WDM').setLevel(logging.NOTSET)
        self.logger = logging.getLogger("Sniffer")

    def extract_stream_url(self, target_url: str) -> Tuple[Optional[str], Dict]:
        self.logger.info(f"開始嗅探任務: {target_url}")
        
        is_bahamut = "ani.gamer.com.tw" in target_url
        min_exit_time = 32 if is_bahamut else 0
        
        options = Options()
        # options.add_argument("--headless=new") 

        base_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        options.add_argument(f"--user-agent={base_ua}")
        
        cwd = os.getcwd()
        profile_dir = os.path.join(cwd, "browser_profile")
        if not os.path.exists(profile_dir):
            os.makedirs(profile_dir)
        options.add_argument(f"--user-data-dir={profile_dir}")
        options.add_argument("--profile-directory=Default")
        
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

        max_retries = 3
        for attempt in range(max_retries):
            try:
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
                self.logger.warning(f"瀏覽器啟動失敗 ({attempt+1}/{max_retries}): {e}")
                if driver: 
                    try: driver.quit()
                    except: pass
                time.sleep(2)
                if attempt == max_retries - 1: return None, {}

        if not driver: return None, {}

        try:
            driver.get(target_url)
            self.logger.info("網頁載入完成，監聽中... (請手動點擊播放)")
            
            req_map = {}    
            header_map = {} 
            extra_map = {}  
            video_candidates = [] 
            
            wait_seconds = 180 
            
            MIN_SIZE_BYTES = 10 * 1024 * 1024 
            MIN_DURATION_SEC = 60 
            
            stability_count = 0
            required_stability = 2
            
            for i in range(wait_seconds):
                action_taken = self._smart_bypass(driver)
                
                if action_taken and is_bahamut:
                    video_candidates.clear()
                    stability_count = 0

                try:
                    logs = driver.get_log('performance')
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
                                if url: 
                                    req_map[req_id] = url
                                    if 'headers' in request:
                                        header_map[req_id] = request['headers']
                            
                            elif method == 'Network.requestWillBeSentExtraInfo':
                                headers = params.get('headers', {})
                                if headers: extra_map[req_id] = headers
                                    
                            elif method == 'Network.responseReceived':
                                response = params.get('response', {})
                                mime_type = response.get('mimeType', '').lower()
                                target_mimes = ['video/', 'mpegurl', 'application/x-mpegurl', 'application/vnd.apple.mpegurl']
                                if any(tm in mime_type for tm in target_mimes):
                                    if req_id in req_map and not req_map[req_id].startswith('blob:'):
                                        if req_id not in video_candidates:
                                            video_candidates.append(req_id)
                        except: continue
                except Exception as e:
                    pass
                
                found_candidate_in_this_loop = False
                
                if video_candidates:
                    for rid in reversed(video_candidates):
                        url = req_map.get(rid, "")
                        clean_url = url.split('?')[0].lower()
                        
                        if clean_url.endswith('.ts') or clean_url.endswith('.m4s'):
                            continue

                        if found_url and found_url == url:
                            found_candidate_in_this_loop = True
                            break
                        
                        self.logger.info(f"檢驗候選連結: {clean_url[-40:]}")
                        is_valid, reason = self._validate_media(driver, url, MIN_SIZE_BYTES, MIN_DURATION_SEC)
                        
                        if is_valid:
                            self.logger.info(f"✅ 連結有效 ({reason})")
                            found_url = url
                            found_candidate_in_this_loop = True
                            
                            main_h = header_map.get(rid, {})
                            extra_h = extra_map.get(rid, {})
                            raw_headers = {**main_h, **extra_h}
                            
                            blocked_keys = ['host', 'content-length', 'connection', 'accept-encoding']
                            normalized_headers = {}

                            for k, v in raw_headers.items():
                                if k.startswith(':'): continue
                                k_lower = k.lower()
                                if k_lower in blocked_keys: continue
                                
                                final_k = k
                                if k_lower == 'cookie': final_k = 'Cookie'
                                elif k_lower == 'referer': final_k = 'Referer'
                                elif k_lower == 'user-agent': final_k = 'User-Agent'
                                elif k_lower == 'origin': final_k = 'Origin'
                                
                                # 這裡會執行 Latin-1 過濾
                                clean_val = self._clean_header_value(v)
                                if not clean_val: continue
                                
                                existing_key = next((ek for ek in normalized_headers if ek.lower() == k_lower), None)
                                if existing_key:
                                    if k_lower in ['cookie', 'referer', 'user-agent', 'origin']:
                                        del normalized_headers[existing_key]
                                        normalized_headers[final_k] = clean_val
                                    else:
                                        normalized_headers[existing_key] = clean_val
                                else:
                                    normalized_headers[final_k] = clean_val
                            
                            found_headers = normalized_headers

                            if 'User-Agent' not in found_headers:
                                found_headers['User-Agent'] = base_ua

                            if 'Cookie' in found_headers:
                                c_val = found_headers['Cookie']
                                self.logger.info(f"🍪 成功捕獲 Cookie (長度: {len(c_val)}, 前綴: {c_val[:20]}...)")

                            try:
                                cookie_file_path = os.path.join(cwd, "cookies.txt")
                                self._save_netscape_cookies(driver, cookie_file_path)
                                found_headers['_USE_COOKIE_FILE'] = cookie_file_path
                            except: pass

                            break 
                        else:
                            self.logger.warning(f"❌ 連結無效 ({reason})")
                            video_candidates.remove(rid)
                
                if found_candidate_in_this_loop:
                    stability_count += 1
                    
                    if stability_count >= required_stability:
                        if i < min_exit_time:
                            if i % 5 == 0:
                                self.logger.info(f"目標鎖定，強制等待時間 ({i}/{min_exit_time}s)...")
                        else:
                            self.logger.info(f"🎯 目標確認 ({stability_count}s)，發起下載！")
                            break 
                else:
                    if stability_count > 0: stability_count = 0

                if i % 5 == 0:
                    self.logger.info(f"嗅探中 ({i}s) - 候選數: {len(video_candidates)}")
                    driver.execute_script("window.scrollTo(0, 300);")
                
                time.sleep(1)

            if not found_url:
                self.logger.error("時間到，未發現符合條件的影片")

        except Exception as e:
            self.logger.error(f"嗅探異常: {e}", exc_info=True)
        finally:
            if driver:
                try: driver.quit()
                except: pass

        return found_url, found_headers

    def _save_netscape_cookies(self, driver, filepath):
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write("# Netscape HTTP Cookie File\n")
            for cookie in driver.get_cookies():
                domain = cookie.get('domain', '')
                flag = 'TRUE' if domain.startswith('.') else 'FALSE'
                path = cookie.get('path', '/')
                secure = 'TRUE' if cookie.get('secure') else 'FALSE'
                expiry = cookie.get('expiry')
                if not expiry:
                    expiry = int(time.time() + 3600*24*7) 
                else:
                    expiry = int(expiry)
                name = cookie.get('name', '')
                value = cookie.get('value', '')
                f.write(f"{domain}\t{flag}\t{path}\t{secure}\t{expiry}\t{name}\t{value}\n")

    def _clean_header_value(self, value) -> str:
        if isinstance(value, list):
            value = "; ".join([str(v) for v in value])
        
        if value is None: return ""
        value = str(value)

        # 1. 移除控制字元 (換行等)
        value = re.sub(r'[\x00-\x1f\x7f]+', ' ', value).strip()
        
        # 2. [關鍵修正] Latin-1 防火牆：
        # HTTP Header 只允許 ISO-8859-1 (Latin-1) 字元 (ASCII 0-255)。
        # 任何中文或 Unicode 符號都會導致 Python requests/http.client 崩潰。
        # 這裡我們直接過濾掉所有 ord > 255 的字元。
        return "".join(c for c in value if ord(c) < 256)

    def _validate_media(self, driver, url: str, min_size: int, min_duration: int) -> Tuple[bool, str]:
        if ".m3u8" in url or "mpegurl" in url:
            return True, "Detected M3U8"
        return False, "Not M3U8"

    def _smart_bypass(self, driver) -> bool:
        clicked = False
        try:
            play_selectors = [
                "div[class*='project-media-cover']", 
                "div[class*='play-button']",         
                "button[class*='vjs-big-play-button']", 
                "div[role='button'][aria-label='Play']"
            ]
            for sel in play_selectors:
                elements = driver.find_elements(By.CSS_SELECTOR, sel)
                for btn in elements:
                    if btn.is_displayed():
                        driver.execute_script("arguments[0].click();", btn)
                        clicked = True
                        time.sleep(0.5)
            
            frames = driver.find_elements(By.TAG_NAME, "iframe")
            for frame in frames:
                try:
                    driver.switch_to.frame(frame)
                    for sel in play_selectors:
                        btns = driver.find_elements(By.CSS_SELECTOR, sel)
                        for b in btns:
                            driver.execute_script("arguments[0].click();", b)
                            clicked = True
                    driver.switch_to.default_content()
                except:
                    driver.switch_to.default_content()

        except Exception:
            try: driver.switch_to.default_content()
            except: pass
            
        return clicked