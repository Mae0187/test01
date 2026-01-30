# src/logic/queue_manager.py
# [VibeCoding] Phase 78: Signal Adaptor & ThreadPool Fix
# 修正重點：
# 1. 增加 Slot 轉接層，解決參數數量不一致導致的 RuntimeError
# 2. 改用 QThreadPool 啟動 QRunnable
# 3. 移除不存在的 status 訊號連接

from collections import deque
from typing import Dict, Any
from PySide6.QtCore import QObject, Signal, Slot, QThreadPool
from src.logic.downloader import DownloadWorker

class QueueManager(QObject):
    """
    管理下載佇列與並發控制的核心邏輯單元 (The Brain)。
    """
    # --- 定義轉發給 UI 的訊號 (保持不變，讓 UI 不需要改) ---
    # task_id, status_msg
    task_status_changed = Signal(str, str)
    
    # task_id, progress_str, percent_float, speed, eta
    task_progress_updated = Signal(str, str, float, str, str)
    
    # task_id, error_msg
    task_error_occurred = Signal(str, str)
    
    # task_id (完成)
    task_completed = Signal(str)
    
    # 佇列整體狀態訊號
    queue_started = Signal()
    queue_finished = Signal()

    def __init__(self, max_concurrent: int = 2):
        super().__init__()
        self.max_concurrent = max_concurrent
        
        # 建立執行緒池 (QRunnable 必須跑在這裡)
        self.thread_pool = QThreadPool.globalInstance()
        self.thread_pool.setMaxThreadCount(max_concurrent)
        
        # 等待佇列: 存放 {'id': str, 'url': str, 'config': dict}
        self.waiting_queue = deque()
        
        # 活躍工作者: {'task_id': DownloadWorker_Instance}
        # 用於保存 Python 物件引用，以便能執行 cancel()
        self.active_workers: Dict[str, DownloadWorker] = {}
        
        # 系統狀態標記
        self.is_processing = False

    def add_task(self, task_id: str, url: str, config: Dict[str, Any]):
        """UI 呼叫此方法將任務加入排程"""
        task = {
            'id': task_id, 
            'url': url, 
            'config': config
        }
        self.waiting_queue.append(task)
        self.task_status_changed.emit(task_id, "等待中")
        
        if self.is_processing:
            self._schedule_next()

    def start_processing(self):
        """開始執行佇列"""
        if not self.is_processing:
            self.is_processing = True
            self.queue_started.emit()
            self._schedule_next()

    def stop_processing(self):
        """暫停佇列"""
        self.is_processing = False

    def cancel_task(self, task_id: str):
        """取消特定任務"""
        # 1. 檢查是否在活躍列表 (正在下載)
        if task_id in self.active_workers:
            worker = self.active_workers[task_id]
            worker.cancel() # 呼叫 Worker 內部的旗標
            # 這裡不刪除 active_workers，讓 worker 結束時發出的訊號來觸發清理
            self.task_status_changed.emit(task_id, "正在取消...")
            return

        # 2. 檢查是否在等待佇列
        original_len = len(self.waiting_queue)
        self.waiting_queue = deque([t for t in self.waiting_queue if t['id'] != task_id])
        
        if len(self.waiting_queue) < original_len:
            self.task_status_changed.emit(task_id, "已移除")

    def _schedule_next(self):
        """核心調度邏輯"""
        if not self.is_processing:
            return

        # 檢查併發限制 (注意：這裡改用 active_workers 的長度來判斷)
        while len(self.active_workers) < self.max_concurrent and self.waiting_queue:
            task = self.waiting_queue.popleft()
            self._start_worker(task)

        # 如果沒有活躍任務且佇列為空
        if not self.active_workers and not self.waiting_queue:
            self.is_processing = False
            self.queue_finished.emit()

    def _start_worker(self, task: Dict[str, Any]):
        """實例化並啟動 Worker"""
        task_id = task['id']
        
        # 1. 建立 Worker (必須確保傳入 3 個參數)
        worker = DownloadWorker(task_id, task['url'], task['config'])
        
        # 2. 連接訊號到內部的「轉接 Slot」
        # 注意：這裡不能直接連 UI，因為參數格式不同
        worker.signals.progress.connect(self._handle_worker_progress)
        worker.signals.finished.connect(self._handle_worker_finished)
        worker.signals.error.connect(self._handle_worker_error)
        worker.signals.cancelled.connect(self._handle_worker_cancelled)
        
        # 3. 存入活躍列表
        self.active_workers[task_id] = worker
        
        # 4. 正確啟動 QRunnable 的方式
        self.thread_pool.start(worker)

    # --- 訊號轉接層 (Signal Adaptors) ---
    # 這裡負責把 Worker 的 3 參數轉成 UI 需要的 5 參數

    @Slot(str, int, str)
    def _handle_worker_progress(self, task_id, percentage, message):
        """處理進度訊號"""
        # 轉發給 UI (補齊 speed 和 eta 為空字串，因為 yt-dlp 訊號還沒拆分這些)
        self.task_progress_updated.emit(task_id, message, float(percentage), "", "")
        
        # 可選：如果進度是 0，也可以更新一下狀態文字
        if percentage == 0:
            self.task_status_changed.emit(task_id, message)

    @Slot(str, bool, str)
    def _handle_worker_finished(self, task_id, success, result):
        """處理完成訊號"""
        if task_id in self.active_workers:
            del self.active_workers[task_id] # 從活躍列表移除
            
        if success:
            self.task_completed.emit(task_id)
            self.task_status_changed.emit(task_id, "下載完成")
        else:
            self.task_error_occurred.emit(task_id, "下載失敗")
            
        self._schedule_next() # 觸發補位

    @Slot(str, str)
    def _handle_worker_error(self, task_id, error_msg):
        """處理錯誤訊號"""
        if task_id in self.active_workers:
            del self.active_workers[task_id]
            
        self.task_error_occurred.emit(task_id, error_msg)
        self._schedule_next()

    @Slot(str)
    def _handle_worker_cancelled(self, task_id):
        """處理取消訊號"""
        if task_id in self.active_workers:
            del self.active_workers[task_id]
            
        self.task_status_changed.emit(task_id, "已取消")
        self._schedule_next()