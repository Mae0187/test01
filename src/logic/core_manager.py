"""
核心管理器 - 統一協調 Sniffer 和 Downloader
整合 Bahamut 和 Pressplay 的完整流程
"""

import os
import sys
from pathlib import Path
from typing import Optional, Dict
from PySide6.QtCore import QObject, Signal, QThread

# 導入專案模組
from logic.sniffer import VideoSniffer
from logic.downloader import DownloadManager


class DownloadWorker(QThread):
    """下載執行緒 Worker - 避免阻塞 GUI"""
    
    # 定義訊號
    progress_signal = Signal(int, int, str)  # (current, total, status)
    finished_signal = Signal(bool, str)      # (success, message)
    log_signal = Signal(str)                 # 日誌訊息
    
    def __init__(self, platform: str, m3u8_url: str, headers: Dict, output_path: str):
        super().__init__()
        self.platform = platform
        self.m3u8_url = m3u8_url
        self.headers = headers
        self.output_path = output_path
        self.is_cancelled = False
    
    def run(self):
        """執行下載任務"""
        try:
            self.log_signal.emit(f"[開始下載] 平台: {self.platform}")
            self.log_signal.emit(f"[M3U8] {self.m3u8_url}")
            self.log_signal.emit(f"[輸出] {self.output_path}")
            
            # 建立下載管理器
            manager = DownloadManager(self.platform)
            
            # 定義進度回調
            def progress_callback(current, total, status):
                if not self.is_cancelled:
                    self.progress_signal.emit(current, total, status)
                    self.log_signal.emit(f"[進度] {current}/{total} - {status}")
            
            # 執行下載
            success = manager.download(
                self.m3u8_url,
                self.headers,
                self.output_path,
                progress_callback=progress_callback
            )
            
            if self.is_cancelled:
                self.finished_signal.emit(False, "下載已取消")
            elif success:
                self.finished_signal.emit(True, "下載完成！")
            else:
                self.finished_signal.emit(False, "下載失敗，請檢查日誌")
                
        except Exception as e:
            import traceback
            error_msg = f"下載錯誤: {str(e)}\n{traceback.format_exc()}"
            self.log_signal.emit(f"[錯誤] {error_msg}")
            self.finished_signal.emit(False, error_msg)
    
    def cancel(self):
        """取消下載"""
        self.is_cancelled = True


class SniffWorker(QThread):
    """嗅探執行緒 Worker - 在背景執行 Selenium"""
    
    # 定義訊號
    success_signal = Signal(str, dict)  # (m3u8_url, headers)
    failed_signal = Signal(str)         # error_message
    log_signal = Signal(str)            # 日誌訊息
    
    def __init__(self, platform: str, video_url: str, credentials: Optional[Dict] = None):
        super().__init__()
        self.platform = platform
        self.video_url = video_url
        self.credentials = credentials  # {'username': ..., 'password': ...}
    
    def run(self):
        """執行嗅探任務"""
        try:
            self.log_signal.emit(f"[開始嗅探] 平台: {self.platform}")
            self.log_signal.emit(f"[影片頁面] {self.video_url}")
            
            # 建立 Sniffer
            sniffer = VideoSniffer(self.platform)
            
            # 執行嗅探
            result = sniffer.sniff(
                video_url=self.video_url,
                credentials=self.credentials
            )
            
            if result and 'm3u8_url' in result:
                m3u8_url = result['m3u8_url']
                headers = result.get('headers', {})
                
                self.log_signal.emit(f"[成功] 抓取到 M3U8: {m3u8_url}")
                self.log_signal.emit(f"[Headers] {len(headers)} 個欄位")
                
                self.success_signal.emit(m3u8_url, headers)
            else:
                error_msg = "無法找到 M3U8 播放清單"
                self.log_signal.emit(f"[失敗] {error_msg}")
                self.failed_signal.emit(error_msg)
                
        except Exception as e:
            import traceback
            error_msg = f"嗅探錯誤: {str(e)}\n{traceback.format_exc()}"
            self.log_signal.emit(f"[錯誤] {error_msg}")
            self.failed_signal.emit(error_msg)


class CoreManager(QObject):
    """
    核心管理器 - 協調整個下載流程
    
    使用流程:
    1. 建立實例: manager = CoreManager()
    2. 連接訊號
    3. 呼叫 start_sniff() 開始嗅探
    4. 嗅探成功後自動呼叫 start_download()
    """
    
    # 定義訊號
    log_signal = Signal(str)                    # 日誌訊息
    sniff_finished_signal = Signal(bool, str)   # 嗅探完成 (success, message)
    download_progress_signal = Signal(int, int, str)  # 下載進度 (current, total, status)
    download_finished_signal = Signal(bool, str)      # 下載完成 (success, message)
    
    def __init__(self):
        super().__init__()
        self.sniff_worker: Optional[SniffWorker] = None
        self.download_worker: Optional[DownloadWorker] = None
        
        # 儲存嗅探結果
        self.current_m3u8_url: Optional[str] = None
        self.current_headers: Optional[Dict] = None
        self.current_platform: Optional[str] = None
    
    def start_sniff(self, platform: str, video_url: str, credentials: Optional[Dict] = None):
        """
        開始嗅探影片資訊
        
        Args:
            platform: 'bahamut' 或 'pressplay'
            video_url: 影片頁面 URL
            credentials: 登入憑證 (選填) {'username': ..., 'password': ...}
        """
        # 檢查是否有進行中的任務
        if self.sniff_worker and self.sniff_worker.isRunning():
            self.log_signal.emit("[警告] 嗅探任務進行中，請稍候...")
            return
        
        self.current_platform = platform
        self.log_signal.emit(f"[啟動嗅探器] 平台: {platform}")
        
        # 建立 Worker
        self.sniff_worker = SniffWorker(platform, video_url, credentials)
        
        # 連接訊號
        self.sniff_worker.log_signal.connect(self.log_signal.emit)
        self.sniff_worker.success_signal.connect(self._on_sniff_success)
        self.sniff_worker.failed_signal.connect(self._on_sniff_failed)
        
        # 啟動執行緒
        self.sniff_worker.start()
    
    def _on_sniff_success(self, m3u8_url: str, headers: Dict):
        """嗅探成功回調"""
        self.current_m3u8_url = m3u8_url
        self.current_headers = headers
        
        self.log_signal.emit("[嗅探完成] M3U8 已取得")
        self.sniff_finished_signal.emit(True, "嗅探成功！可以開始下載")
    
    def _on_sniff_failed(self, error_msg: str):
        """嗅探失敗回調"""
        self.log_signal.emit(f"[嗅探失敗] {error_msg}")
        self.sniff_finished_signal.emit(False, error_msg)
    
    def start_download(self, output_path: str, auto_start: bool = True):
        """
        開始下載影片
        
        Args:
            output_path: 輸出檔案路徑 (例如: D:/Videos/video.mp4)
            auto_start: 是否自動開始 (如果為 False，需手動呼叫)
        
        Returns:
            bool: 是否成功啟動下載
        """
        # 檢查是否已完成嗅探
        if not self.current_m3u8_url or not self.current_headers:
            self.log_signal.emit("[錯誤] 請先完成影片嗅探")
            return False
        
        # 檢查是否有進行中的下載
        if self.download_worker and self.download_worker.isRunning():
            self.log_signal.emit("[警告] 下載任務進行中...")
            return False
        
        # 確保輸出目錄存在
        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)
            self.log_signal.emit(f"[建立目錄] {output_dir}")
        
        self.log_signal.emit(f"[啟動下載器] 準備下載到: {output_path}")
        
        # 建立 Worker
        self.download_worker = DownloadWorker(
            platform=self.current_platform,
            m3u8_url=self.current_m3u8_url,
            headers=self.current_headers,
            output_path=output_path
        )
        
        # 連接訊號
        self.download_worker.log_signal.connect(self.log_signal.emit)
        self.download_worker.progress_signal.connect(self.download_progress_signal.emit)
        self.download_worker.finished_signal.connect(self._on_download_finished)
        
        # 啟動執行緒
        if auto_start:
            self.download_worker.start()
            self.log_signal.emit("[下載開始] 執行緒已啟動")
        
        return True
    
    def _on_download_finished(self, success: bool, message: str):
        """下載完成回調"""
        if success:
            self.log_signal.emit("[下載完成] ✅ 影片已儲存")
        else:
            self.log_signal.emit(f"[下載失敗] ❌ {message}")
        
        self.download_finished_signal.emit(success, message)
    
    def cancel_download(self):
        """取消當前下載"""
        if self.download_worker and self.download_worker.isRunning():
            self.log_signal.emit("[取消下載] 正在中止...")
            self.download_worker.cancel()
            self.download_worker.wait()  # 等待執行緒結束
            self.log_signal.emit("[已取消] 下載已停止")
    
    def get_sniff_data(self) -> Optional[Dict]:
        """
        取得嗅探資料 (供外部查詢)
        
        Returns:
            {'m3u8_url': ..., 'headers': {...}, 'platform': ...}
        """
        if self.current_m3u8_url and self.current_headers:
            return {
                'm3u8_url': self.current_m3u8_url,
                'headers': self.current_headers,
                'platform': self.current_platform
            }
        return None
    
    def reset(self):
        """重置狀態 (用於開始新任務)"""
        self.current_m3u8_url = None
        self.current_headers = None
        self.current_platform = None
        self.log_signal.emit("[重置] 管理器已重置")


# ============================================
# 測試範例 (CLI 模式)
# ============================================

def test_bahamut():
    """測試 Bahamut 完整流程"""
    from PySide6.QtWidgets import QApplication
    
    app = QApplication(sys.argv)
    
    manager = CoreManager()
    
    # 連接訊號
    manager.log_signal.connect(lambda msg: print(msg))
    manager.sniff_finished_signal.connect(
        lambda success, msg: print(f"\n[嗅探結果] {'成功' if success else '失敗'}: {msg}\n")
    )
    manager.download_progress_signal.connect(
        lambda cur, tot, status: print(f"[進度] {cur}/{tot} - {status}")
    )
    manager.download_finished_signal.connect(
        lambda success, msg: print(f"\n[下載結果] {'成功' if success else '失敗'}: {msg}\n")
    )
    
    # 嗅探成功後自動下載
    def on_sniff_done(success, msg):
        if success:
            print("\n準備開始下載...")
            manager.start_download("test_bahamut.mp4")
    
    manager.sniff_finished_signal.connect(on_sniff_done)
    
    # 開始嗅探
    bahamut_url = "https://ani.gamer.com.tw/animeVideo.php?sn=xxxxx"
    manager.start_sniff('bahamut', bahamut_url)
    
    sys.exit(app.exec())


def test_pressplay():
    """測試 Pressplay 完整流程"""
    from PySide6.QtWidgets import QApplication
    
    app = QApplication(sys.argv)
    
    manager = CoreManager()
    
    # 連接訊號
    manager.log_signal.connect(lambda msg: print(msg))
    
    def on_sniff_done(success, msg):
        if success:
            print("\n準備開始下載...")
            manager.start_download("test_pressplay.mp4")
    
    manager.sniff_finished_signal.connect(on_sniff_done)
    
    # 開始嗅探
    pressplay_url = "https://pressplay.cc/link/xxxxx"
    manager.start_sniff('pressplay', pressplay_url)
    
    sys.exit(app.exec())


if __name__ == "__main__":
    # 執行測試
    print("選擇測試:")
    print("1. Bahamut")
    print("2. Pressplay")
    choice = input("輸入選擇 (1/2): ")
    
    if choice == "1":
        test_bahamut()
    elif choice == "2":
        test_pressplay()
    else:
        print("無效選擇")