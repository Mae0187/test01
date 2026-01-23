"""
下載管理器 - 整合原生 HLS 下載器
支援 Bahamut 和 Pressplay
"""

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Optional, Callable
from native_hls_downloader import NativeHLSDownloader


class DownloadManager:
    """統一的下載管理器"""
    
    def __init__(self, platform: str):
        """
        Args:
            platform: 平台名稱 ('bahamut' 或 'pressplay')
        """
        self.platform = platform.lower()
        self.use_native_downloader = (self.platform == 'pressplay')
    
    def download(
        self,
        m3u8_url: str,
        headers: Dict[str, str],
        output_path: str,
        progress_callback: Optional[Callable] = None
    ) -> bool:
        """
        執行下載
        
        Args:
            m3u8_url: M3U8 播放清單 URL
            headers: HTTP Headers (包含 Cookie)
            output_path: 輸出檔案路徑
            progress_callback: 進度回調函數 (current, total, status)
        
        Returns:
            bool: 是否成功
        """
        print(f"[下載管理器] 平台: {self.platform}")
        print(f"[下載管理器] M3U8: {m3u8_url}")
        print(f"[下載管理器] 輸出: {output_path}")
        
        if self.use_native_downloader:
            # Pressplay 使用原生下載器
            return self._download_with_native(m3u8_url, headers, output_path, progress_callback)
        else:
            # Bahamut 使用 yt-dlp (相對簡單的場景)
            return self._download_with_ytdlp(m3u8_url, headers, output_path, progress_callback)
    
    def _download_with_native(
        self,
        m3u8_url: str,
        headers: Dict[str, str],
        output_path: str,
        progress_callback: Optional[Callable]
    ) -> bool:
        """使用原生 Python HLS 下載器 (Pressplay)"""
        try:
            print("[下載器] 使用原生 Python HLS 下載器")
            
            downloader = NativeHLSDownloader(m3u8_url, headers, output_path)
            success = downloader.download(progress_callback=progress_callback)
            
            return success
            
        except Exception as e:
            print(f"[錯誤] 原生下載器失敗: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _download_with_ytdlp(
        self,
        m3u8_url: str,
        headers: Dict[str, str],
        output_path: str,
        progress_callback: Optional[Callable]
    ) -> bool:
        """使用 yt-dlp + FFmpeg (Bahamut)"""
        try:
            print("[下載器] 使用 yt-dlp")
            
            # 建立暫時的 Cookie 檔案
            cookie_file = self._create_cookie_file(headers.get('Cookie', ''))
            
            # 建立 yt-dlp 指令
            cmd = [
                'yt-dlp',
                '--no-warnings',
                '--no-check-certificate',
                '-o', output_path,
            ]
            
            # 添加 Headers
            if 'Referer' in headers:
                cmd.extend(['--referer', headers['Referer']])
            if 'User-Agent' in headers:
                cmd.extend(['--user-agent', headers['User-Agent']])
            if cookie_file:
                cmd.extend(['--cookies', cookie_file])
            
            # 添加 M3U8 URL
            cmd.append(m3u8_url)
            
            print(f"[執行] {' '.join(cmd)}")
            
            # 執行下載
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace'
            )
            
            # 清理 Cookie 檔案
            if cookie_file and os.path.exists(cookie_file):
                os.remove(cookie_file)
            
            if result.returncode != 0:
                print(f"[yt-dlp 錯誤]\n{result.stderr}")
                return False
            
            print("[完成] yt-dlp 下載成功")
            return True
            
        except Exception as e:
            print(f"[錯誤] yt-dlp 下載失敗: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _create_cookie_file(self, cookie_string: str) -> Optional[str]:
        """建立暫時的 Netscape Cookie 檔案"""
        if not cookie_string:
            return None
        
        try:
            # 建立暫存檔案
            fd, cookie_file = tempfile.mkstemp(suffix='.txt', prefix='cookies_')
            os.close(fd)
            
            # 寫入 Netscape 格式
            with open(cookie_file, 'w', encoding='utf-8') as f:
                f.write("# Netscape HTTP Cookie File\n")
                
                # 解析 Cookie 字串
                for cookie in cookie_string.split(';'):
                    cookie = cookie.strip()
                    if '=' in cookie:
                        name, value = cookie.split('=', 1)
                        # Netscape 格式: domain, flag, path, secure, expiration, name, value
                        f.write(f".pressplay.cc\tTRUE\t/\tFALSE\t0\t{name}\t{value}\n")
            
            return cookie_file
            
        except Exception as e:
            print(f"[警告] 建立 Cookie 檔案失敗: {e}")
            return None


# ============================================
# 在原有的 GUI 中整合
# ============================================

class DownloadWorker:
    """配合 PySide6 的下載執行緒 Worker"""
    
    def __init__(self, platform: str, m3u8_url: str, headers: Dict, output_path: str):
        self.manager = DownloadManager(platform)
        self.m3u8_url = m3u8_url
        self.headers = headers
        self.output_path = output_path
    
    def run(self):
        """執行下載 (在執行緒中執行)"""
        def progress_callback(current, total, status):
            # 發送進度訊號給 GUI
            print(f"[進度] {current}/{total} - {status}")
            # 如果使用 Qt Signal: self.progress_signal.emit(current, total, status)
        
        success = self.manager.download(
            self.m3u8_url,
            self.headers,
            self.output_path,
            progress_callback=progress_callback
        )
        
        if success:
            print("[完成] 下載成功!")
            # self.finished_signal.emit(True)
        else:
            print("[失敗] 下載失敗!")
            # self.finished_signal.emit(False)


# ============================================
# 測試範例
# ============================================

def test_pressplay():
    """測試 Pressplay 下載"""
    manager = DownloadManager('pressplay')
    
    # 這些資料應該從 Sniffer 取得
    m3u8_url = "https://example.pressplay.cc/playlist.m3u8"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://pressplay.cc/...",
        "Cookie": "session=abc123; token=xyz789; ..."
    }
    output_path = "pressplay_video.mp4"
    
    success = manager.download(m3u8_url, headers, output_path)
    print(f"下載結果: {'成功' if success else '失敗'}")


def test_bahamut():
    """測試 Bahamut 下載"""
    manager = DownloadManager('bahamut')
    
    m3u8_url = "https://example.bahamut.com/playlist.m3u8"
    headers = {
        "User-Agent": "Mozilla/5.0...",
        "Referer": "https://ani.gamer.com.tw/..."
    }
    output_path = "bahamut_anime.mp4"
    
    success = manager.download(m3u8_url, headers, output_path)
    print(f"下載結果: {'成功' if success else '失敗'}")


if __name__ == "__main__":
    # 根據需要測試不同平台
    test_pressplay()
    # test_bahamut()