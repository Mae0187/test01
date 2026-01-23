# src/logic/sniffer.py
import json
import time
import os
import logging
import re
from typing import Optional, Tuple, Dict
from urllib.parse import urlparse

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By

class BrowserSniffer:
    """
    瀏覽器自動嗅探器 (Phase 3.38: Result-Oriented Lock)
    特色: 
    1. 【驗證即鎖定】不再等待影片播放狀態 (因 Iframe 權限問題無法偵測)。
    2. 【長度過濾】只要 M3U8 解析出長度大於 3 分鐘，即視為正片並直接鎖定。
    3. 【被動觸發】支援使用者手動點擊後，自動捕獲產生的新流量。
    """
    def __init__(self):
        logging.getLogger('WDM').setLevel(logging.NOTSET)
        self.logger = logging.getLogger("Sniffer")

    def extract_stream_url(self, target_url: str) -> Tuple[Optional[str], Dict]:
        self.logger.info(f"開始嗅探任務: {target_url}")
        
        is_bahamut = "ani.gamer.com.tw" in target_url
        min_exit_time = 32 if is_bahamut else 0
        
        if is_bahamut:
            self.logger.info(f"偵測到巴哈姆特，啟用強制觀察期 (至少 {min_exit_time} 秒)...")

        options = Options()
        # [DEBUG]
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
            extra_map = {} 
            video_candidates = [] 
            
            wait_seconds = 180 # 給予足夠的時間讓使用者操作
            
            # 只要長度大於 3 分鐘 (180秒)，我們就認定它是正片
            MIN_SIZE_BYTES = 20 * 1024 * 1024
            MIN_DURATION_SEC = 180
            
            stability_count = 0
            required_stability = 2
            
            for i in range(wait_seconds):
                # A. 嘗試自動點擊 (但不強求成功)
                action_taken = self._smart_bypass(driver)
                
                if action_taken and is_bahamut:
                    video_candidates.clear()
                    stability_count = 0

                # B. 收割 Log
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
                                if url: req_map[req_id] = url
                                    
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
                
                # C. [核心邏輯] 即時驗證與鎖定
                # 我們不再檢查 is_playing，因為 Iframe 會導致該數值為 False
                # 我們完全依賴 _validate_media 的結果
                
                found_candidate_in_this_loop = False
                
                if video_candidates:
                    # 檢查最新的候選者
                    for rid in reversed(video_candidates):
                        url = req_map.get(rid, "")
                        clean_url = url.split('?')[0].lower()
                        
                        # 排除明顯的片段
                        if clean_url.endswith('.ts') or clean_url.endswith('.m4s'):
                            continue

                        # 如果這個 URL 已經被鎖定過，就繼續保持
                        if found_url and found_url == url:
                            found_candidate_in_this_loop = True
                            break
                        
                        self.logger.info(f"檢驗候選連結: {clean_url[-40:]}")
                        is_valid, reason = self._validate_media(driver, url, MIN_SIZE_BYTES, MIN_DURATION_SEC)
                        
                        if is_valid:
                            self.logger.info(f"✅ 連結有效 ({reason})")
                            found_url = url
                            found_candidate_in_this_loop = True
                            
                            # 1. 產出 Cookie 檔案
                            try:
                                cookie_file_path = os.path.join(cwd, "cookies.txt")
                                self._save_netscape_cookies(driver, cookie_file_path)
                                found_headers['_USE_COOKIE_FILE'] = cookie_file_path
                            except: pass

                            # 2. 捕捉 Headers (包含 Referer)
                            raw_headers = extra_map.get(rid, {})
                            blocked_headers = ['host', 'content-length', 'connection', 'accept-encoding', 'cookie']
                            
                            for k, v in raw_headers.items():
                                if k.startswith(':'): continue
                                k_lower = k.lower()
                                if k_lower in blocked_headers: continue
                                
                                clean_val = self._clean_header_value(v)
                                found_headers[k] = clean_val 

                            if 'User-Agent' not in found_headers:
                                found_headers['User-Agent'] = base_ua
                            
                            # 找到最新的且有效的，就跳出檢查迴圈
                            break 
                        else:
                            # 只有真的驗證失敗 (例如長度太短) 才是無效
                            # 這樣可以過濾掉廣告或預覽片
                            self.logger.warning(f"❌ 連結無效 ({reason})")
                            video_candidates.remove(rid)
                
                # D. 穩定性計數
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

    def _clean_header_value(self, value: str) -> str:
        if value is None: return ""
        value = str(value)
        temp = value.replace('\n', ' ').replace('\r', ' ')
        return re.sub(r'[^\x20-\x7E]', '', temp).strip()

    def _validate_media(self, driver, url: str, min_size: int, min_duration: int) -> Tuple[bool, str]:
        try:
            js_script = """
            var url = arguments[0];
            var minBytes = arguments[1];
            var minSecs = arguments[2];
            var callback = arguments[3];

            fetch(url, {method: 'GET', headers: {'Range': 'bytes=0-65535'}}) 
            .then(response => {
                var cType = response.headers.get('content-type') || '';
                var cLen = response.headers.get('content-length');
                
                if (url.includes('.m3u8') || cType.includes('mpegurl')) {
                    return response.text().then(text => {
                        var duration = 0;
                        var lines = text.split('\\n');
                        for (var line of lines) {
                            if (line.startsWith('#EXTINF:')) {
                                var sec = parseFloat(line.split(':')[1]);
                                if (!isNaN(sec)) duration += sec;
                            }
                        }
                        if (duration > minSecs) {
                            callback({valid: true, reason: 'M3U8 Duration: ' + duration.toFixed(1) + 's'});
                        } else {
                            callback({valid: false, reason: 'M3U8 Too Short: ' + duration.toFixed(1) + 's'});
                        }
                    });
                } 
                else {
                    if (cLen && parseInt(cLen) > minBytes) {
                        var mb = (parseInt(cLen)/1024/1024).toFixed(1);
                        callback({valid: true, reason: 'File Size: ' + mb + 'MB'});
                    } else if (cLen) {
                        var mb = (parseInt(cLen)/1024/1024).toFixed(1);
                        callback({valid: false, reason: 'File Too Small: ' + mb + 'MB'});
                    } else {
                        callback({valid: true, reason: 'Unknown Size (Pass)'});
                    }
                }
            })
            .catch(err => {
                callback({valid: false, reason: 'Fetch Error: ' + err});
            });
            """
            result = driver.execute_async_script(js_script, url, min_size, min_duration)
            return result.get('valid', False), result.get('reason', 'Unknown')

        except Exception as e:
            return False, f"Check Failed: {str(e)}"

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

            agree_btns = driver.find_elements(By.XPATH, "//*[contains(text(), '同意') or contains(text(), 'Yes') or contains(text(), 'Enter')]")
            for btn in agree_btns:
                if btn.is_displayed():
                    driver.execute_script("arguments[0].click();", btn)
                    clicked = True

            skip_btns = driver.find_elements(By.XPATH, "//*[contains(text(), '略過廣告') or contains(text(), '跳過廣告') or contains(text(), 'Skip Ad') or contains(text(), '點此跳過')]")
            for btn in skip_btns:
                if btn.is_displayed():
                    driver.execute_script("arguments[0].click();", btn)
                    self.logger.info("擊破【廣告防禦】")
                    clicked = True
            
            # Iframe 穿透點擊 (盡力而為)
            if not clicked:
                frames = driver.find_elements(By.TAG_NAME, "iframe")
                for i, frame in enumerate(frames):
                    try:
                        driver.switch_to.frame(frame)
                        for sel in play_selectors:
                            elements = driver.find_elements(By.CSS_SELECTOR, sel)
                            for btn in elements:
                                if btn.is_displayed():
                                    driver.execute_script("arguments[0].click();", btn)
                                    self.logger.info(f"擊破 Iframe[{i}] 內的按鈕")
                                    clicked = True
                                    time.sleep(0.5)
                        driver.switch_to.default_content()
                        if clicked: break
                    except:
                        driver.switch_to.default_content()

        except Exception:
            try: driver.switch_to.default_content()
            except: pass
            
        return clicked