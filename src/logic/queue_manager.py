# src/logic/queue_manager.py
from collections import deque
from typing import Dict, Any, Optional
from PySide6.QtCore import QObject, Signal
from src.logic.downloader import DownloadWorker

class QueueManager(QObject):
    """
    管理下載佇列與並發控制的核心邏輯單元 (The Brain)。
    """
    # 定義轉發給 UI 的訊號
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
        
        # 等待佇列: 存放 {'id': str, 'url': str, 'config': dict}
        self.waiting_queue = deque()
        
        # 活躍工作者: {'task_id': DownloadWorker_Instance}
        self.active_workers: Dict[str, DownloadWorker] = {}
        
        # 系統狀態標記
        self.is_processing = False

    def add_task(self, task_id: str, url: str, config: Dict[str, Any]):
        """
        UI 呼叫此方法將任務加入排程
        """
        task = {
            'id': task_id, 
            'url': url, 
            'config': config
        }
        self.waiting_queue.append(task)
        self.task_status_changed.emit(task_id, "等待中")
        
        # 如果已經在處理中，且有空位，嘗試啟動
        if self.is_processing:
            self._schedule_next()

    def start_processing(self):
        """
        開始執行佇列 (點擊 '開始下載' 按鈕)
        """
        if not self.is_processing:
            self.is_processing = True
            self.queue_started.emit()
            self._schedule_next()

    def stop_processing(self):
        """
        暫停佇列 (不會中斷正在下載的任務，只是不再啟動新的)
        """
        self.is_processing = False

    def cancel_task(self, task_id: str):
        """
        取消特定任務 (無論是在佇列中還是在執行中)
        """
        # 1. 檢查是否在活躍列表
        if task_id in self.active_workers:
            worker = self.active_workers[task_id]
            worker.stop()
            worker.wait() # 等待執行緒安全結束
            # 移除邏輯由 _on_worker_finished 處理，或者手動觸發
            # 這裡簡單處理：
            del self.active_workers[task_id]
            self.task_status_changed.emit(task_id, "已取消")
            self._schedule_next() # 補位
            return

        # 2. 檢查是否在等待佇列
        # deque 不容易直接刪除中間元素，需重建 (效率尚可，因為佇列通常不長)
        original_len = len(self.waiting_queue)
        self.waiting_queue = deque([t for t in self.waiting_queue if t['id'] != task_id])
        
        if len(self.waiting_queue) < original_len:
            self.task_status_changed.emit(task_id, "已移除")

    def _schedule_next(self):
        """
        核心調度邏輯：檢查是否有空位並啟動下一個任務
        """
        if not self.is_processing:
            return

        # 檢查併發限制
        while len(self.active_workers) < self.max_concurrent and self.waiting_queue:
            task = self.waiting_queue.popleft()
            self._start_worker(task)

        # 如果沒有活躍任務且佇列為空，發送完成訊號
        if not self.active_workers and not self.waiting_queue:
            self.is_processing = False
            self.queue_finished.emit()

    def _start_worker(self, task: Dict[str, Any]):
        """
        實例化並啟動 Worker
        """
        task_id = task['id']
        worker = DownloadWorker(task_id, task['url'], task['config'])
        
        # 連接訊號
        worker.signals.progress.connect(self.task_progress_updated)
        worker.signals.status.connect(self.task_status_changed)
        worker.signals.error.connect(self._on_worker_error)
        worker.signals.finished.connect(self._on_worker_finished)
        
        # 啟動並存入活躍列表
        self.active_workers[task_id] = worker
        worker.start()

    def _on_worker_finished(self, task_id: str):
        """
        Worker 完成時的回調
        """
        if task_id in self.active_workers:
            # 資源清理
            worker = self.active_workers.pop(task_id)
            worker.deleteLater() # 確保 Qt 清理記憶體
            
        self.task_completed.emit(task_id)
        self._schedule_next() # 觸發補位

    def _on_worker_error(self, task_id: str, error_msg: str):
        """
        Worker 報錯時的回調
        """
        if task_id in self.active_workers:
            worker = self.active_workers.pop(task_id)
            worker.deleteLater()
            
        self.task_error_occurred.emit(task_id, error_msg)
        self._schedule_next() # 即使錯誤也要繼續下一個