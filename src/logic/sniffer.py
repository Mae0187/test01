# src/logic/sniffer.py
import json
import time
import os
import logging
from typing import Optional, Tuple, Dict
from urllib.parse import urlparse

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By

class BrowserSniffer:
    """
    瀏覽器自動嗅探器 (Phase 3.24: The Autopsy Filter)
    特色: 
    1. 實作「內容驗證機制」：不再依賴時間猜測。
    2. MP4 過濾：檔案小於 20MB -> 丟棄 (視為廣告)。
    3. M3U8 過濾：總時長小於 3 分鐘 -> 丟棄 (視為廣告)。
    4. 移除強制等待時間，只要驗證通過立刻下載，效率極大化。
    """
    def __init__(self):
        logging.getLogger('WDM').setLevel(logging.NOTSET)
        self.logger = logging.getLogger("Sniffer")

    def extract_stream_url(self, target_url: str) -> Tuple[Optional[str], Dict]:
        self.logger.info(f"開始嗅探任務: {target_url}")
        
        options = Options()
        # [DEBUG]
        # options.add_argument("--headless=new") 

        base_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        options.add_argument(f"--user-agent={base_ua}")
        
        # 記憶登入狀態
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

        # 1. 啟動瀏覽器
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
            self.logger.info("網頁載入完成，開始監聽並執行「內容驗屍」...")
            
            req_map = {}
            extra_map = {} 
            video_candidates = [] 
            
            # 給予最大 90 秒，因為我們會主動檢查，合格就提早退，不合格就繼續找
            wait_seconds = 90
            
            # 最小檔案大小 (bytes) -> 20MB
            MIN_SIZE_BYTES = 20 * 1024 * 1024
            # 最小影片長度 (秒) -> 3分鐘 (180秒)
            MIN_DURATION_SEC = 180
            
            for i in range(wait_seconds):
                # A. 智慧點擊 (幫助廣告快轉)
                self._smart_bypass(driver)
                
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
                
                # C. [核心邏輯] 即時驗屍 (Real-time Validation)
                if video_candidates:
                    # 倒序檢查，從最新的開始驗證
                    for rid in reversed(video_candidates):
                        url = req_map.get(rid, "")
                        clean_url = url.split('?')[0].lower()
                        
                        # 排除明顯的片段檔
                        if clean_url.endswith('.ts') or clean_url.endswith('.m4s'):
                            continue

                        self.logger.info(f"正在檢驗候選目標: {clean_url[-30:]} ...")
                        
                        # 執行 JS 驗證 (這是最準的)
                        is_valid, reason = self._validate_media(driver, url, MIN_SIZE_BYTES, MIN_DURATION_SEC)
                        
                        if is_valid:
                            self.logger.info(f"✅ 目標通過驗證！({reason})，鎖定並下載。")
                            found_url = url
                            
                            # 提取 Headers
                            raw_headers = extra_map.get(rid, {})
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
                                
                            break # 跳出候選檢查迴圈
                        else:
                            self.logger.warning(f"❌ 目標未通過驗證 ({reason})，丟棄並繼續尋找...")
                            # 從候選名單移除，避免重複檢查
                            video_candidates.remove(rid)
                    
                    if found_url:
                        break # 跳出時間等待迴圈

                if i % 5 == 0:
                    driver.execute_script("window.scrollTo(0, 300);")
                
                time.sleep(1)

            if not found_url:
                self.logger.error("時間到，未發現符合條件 (長度/大小) 的影片")

        except Exception as e:
            self.logger.error(f"嗅探異常: {e}", exc_info=True)
        finally:
            if driver:
                try: driver.quit()
                except: pass

        return found_url, found_headers

    def _validate_media(self, driver, url: str, min_size: int, min_duration: int) -> Tuple[bool, str]:
        """
        [驗屍官] 使用瀏覽器內部的 JS fetch 來檢查目標是否為正片
        1. 如果是 .m3u8 -> 檢查總時長 (Duration)
        2. 如果是 .mp4  -> 檢查檔案大小 (Content-Length)
        """
        try:
            # 注入 JS 程式碼來檢測
            # 回傳格式: {valid: bool, reason: str, type: 'm3u8'|'mp4'}
            js_script = """
            var url = arguments[0];
            var minBytes = arguments[1];
            var minSecs = arguments[2];
            var callback = arguments[3];

            fetch(url, {method: 'GET', headers: {'Range': 'bytes=0-65535'}}) // 只抓開頭，不用下載全部
            .then(response => {
                var cType = response.headers.get('content-type') || '';
                var cLen = response.headers.get('content-length');
                
                // 1. 檢查 M3U8 (看內容)
                if (url.includes('.m3u8') || cType.includes('mpegurl')) {
                    return response.text().then(text => {
                        // 簡單計算 #EXTINF 的總和
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
                // 2. 檢查 MP4 (看大小)
                else {
                    if (cLen && parseInt(cLen) > minBytes) {
                        var mb = (parseInt(cLen)/1024/1024).toFixed(1);
                        callback({valid: true, reason: 'File Size: ' + mb + 'MB'});
                    } else if (cLen) {
                        var mb = (parseInt(cLen)/1024/1024).toFixed(1);
                        callback({valid: false, reason: 'File Too Small: ' + mb + 'MB'});
                    } else {
                        // 讀不到大小，只好盲猜通過 (避免誤殺)
                        callback({valid: true, reason: 'Unknown Size (Pass)'});
                    }
                }
            })
            .catch(err => {
                callback({valid: false, reason: 'Fetch Error: ' + err});
            });
            """
            
            # 使用 async script 等待 JS 回傳結果
            result = driver.execute_async_script(js_script, url, min_size, min_duration)
            return result.get('valid', False), result.get('reason', 'Unknown')

        except Exception as e:
            # 如果檢測出錯 (例如 CORS)，為了保險起見，如果是 m3u8 我們傾向於再等等
            # 但如果是巴哈，通常都在同網域，應該沒問題
            return False, f"Check Failed: {str(e)}"

    def _smart_bypass(self, driver) -> bool:
        clicked = False
        try:
            agree_btns = driver.find_elements(By.XPATH, "//*[contains(text(), '同意') or contains(text(), 'Yes') or contains(text(), 'Enter')]")
            for btn in agree_btns:
                if btn.is_displayed():
                    driver.execute_script("arguments[0].click();", btn)
                    clicked = True
                    time.sleep(0.5)

            skip_btns = driver.find_elements(By.XPATH, "//*[contains(text(), '略過廣告') or contains(text(), '跳過廣告') or contains(text(), 'Skip Ad') or contains(text(), '點此跳過')]")
            for btn in skip_btns:
                if btn.is_displayed():
                    driver.execute_script("arguments[0].click();", btn)
                    self.logger.info("擊破【廣告防禦】")
                    clicked = True

            if not clicked:
                selectors = [".vjs-big-play-button", ".plyr__control--overlaid", ".art-control-play", "button[aria-label='Play']", "div[title='Play']", ".html5-main-video"]
                for css in selectors:
                    elements = driver.find_elements(By.CSS_SELECTOR, css)
                    for btn in elements:
                        if btn.is_displayed():
                            driver.execute_script("arguments[0].click();", btn)
                            time.sleep(0.5)

            driver.execute_script("var vids=document.querySelectorAll('video');vids.forEach(v=>{v.muted=true;v.play().catch(e=>{});});")

        except Exception:
            pass
            
        return clicked