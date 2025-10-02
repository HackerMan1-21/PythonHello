import os
import threading
import sqlite3
import hashlib
from typing import cast
from PIL import Image, ImageDraw
from PIL.Image import Resampling
import cv2
from PyQt5.QtGui import QPixmap, QImage, QIcon
from PyQt5.QtCore import QThread, QCoreApplication, QTimer, QSize
import time
import queue
import concurrent.futures
import multiprocessing

class FastCache:
    def __init__(self, cache_dir=".thumb_cache"):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self.db_path = os.path.join(cache_dir, "thumbs.db")
        self._init_db()
        self.memory_cache = {}
        self.cache = self.memory_cache  # 後方互換性
        self.max_memory = 1000
        
        # pHashキャッシュ用DB初期化
        self.phash_db_path = os.path.join(cache_dir, "phash.db")
        self._init_phash_db()
    
    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS thumbs (
                path_hash TEXT PRIMARY KEY,
                path TEXT,
                mtime REAL,
                data BLOB
            )""")
    
    def _init_phash_db(self):
        with sqlite3.connect(self.phash_db_path) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS hash_cache (
                file_path TEXT PRIMARY KEY,
                file_size INTEGER,
                modified_time REAL,
                phash TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_phash ON hash_cache(phash)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_file_path ON hash_cache(file_path)")
    
    def _get_hash(self, path):
        return hashlib.md5(path.encode()).hexdigest()
    
    def get(self, path):
        if path in self.memory_cache:
            return self.memory_cache[path]
        
        try:
            mtime = os.path.getmtime(path)
            path_hash = self._get_hash(path)
            
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT data FROM thumbs WHERE path_hash=? AND mtime=?",
                    (path_hash, mtime)
                ).fetchone()
                
                if row:
                    import pickle
                    thumb = pickle.loads(row[0])
                    if len(self.memory_cache) < self.max_memory:
                        self.memory_cache[path] = thumb
                    return thumb
        except:
            pass
        return None
    
    def set(self, path, thumb):
        try:
            mtime = os.path.getmtime(path)
            path_hash = self._get_hash(path)
            
            import pickle
            data = pickle.dumps(thumb)
            
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO thumbs (path_hash, path, mtime, data) VALUES (?, ?, ?, ?)",
                    (path_hash, path, mtime, data)
                )
            
            if len(self.memory_cache) < self.max_memory:
                self.memory_cache[path] = thumb
        except:
            pass
    
    def get_phash(self, file_path):
        try:
            stat = os.stat(file_path)
            file_size = stat.st_size
            mtime = stat.st_mtime
            
            with sqlite3.connect(self.phash_db_path) as conn:
                row = conn.execute(
                    "SELECT phash FROM hash_cache WHERE file_path=? AND file_size=? AND modified_time=?",
                    (file_path, file_size, mtime)
                ).fetchone()
                
                if row:
                    print(f"[pHash cache HIT] {file_path}")
                    return row[0]
        except:
            pass
        print(f"[pHash cache MISS] {file_path}")
        return None
    
    def set_phash(self, file_path, phash_str):
        try:
            stat = os.stat(file_path)
            file_size = stat.st_size
            mtime = stat.st_mtime
            
            with sqlite3.connect(self.phash_db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO hash_cache (file_path, file_size, modified_time, phash) VALUES (?, ?, ?, ?)",
                    (file_path, file_size, mtime, phash_str)
                )
        except:
            pass
    
    def clear(self):
        self.memory_cache.clear()
        try:
            os.remove(self.db_path)
            os.remove(self.phash_db_path)
            self._init_db()
            self._init_phash_db()
        except:
            pass

def pil_image_to_qpixmap(img):
    app = QCoreApplication.instance()
    if app is not None and QThread.currentThread() != app.thread():
        raise RuntimeError("pil_image_to_qpixmapは必ずGUIスレッドで呼んでください")
    if img is None:
        return None
    if img.mode != "RGB":
        img = img.convert("RGB")
    data = img.tobytes("raw", "RGB")
    qimg = QImage(data, img.width, img.height, QImage.Format_RGB888)
    return QPixmap.fromImage(qimg)

def get_placeholder_image(size=(180, 180)):
    img = Image.new("RGB", size, color=cast(int, (40, 40, 40)))  # type: ignore[arg-type]
    draw = ImageDraw.Draw(img)
    w, h = size
    center_x, center_y = w // 2, h // 2
    draw.ellipse((center_x-20, center_y-20, center_x+20, center_y+20), outline=(100, 100, 100), width=2)
    draw.text((center_x-15, center_y-5), "...", fill=(150, 150, 150))
    return img

def get_no_thumbnail_image(size=(180, 180)):
    img = Image.new("RGB", size, color=cast(int, (60, 60, 60)))  # type: ignore[arg-type]
    draw = ImageDraw.Draw(img)
    w, h = size
    draw.line((10, 10, w - 10, h - 10), fill=(200, 80, 80), width=6)
    draw.line((w - 10, 10, 10, h - 10), fill=(200, 80, 80), width=6)
    draw.rectangle((0, 0, w - 1, h - 1), outline=(180, 180, 180), width=2)
    return img

def get_thumbnail_for_file(filepath, size=(180, 180), cache=None):
    if cache:
        cached = cache.get(filepath)
        if cached:
            return cached
    
    try:
        # ファイル存在チェック
        if not os.path.exists(filepath):
            return get_no_thumbnail_image(size)
        
        ext = os.path.splitext(filepath)[1].lower()
        
        if ext in ('.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm'):
            cap = cv2.VideoCapture(filepath)
            if not cap.isOpened():
                cap.release()
                return get_no_thumbnail_image(size)
            
            # 複数フレームを試行
            ret = False
            frame = None
            for frame_pos in [0, 30, 60]:
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_pos)
                ret, frame = cap.read()
                if ret and frame is not None and frame.size > 0:
                    break
            
            cap.release()
            
            if ret and frame is not None:
                # フレームの有効性チェック
                if frame.shape[0] > 0 and frame.shape[1] > 0:
                    img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    pil_img = Image.fromarray(img)
                    pil_img.thumbnail(size, Resampling.LANCZOS)
                    result = Image.new("RGB", size, color=cast(int, (40, 40, 40)))  # type: ignore[arg-type]
                    offset = ((size[0] - pil_img.width) // 2, (size[1] - pil_img.height) // 2)
                    result.paste(pil_img, offset)
                    if cache:
                        cache.set(filepath, result)
                    return result
                    
        elif ext in ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'):
            with Image.open(filepath) as img:
                # 画像の有効性チェック
                if img.size[0] > 0 and img.size[1] > 0:
                    img.thumbnail(size, Resampling.LANCZOS)
                    result = Image.new("RGB", size, color=cast(int, (40, 40, 40)))  # type: ignore[arg-type]
                    offset = ((size[0] - img.width) // 2, (size[1] - img.height) // 2)
                    result.paste(img, offset)
                    if cache:
                        cache.set(filepath, result)
                    return result
    except Exception as e:
        print(f"[THUMB ERROR] {filepath}: {e}")
    
    return get_no_thumbnail_image(size)

class BatchThumbnailWorker:
    def __init__(self, cache=None, max_workers=None):
        self.cache = cache or FastCache()
        # 大規模データではワーカー数を制限
        self.max_workers = max_workers or min(4, multiprocessing.cpu_count())
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers)
        self.processed_count = 0
        self.error_count = 0
    
    def process_batch(self, paths, size, callback):
        print(f"[THUMB] バッチ処理開始: {len(paths)}ファイル")
        
        def process_single(path: str):
            try:
                thumb = get_thumbnail_for_file(path, size, self.cache)
                if thumb is None:
                    self.error_count += 1
                    return path, get_no_thumbnail_image(size)
                self.processed_count += 1
                return path, thumb
            except Exception as e:
                print(f"[THUMB ERROR] {os.path.basename(path)}: {e}")
                self.error_count += 1
                return path, get_no_thumbnail_image(size)
        
        # 大規模データではバッチサイズを制限
        batch_size = 50 if len(paths) > 1000 else len(paths)
        
        for i in range(0, len(paths), batch_size):
            batch = paths[i:i+batch_size]
            futures = [self.executor.submit(process_single, path) for path in batch]
            
            for future in concurrent.futures.as_completed(futures):
                try:
                    path, thumb = future.result(timeout=30)  # 30秒タイムアウト
                    callback(path, thumb)
                except concurrent.futures.TimeoutError as timeout_err:
                    # pathが未定義の場合のフォールバック
                    print(f"[THUMB TIMEOUT] {timeout_err}")
                except Exception as e:
                    print(f"[THUMB CALLBACK ERROR] {e}")
        
        print(f"[THUMB] 処理完了: 成功{self.processed_count}, エラー{self.error_count}")
    
    def shutdown(self):
        print(f"[THUMB] シャットダウン: 成功{self.processed_count}, エラー{self.error_count}")
        self.executor.shutdown(wait=False)

def start_thumbnail_workers(q, update_cb, num_workers=None, cache=None):
    return BatchThumbnailWorker(cache, num_workers)

class VirtualThumbnailManager:
    def __init__(self, gui_instance):
        self.gui = gui_instance
        self.worker = BatchThumbnailWorker(FastCache())
        self.pending = set()
    
    def load_visible_batch(self, paths):
        visible_paths = [p for p in paths if p not in self.pending]
        if not visible_paths:
            return
        
        self.pending.update(visible_paths)
        
        # プレースホルダー即座表示
        placeholder = get_placeholder_image((180, 180))
        placeholder_pix = pil_image_to_qpixmap(placeholder)
        
        for path in visible_paths:
            norm_path = os.path.abspath(os.path.normpath(path))
            btn = self.gui.thumb_widget_map.get(norm_path)
            if btn:
                btn.setIcon(QIcon(placeholder_pix))
                btn.setIconSize(QSize(180, 180))
        
        # バッチ処理
        def update_callback(path, thumb):
            norm_path = os.path.abspath(os.path.normpath(path))
            btn = self.gui.thumb_widget_map.get(norm_path)
            if btn and thumb:
                QTimer.singleShot(0, lambda: self._update_ui(btn, thumb))
            self.pending.discard(path)
        
        self.worker.process_batch(visible_paths, (180, 180), update_callback)
    
    def _update_ui(self, btn, thumb):
        try:
            pix = pil_image_to_qpixmap(thumb)
            btn.setIcon(QIcon(pix))
        except:
            pass

# 後方互換性
ThumbnailCache = FastCache

def load_thumb_cache(folder=None):
    return FastCache()

def save_thumb_cache(cache):
    pass

def clear_thumbnail_cache():
    pass

def clear_queue(q):
    while not q.empty():
        try:
            q.get_nowait()
        except:
            break