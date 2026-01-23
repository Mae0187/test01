"""
Native HLS Downloader - 繞過 FFmpeg CLI 限制
適用於 Pressplay 等需要超長 Cookie Header 的平台
"""

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from urllib.parse import urljoin, urlparse
import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad


class NativeHLSDownloader:
    """原生 Python HLS 下載器"""
    
    def __init__(self, m3u8_url: str, headers: Dict[str, str], output_path: str):
        """
        Args:
            m3u8_url: M3U8 播放清單 URL
            headers: 完整的 HTTP Headers (包含 Cookie)
            output_path: 最終輸出影片路徑 (例如: output.mp4)
        """
        self.m3u8_url = m3u8_url
        self.headers = headers
        self.output_path = output_path
        self.base_url = self._get_base_url(m3u8_url)
        
        # 建立暫存目錄
        self.temp_dir = Path(tempfile.mkdtemp(prefix="hls_"))
        self.segments_dir = self.temp_dir / "segments"
        self.segments_dir.mkdir(exist_ok=True)
        
        print(f"[初始化] 暫存目錄: {self.temp_dir}")
    
    def _get_base_url(self, url: str) -> str:
        """取得 M3U8 的基礎 URL"""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{'/'.join(parsed.path.split('/')[:-1])}/"
    
    def download(self, progress_callback=None) -> bool:
        """
        執行完整下載流程
        
        Args:
            progress_callback: 進度回調函數 callback(current, total, status)
        
        Returns:
            bool: 是否成功
        """
        try:
            # Step 1: 下載並解析 M3U8
            print("[步驟 1/4] 解析 M3U8 播放清單...")
            segments, key_info = self._parse_m3u8()
            total = len(segments)
            print(f"[解析完成] 找到 {total} 個影片分片")
            
            if progress_callback:
                progress_callback(0, total, "開始下載分片")
            
            # Step 2: 下載所有分片
            print("[步驟 2/4] 下載影片分片...")
            downloaded_files = []
            for i, segment_url in enumerate(segments, 1):
                segment_path = self._download_segment(segment_url, i, key_info)
                if segment_path:
                    downloaded_files.append(segment_path)
                    if progress_callback:
                        progress_callback(i, total, f"下載中 ({i}/{total})")
                else:
                    print(f"[警告] 分片 {i} 下載失敗: {segment_url}")
                    return False
            
            # Step 3: 合併分片
            print(f"[步驟 3/4] 合併 {len(downloaded_files)} 個分片...")
            if progress_callback:
                progress_callback(total, total, "合併影片中")
            
            success = self._merge_segments(downloaded_files)
            
            # Step 4: 清理暫存檔案
            print("[步驟 4/4] 清理暫存檔案...")
            self._cleanup()
            
            if success:
                print(f"[完成] 影片已儲存至: {self.output_path}")
                if progress_callback:
                    progress_callback(total, total, "下載完成")
            
            return success
            
        except Exception as e:
            print(f"[錯誤] 下載失敗: {e}")
            import traceback
            traceback.print_exc()
            self._cleanup()
            return False
    
    def _parse_m3u8(self) -> Tuple[List[str], Optional[Dict]]:
        """
        解析 M3U8 並提取分片 URL 和加密資訊
        
        Returns:
            (segments, key_info): 分片列表和加密金鑰資訊
        """
        resp = requests.get(self.m3u8_url, headers=self.headers, timeout=30)
        resp.raise_for_status()
        
        content = resp.text
        segments = []
        key_info = None
        
        for line in content.splitlines():
            line = line.strip()
            
            # 解析加密資訊
            if line.startswith("#EXT-X-KEY"):
                key_info = self._parse_key_line(line)
            
            # 解析分片 URL
            elif line and not line.startswith("#"):
                # 轉換相對 URL 為絕對 URL
                if line.startswith("http"):
                    segment_url = line
                else:
                    segment_url = urljoin(self.base_url, line)
                segments.append(segment_url)
        
        return segments, key_info
    
    def _parse_key_line(self, line: str) -> Dict:
        """解析 #EXT-X-KEY 行"""
        key_info = {}
        
        # 提取 METHOD
        method_match = re.search(r'METHOD=([^,\s]+)', line)
        if method_match:
            key_info['method'] = method_match.group(1)
        
        # 提取 URI
        uri_match = re.search(r'URI="([^"]+)"', line)
        if uri_match:
            key_uri = uri_match.group(1)
            key_info['uri'] = urljoin(self.base_url, key_uri)
        
        # 提取 IV (如果有)
        iv_match = re.search(r'IV=0x([0-9A-Fa-f]+)', line)
        if iv_match:
            key_info['iv'] = bytes.fromhex(iv_match.group(1))
        
        return key_info
    
    def _download_segment(self, url: str, index: int, key_info: Optional[Dict]) -> Optional[Path]:
        """
        下載單個分片並解密 (如果需要)
        
        Args:
            url: 分片 URL
            index: 分片序號
            key_info: 加密金鑰資訊
        
        Returns:
            Path: 解密後的分片檔案路徑，失敗則返回 None
        """
        try:
            # 下載分片
            resp = requests.get(url, headers=self.headers, timeout=60)
            resp.raise_for_status()
            data = resp.content
            
            # 如果有加密，進行解密
            if key_info and key_info.get('method') == 'AES-128':
                data = self._decrypt_segment(data, key_info, index)
            
            # 儲存到暫存目錄
            segment_path = self.segments_dir / f"segment_{index:05d}.ts"
            segment_path.write_bytes(data)
            
            return segment_path
            
        except Exception as e:
            print(f"[錯誤] 下載分片 {index} 失敗: {e}")
            return None
    
    def _decrypt_segment(self, data: bytes, key_info: Dict, index: int) -> bytes:
        """AES-128 解密"""
        # 下載金鑰
        if not hasattr(self, '_aes_key'):
            key_resp = requests.get(key_info['uri'], headers=self.headers, timeout=30)
            key_resp.raise_for_status()
            self._aes_key = key_resp.content
        
        # 取得 IV (如果沒有指定，使用分片序號)
        if 'iv' in key_info:
            iv = key_info['iv']
        else:
            iv = index.to_bytes(16, byteorder='big')
        
        # AES-128-CBC 解密
        cipher = AES.new(self._aes_key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(data)
        
        # 移除 PKCS7 Padding
        try:
            decrypted = unpad(decrypted, AES.block_size)
        except ValueError:
            # 某些串流可能沒有正確的 padding
            pass
        
        return decrypted
    
    def _merge_segments(self, segment_files: List[Path]) -> bool:
        """
        使用 FFmpeg 合併分片 (無損模式 -c copy)
        
        這裡不涉及任何 Cookie/Header，只是純粹的檔案串接
        """
        try:
            # 建立 concat 清單檔案
            concat_file = self.temp_dir / "concat_list.txt"
            with open(concat_file, 'w', encoding='utf-8') as f:
                for seg in segment_files:
                    # FFmpeg concat 需要轉義路徑
                    escaped_path = str(seg).replace('\\', '/').replace("'", "'\\''")
                    f.write(f"file '{escaped_path}'\n")
            
            # 呼叫 FFmpeg (這裡不需要任何 Cookie)
            ffmpeg_cmd = [
                'ffmpeg',
                '-y',  # 覆蓋輸出
                '-f', 'concat',
                '-safe', '0',
                '-i', str(concat_file),
                '-c', 'copy',  # 無損串接
                '-bsf:a', 'aac_adtstoasc',  # 修復某些 AAC 串流
                self.output_path
            ]
            
            print(f"[執行] {' '.join(ffmpeg_cmd)}")
            
            result = subprocess.run(
                ffmpeg_cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace'
            )
            
            if result.returncode != 0:
                print(f"[FFmpeg 錯誤]\n{result.stderr}")
                return False
            
            return True
            
        except Exception as e:
            print(f"[合併失敗] {e}")
            return False
    
    def _cleanup(self):
        """清理暫存目錄"""
        try:
            import shutil
            if self.temp_dir.exists():
                shutil.rmtree(self.temp_dir)
                print(f"[清理] 已刪除暫存目錄: {self.temp_dir}")
        except Exception as e:
            print(f"[警告] 清理失敗: {e}")


# ============================================
# 使用範例
# ============================================

def example_usage():
    """示範如何使用"""
    
    # 1. 從 Sniffer 取得的資料
    m3u8_url = "https://example.com/playlist.m3u8"
    
    headers = {
        "User-Agent": "Mozilla/5.0...",
        "Referer": "https://pressplay.cc/...",
        "Cookie": "very_long_cookie_string_that_breaks_windows_cmd...",
        # ... 其他 Headers
    }
    
    output_file = "output_video.mp4"
    
    # 2. 建立下載器
    downloader = NativeHLSDownloader(m3u8_url, headers, output_file)
    
    # 3. 執行下載 (帶進度回調)
    def progress_callback(current, total, status):
        print(f"[進度] {current}/{total} - {status}")
    
    success = downloader.download(progress_callback=progress_callback)
    
    if success:
        print("下載成功!")
    else:
        print("下載失敗!")


if __name__ == "__main__":
    example_usage()