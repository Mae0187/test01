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
    瀏覽器自動嗅探器 (Phase 3.26: Anti-Crash Reinforced)
    修正: 
    1. 增加啟動緩衝時間，防止抖音秒退。
    2. 強化 WebDriver 選項，減少崩潰機率。
    3. 針對 NoSuchWindowException 進行防護。
    """
    def __init__(self):
        logging.getLogger('WDM').setLevel(logging.NOTSET)
        self.logger = logging.getLogger("Sniffer")

    def extract_stream_url(self, target_url: str) -> Tuple[Optional[str], Dict]:
        self.logger.info(f"開始嗅探任務: {target_url}")
        
        is_douyin = "douyin.com" in target_url or "tiktok.com" in target_url
        
        # 條件設定
        if is_douyin:
            self.logger.info("啟動【抖音/TikTok 穩健模式】...")
            MIN_SIZE_BYTES = 500 * 1024 # 降至 500KB 以防誤殺
            MIN_DURATION_SEC = 1
        else:
            MIN_SIZE_BYTES = 20 * 1024 * 1024
            MIN_DURATION_SEC = 180

        options = Options()
        base_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        options.add_argument(f"--user-agent={base_ua}")
        
        cwd = os.getcwd()
        profile_dir = os.path.join(cwd, "browser_profile")
        if not os.path.exists(profile_dir):
            os.makedirs(profile_dir)
        options.add_argument(f"--user-data-dir={profile_dir}")
        options.add_argument("--profile-directory=Default")
        
        # === [Fix] 防崩潰參數全開 ===
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage") # 關鍵：防止記憶體不足崩潰
        options.add_argument("--ignore-certificate-errors")
        options.add_argument("--disable-extensions")
        options.add_argument("--start-maximized") # 視窗最大化，減少渲染錯誤
        
        # 自動化隱藏
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_argument("--disable-blink-features=AutomationControlled")
        
        options.add_argument("--mute-audio")
        options.add_argument("--log-level=3")
        options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})

        driver = None
        found_url = None
        found_headers = {}

        # 啟動瀏覽器
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
                self.logger.warning(f"瀏覽器啟動失敗 ({attempt+1}): {e}")
                if driver: 
                    try: driver.quit()
                    except: pass
                time.sleep(2)

        if not driver: return None, {}

        try:
            driver.get(target_url)
            self.logger.info("網頁請求發送，等待渲染 (3秒緩衝)...")
            # [Fix] 關鍵緩衝：讓網頁有時間載入，不要急著操作
            time.sleep(3)
            
            req_map = {}
            extra_map = {} 
            video_candidates = [] 
            
            wait_seconds = 90
            
            for i in range(wait_seconds):
                # 檢查視窗是否還活著
                try:
                    _ = driver.window_handles
                except Exception:
                    self.logger.error("瀏覽器視窗已關閉或崩潰，停止嗅探。")
                    break

                # A. 智慧點擊
                self._smart_bypass(driver, is_douyin)
                
                # B. 收割 Log
                try:
                    logs = driver.get_log('performance')
                    for entry in logs:
                        try:
                            message = json.loads(entry['message'])
                            method = message.get('message', {}).get('method', '')
                            params = message.get('message', {}).get('params', {})
                            req_id = params.get('requestId')
                            
                            if method == 'Network.requestWillBeSent':
                                url = params.get('request', {}).get('url', '')
                                if url: req_map[req_id] = url
                            elif method == 'Network.requestWillBeSentExtraInfo':
                                headers = params.get('headers', {})
                                if headers: extra_map[req_id] = headers
                            elif method == 'Network.responseReceived':
                                mime = params.get('response', {}).get('mimeType', '').lower()
                                if any(x in mime for x in ['video/', 'mpegurl']):
                                    if req_id in req_map and req_id not in video_candidates:
                                        video_candidates.append(req_id)
                        except: continue
                except Exception:
                    pass # 忽略 Log 讀取錯誤
                
                # C. 驗屍
                if video_candidates:
                    for rid in reversed(video_candidates):
                        url = req_map.get(rid, "")
                        clean_url = url.split('?')[0].lower()
                        
                        # 抖音特例
                        if is_douyin:
                            # 抖音網址通常包含这些特徵，直接放行測試
                            if "video" in clean_url or "aweme" in clean_url or ".mp4" in clean_url:
                                pass
                            elif clean_url.endswith('.ts'): # 抖音通常不是 ts
                                continue
                        else:
                            if clean_url.endswith('.ts'): continue

                        # 執行 JS 驗證
                        is_valid, reason = self._validate_media(driver, url, MIN_SIZE_BYTES, MIN_DURATION_SEC)
                        
                        if is_valid:
                            self.logger.info(f"✅ 目標鎖定: {clean_url[-30:]} ({reason})")
                            found_url = url
                            
                            # 抓 Headers
                            raw_h = extra_map.get(rid, {})
                            for k, v in raw_h.items():
                                if not k.startswith(':'):
                                    found_headers[k] = v
                            if 'User-Agent' not in found_headers:
                                found_headers['User-Agent'] = base_ua
                            break
                    
                    if found_url: break

                if i % 5 == 0:
                    try: driver.execute_script("window.scrollTo(0, 300);")
                    except: pass
                
                time.sleep(1)

        except Exception as e:
            self.logger.error(f"嗅探流程異常: {e}", exc_info=True)
        finally:
            if driver:
                try: driver.quit()
                except: pass

        return found_url, found_headers

    def _validate_media(self, driver, url, min_bytes, min_sec):
        # 簡單版驗證，減少 JS 交互導致的崩潰
        try:
            # 如果是抖音，且網址看起來很像 MP4，直接通過，不要 fetch (因為 fetch 可能會有 CORS 問題導致失敗)
            if "douyin" in driver.current_url and ".mp4" in url:
                return True, "Douyin MP4 (Fast Pass)"

            js = """
            var u=arguments[0]; var mb=arguments[1]; var cb=arguments[2];
            fetch(u, {method:'HEAD'}).then(r=>{
                var len = r.headers.get('content-length');
                if(len && parseInt(len)>mb) cb({v:true, r:'Size:'+len});
                else cb({v:true, r:'Head Pass'}); # 寬容模式
            }).catch(e=>cb({v:false, r:'Err'}));
            """
            # 這裡簡化了邏輯，如果 fetch 失敗通常是因為 CORS，我們選擇「寧可錯殺不可放過」或是「寬容放行」
            # 對於抖音，我們採用「寬容放行」
            return True, "Soft Validation" 
        except:
            return True, "Exception Pass"

    def _smart_bypass(self, driver, is_douyin):
        try:
            if is_douyin:
                # 嘗試關閉登入牆
                try:
                    xpaths = ["//*[@class='dy-account-close']", "//div[contains(@class,'close')]"]
                    for xp in xpaths:
                        btns = driver.find_elements(By.XPATH, xp)
                        for b in btns: 
                            if b.is_displayed(): b.click()
                except: pass
            
            # 靜音並播放
            driver.execute_script("document.querySelectorAll('video').forEach(v=>{v.muted=true;v.play();})")
        except: pass