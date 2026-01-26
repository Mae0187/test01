# -*- coding: utf-8 -*-
# src/logic/browser_downloader.py
# [VibeCoding] Phase 10: The Clone (Header Cloning + curl_cffi)

import os
import time
import json
import base64
import logging
import m3u8
import shutil
import re
from typing import Callable, Optional

# Selenium
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

# Curl_cffi
try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL = True
except ImportError:
    HAS_CURL = False

try:
    from Crypto.Cipher import AES
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

class BrowserDownloader:
    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger("BrowserBridge")
        self.driver = None
        self.is_cancelled = False

    def _init_driver(self):
        options = Options()
        profile_dir = os.path.join(os.getcwd(), "browser_profile")
        if not os.path.exists(profile_dir): os.makedirs(profile_dir)
        options.add_argument(f"--user-data-dir={profile_dir}")
        options.add_argument("--mute-audio")
        options.add_argument("--disable-gpu")
        options.add_argument("--log-level=3")
        options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})

        service = Service(ChromeDriverManager().install())
        service.creation_flags = 0x08000000 
        self.driver = webdriver.Chrome(service=service, options=options)

    def download(self, target_url: str, output_path: str, progress_callback: Optional[Callable] = None) -> bool:
        if not HAS_CURL or not HAS_CRYPTO:
            self.logger.error("❌ 缺少 curl_cffi 或 pycryptodome")
            return False

        self.logger.info(f"[Clone] 啟動表頭複製模式...")
        
        try:
            self._init_driver()
            self.logger.info("[Clone] 進入頁面，請等待 M3U8...")
            self.driver.get(target_url)

            # 1. 抓取 M3U8 並提取真實 Headers
            m3u8_url = None
            real_headers = {}
            
            for i in range(30):
                if self.is_cancelled: return False
                try: self.driver.execute_script("window.scrollBy(0, 100);")
                except: pass
                
                # 從 Network Logs 找 .m3u8 的請求標頭
                logs = self.driver.get_log('performance')
                for entry in logs:
                    try:
                        msg = json.loads(entry['message'])['message']
                        if msg['method'] == 'Network.requestWillBeSent':
                            req = msg['params']['request']
                            url = req['url']
                            if '.m3u8' in url and ('token' in url or 'hls' in url):
                                m3u8_url = url
                                # 這是關鍵：我們偷到了瀏覽器發送的 headers
                                real_headers = req['headers']
                                break
                    except: pass
                
                if m3u8_url: break
                time.sleep(3)
            
            if not m3u8_url:
                self.logger.error("❌ 找不到 M3U8")
                return False

            self.logger.info(f"✅ 捕獲 M3U8，並成功複製 {len(real_headers)} 個 Headers")
            
            # 2. 建立 Python Session (模擬 Chrome 120)
            session = cffi_requests.Session(impersonate="chrome120")
            
            # 將偷來的 Headers 注入 Session
            # 注意：需過濾掉一些危險的 header 如 :method, :scheme 等
            for k, v in real_headers.items():
                if not k.startswith(':'):
                    session.headers[k] = v
            
            # 補強 Cookie (確保最新)
            selenium_cookies = self.driver.get_cookies()
            for c in selenium_cookies:
                session.cookies.set(c['name'], c['value'], domain=c['domain'])

            self.logger.info("[Clone] 關閉瀏覽器，轉交 Python 下載...")
            self.driver.quit()
            self.driver = None

            # 3. 下載 M3U8
            resp = session.get(m3u8_url)
            if resp.status_code != 200:
                self.logger.error(f"M3U8 下載失敗: {resp.status_code}")
                return False
                
            playlist = m3u8.loads(resp.text, uri=m3u8_url)
            if playlist.is_variant:
                best = max(playlist.playlists, key=lambda p: p.stream_info.bandwidth or 0)
                # 對子列表也用同樣的 Headers
                resp = session.get(best.absolute_uri)
                playlist = m3u8.loads(resp.text, uri=best.absolute_uri)

            segments = playlist.segments
            total_segments = len(segments)
            self.logger.info(f"[Clone] 開始下載 {total_segments} 個切片...")

            temp_dir = output_path + "_temp"
            if not os.path.exists(temp_dir): os.makedirs(temp_dir)

            # 4. 單線程下載 (避免並發觸發風控)
            success_count = 0
            for idx, seg in enumerate(segments):
                if self.is_cancelled: break
                
                fname = f"seg_{idx:05d}.ts"
                save_path = os.path.join(temp_dir, fname)
                
                if os.path.exists(save_path) and os.path.getsize(save_path) > 0:
                    success_count += 1
                    continue
                
                # Key
                key_info = None
                if seg.key and seg.key.method == 'AES-128':
                    key_uri = seg.key.absolute_uri
                    iv = seg.key.iv
                    if not iv: iv = idx.to_bytes(16, byteorder='big')
                    elif isinstance(iv, str) and iv.startswith('0x'):
                        iv = base64.b16decode(iv[2:].upper().zfill(32))
                    key_info = {'uri': key_uri, 'iv': iv}

                # 下載
                try:
                    # 使用偷來的 Headers 去請求 segment
                    # 這裡不需要改 Headers，因為 session 已經有了
                    s_resp = session.get(seg.absolute_uri, timeout=10)
                    if s_resp.status_code == 200:
                        data = s_resp.content
                        
                        # 解密
                        if key_info:
                            # 獲取 Key
                            k_resp = session.get(key_info['uri'])
                            key_bytes = k_resp.content
                            cipher = AES.new(key_bytes, AES.MODE_CBC, key_info['iv'])
                            try: data = cipher.decrypt(data)
                            except: pass

                        with open(save_path, 'wb') as f:
                            f.write(data)
                        success_count += 1
                    else:
                        self.logger.warning(f"切片 {idx} 失敗: {s_resp.status_code}")
                except Exception as e:
                    pass

                if progress_callback and idx % 5 == 0:
                    percent = ((idx + 1) / total_segments) * 95
                    progress_callback(percent, f"下載中 {idx}/{total_segments}")
            
            # 5. 結算
            if success_count < total_segments * 0.8:
                self.logger.error("❌ 下載失敗")
                return False

            # 合併
            ts_output = output_path.replace(".mp4", ".ts")
            with open(ts_output, 'wb') as outfile:
                for idx in range(total_segments):
                    fname = os.path.join(temp_dir, f"seg_{idx:05d}.ts")
                    if os.path.exists(fname):
                        with open(fname, 'rb') as infile:
                            shutil.copyfileobj(infile, outfile)
            
            try: shutil.rmtree(temp_dir)
            except: pass
            
            self._convert_to_mp4(ts_output, output_path)
            return True

        except Exception as e:
            self.logger.error(f"[Clone] 異常: {e}")
            return False