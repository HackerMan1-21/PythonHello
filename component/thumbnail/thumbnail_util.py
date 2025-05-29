# thumbnail_util.py
# サムネイル生成: サムネイル生成・キャッシュ管理
import os
import threading
import pickle
from PIL import Image
import cv2

# サムネイルキャッシュファイル名生成
def get_thumb_cache_file(folder):
    if folder is None:
        folder = 'global'
    folder = os.path.abspath(folder)
    import hashlib
    h = hashlib.sha1(folder.encode('utf-8')).hexdigest()[:12]
    return f".thumb_cache_{h}.pkl"

thumb_cache = {}
thumb_cache_lock = threading.Lock()

def load_thumb_cache(folder=None):
    global thumb_cache
    try:
        cache_file = get_thumb_cache_file(folder)
        with open(cache_file, "rb") as f:
            thumb_cache = pickle.load(f)
    except Exception:
        thumb_cache = {}

def save_thumb_cache(folder=None):
    with thumb_cache_lock:
        try:
            cache_file = get_thumb_cache_file(folder)
            with open(cache_file, "wb") as f:
                pickle.dump(thumb_cache, f)
        except Exception:
            pass

def get_image_thumbnail(filepath, size=(240,240)):
    key = (filepath, size)
    with thumb_cache_lock:
        if key in thumb_cache:
            return thumb_cache[key]
    try:
        img = Image.open(filepath).convert("RGB")
        img.thumbnail(size, Image.LANCZOS)
        with thumb_cache_lock:
            thumb_cache[key] = img.copy()
        return img
    except Exception:
        return None

def get_video_thumbnail(filepath, size=(240,240), error_files=None):
    key = (filepath, size)
    with thumb_cache_lock:
        if key in thumb_cache:
            return thumb_cache[key]
    try:
        cap = cv2.VideoCapture(filepath)
        ret, frame = cap.read()
        cap.release()
        if not ret:
            if error_files is not None:
                error_files.append(f"{filepath} : 動画フレーム取得失敗")
            return None
        img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(img)
        pil_img.thumbnail(size, Image.LANCZOS)
        with thumb_cache_lock:
            thumb_cache[key] = pil_img.copy()
        return pil_img
    except Exception as e:
        if error_files is not None:
            error_files.append(f"{filepath} : {e}")
        return None

class ThumbnailWorker(threading.Thread):
    def __init__(self, q, update_cb):
        super().__init__(daemon=True)
        self.q = q
        self.update_cb = update_cb
    def run(self):
        while True:
            item = self.q.get()
            if item is None:
                break
            path, size, is_video, error_files = item
            if is_video:
                get_video_thumbnail(path, size, error_files)
            else:
                get_image_thumbnail(path, size)
            self.update_cb(path)
            self.q.task_done()
