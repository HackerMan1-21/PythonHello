"""
thumbnail_optimizer.py
サムネイル表示最適化システム
"""

import threading
from queue import Queue, PriorityQueue
import time
from PIL import Image

class SmartThumbnailManager:
    def __init__(self, max_workers=4, cache_size=1000):
        self.max_workers = max_workers
        self.cache_size = cache_size
        self.priority_queue = PriorityQueue()
        self.cache = {}
        self.workers = []
        self.running = True
        
        for _ in range(max_workers):
            worker = threading.Thread(target=self._worker_loop)
            worker.daemon = True
            worker.start()
            self.workers.append(worker)
    
    def _worker_loop(self):
        """ワーカースレッドのメインループ"""
        while self.running:
            try:
                priority, timestamp, file_path, callback = self.priority_queue.get(timeout=1)
                
                if file_path in self.cache:
                    callback(file_path, self.cache[file_path])
                else:
                    thumbnail = self._generate_thumbnail(file_path)
                    if thumbnail:
                        self._update_cache(file_path, thumbnail)
                        callback(file_path, thumbnail)
                
                self.priority_queue.task_done()
            except:
                continue
    
    def _generate_thumbnail(self, file_path):
        """サムネイル生成"""
        try:
            if file_path.lower().endswith(('.mp4', '.avi', '.mov', '.mkv')):
                import cv2
                cap = cv2.VideoCapture(file_path)
                ret, frame = cap.read()
                cap.release()
                if ret:
                    frame = cv2.resize(frame, (180, 135))
                    return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            else:
                img = Image.open(file_path)
                img.thumbnail((180, 135))
                return img
        except:
            return None
    
    def _update_cache(self, file_path, thumbnail):
        """キャッシュ更新（LRU）"""
        if len(self.cache) >= self.cache_size:
            oldest_key = next(iter(self.cache))
            del self.cache[oldest_key]
        self.cache[file_path] = thumbnail
    
    def request_thumbnail(self, file_path, callback, priority=1):
        """サムネイル要求（優先度付き）"""
        timestamp = time.time()
        self.priority_queue.put((priority, timestamp, file_path, callback))
    
    def request_batch(self, file_paths, callback, priority=1):
        """バッチ要求"""
        for file_path in file_paths:
            self.request_thumbnail(file_path, callback, priority)
    
    def shutdown(self):
        """シャットダウン"""
        self.running = False
        for worker in self.workers:
            worker.join(timeout=1)