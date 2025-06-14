# thumbnail_util.py
# サムネイル生成: サムネイル生成・キャッシュ管理
import os
import threading
import pickle
from PIL import Image, ImageDraw
from PIL.Image import Resampling
import cv2
from PyQt5.QtGui import QPixmap, QImage
from PyQt5.QtCore import QThread, QCoreApplication
import time

# サムネイルキャッシュファイル名生成
def get_thumb_cache_file(folder):
    if folder is None:
        folder = 'global'
    folder = os.path.abspath(folder)
    import hashlib
    h = hashlib.sha1(folder.encode('utf-8')).hexdigest()[:12]
    return f".thumb_cache_{h}.pkl"

class ThumbnailCache:
    def __init__(self, folder=None, max_items=25000, max_bytes=3*1024*1024*1024):
        self.folder = folder
        self.cache_file = get_thumb_cache_file(folder)
        self.cache = {}  # key: (filepath, size), value: PIL.Image
        self.lock = threading.Lock()
        self.access_times = {}  # key: (filepath, size), value: last access timestamp
        self.max_items = max_items  # 最大エントリ数
        self.max_bytes = max_bytes  # 最大バイト数
        self.total_bytes = 0
        self.load()

    def load(self):
        try:
            with open(self.cache_file, "rb") as f:
                self.cache = pickle.load(f)
            # アクセスタイム初期化
            self.access_times = {k: time.time() for k in self.cache.keys()}
            self.total_bytes = sum(self._estimate_size(v) for v in self.cache.values())
        except Exception:
            self.cache = {}
            self.access_times = {}
            self.total_bytes = 0

    def save(self):
        with self.lock:
            try:
                with open(self.cache_file, "wb") as f:
                    pickle.dump(self.cache, f)
            except Exception:
                pass

    def get(self, key):
        with self.lock:
            v = self.cache.get(key)
            if v is not None:
                self.access_times[key] = time.time()
            return v

    def set(self, key, value):
        with self.lock:
            if key not in self.cache:
                self.total_bytes += self._estimate_size(value)
            else:
                self.total_bytes -= self._estimate_size(self.cache[key])
                self.total_bytes += self._estimate_size(value)
            self.cache[key] = value
            self.access_times[key] = time.time()
            self._cleanup_if_needed()

    def clear(self):
        with self.lock:
            self.cache = {}
            self.access_times = {}
            self.total_bytes = 0

    def _estimate_size(self, img):
        # PIL.Imageのバイトサイズ推定
        try:
            from io import BytesIO
            buf = BytesIO()
            img.save(buf, format="PNG")
            return buf.tell()
        except Exception:
            return 0

    def _cleanup_if_needed(self):
        # 容量・件数制限を超えたら古いものから削除
        while len(self.cache) > self.max_items or self.total_bytes > self.max_bytes:
            # 最も古いアクセスのkeyを削除
            if not self.access_times:
                break
            oldest_key = min(self.access_times, key=self.access_times.get)  # type: ignore
            v = self.cache.pop(oldest_key, None)
            self.access_times.pop(oldest_key, None)
            if v is not None:
                self.total_bytes -= self._estimate_size(v)

# PIL.Image → QPixmap 変換（必ずメインスレッドでのみ呼ぶこと！）
def pil_image_to_qpixmap(img):
    # GUIスレッド以外から呼ばれた場合は例外を投げる
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

# サムネイル生成（PIL.Imageのみ返す。Qtオブジェクトは絶対返さない）
def get_no_thumbnail_image(size=(180, 180)):
    img = Image.new("RGB", size, (60, 60, 60))
    draw = ImageDraw.Draw(img)
    w, h = size
    draw.line((10, 10, w-10, h-10), fill=(200, 80, 80), width=6)
    draw.line((w-10, 10, 10, h-10), fill=(200, 80, 80), width=6)
    draw.rectangle((0, 0, w-1, h-1), outline=(180, 180, 180), width=2)
    return img

def get_image_thumbnail(filepath, size=(180,180), cache=None, defer_queue=None, is_video=False, error_files=None):
    filepath = os.path.abspath(os.path.normpath(filepath))
    key = (filepath, size)
    if cache is not None:
        thumb = cache.get(key)
        if thumb is not None:
            return thumb
    try:
        img = Image.open(filepath).convert("RGB")
        img.thumbnail(size, resample=Resampling.LANCZOS)
        bg = Image.new("RGB", size, (60, 60, 60))  # type: ignore
        offset = ((size[0] - img.width) // 2, (size[1] - img.height) // 2)
        bg.paste(img, offset)
        if cache is not None:
            cache.set(key, bg.copy())
        return bg
    except Exception:
        return None

def get_video_thumbnail(filepath, size=(180,180), error_files=None, cache=None, defer_queue=None):
    filepath = os.path.abspath(os.path.normpath(filepath))
    key = (filepath, size)
    if cache is not None:
        thumb = cache.get(key)
        if thumb is not None:
            return thumb
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
        pil_img.thumbnail(size, resample=Resampling.LANCZOS)
        bg = Image.new("RGB", size, (60, 60, 60))  # type: ignore
        offset = ((size[0] - pil_img.width) // 2, (size[1] - pil_img.height) // 2)
        bg.paste(pil_img, offset)
        if cache is not None:
            cache.set(key, bg.copy())
        return bg
    except Exception as e:
        if error_files is not None:
            error_files.append(f"{filepath} : {e}")
        return None

def get_thumbnail_for_file(filepath, size=(180, 90), error_files=None, cache=None, defer_queue=None):
    filepath = os.path.abspath(os.path.normpath(filepath))
    ext = os.path.splitext(filepath)[1].lower()
    video_exts = ('.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm', '.mpg', '.mpeg', '.3gp')
    if ext in video_exts:
        return get_video_thumbnail(filepath, size, error_files, cache, None)
    else:
        return get_image_thumbnail(filepath, size, cache, None, is_video=False, error_files=error_files)

# サムネイル生成ワーカー（PIL.Imageのみ扱う。Qtオブジェクトは絶対扱わない）
class ThumbnailWorker(threading.Thread):
    def __init__(self, q, update_cb, cache=None):
        super().__init__(daemon=True)
        self.q = q
        self.update_cb = update_cb
        self.cache = cache
    def run(self):
        print("[DEBUG] ThumbnailWorker.run: start")
        while True:
            item = self.q.get()
            print(f"[DEBUG] ThumbnailWorker.run: got item {item}")
            if item is None:
                break
            try:
                path, size, is_video, error_files = item
            except Exception as e:
                print(f"[ERROR] ThumbnailWorker.run: failed to unpack item {item}: {e}")
                self.q.task_done()
                continue
            try:
                if is_video:
                    pil_img = get_video_thumbnail(path, size, error_files, self.cache)
                else:
                    pil_img = get_image_thumbnail(path, size, self.cache)
                print(f"[DEBUG] ThumbnailWorker.run: generated thumbnail for {path}")
            except Exception as e:
                print(f"[DEBUG] ThumbnailWorker.run: Exception for {path}: {e}")
                pil_img = None
            self.update_cb(path, pil_img)
            self.q.task_done()

def start_thumbnail_workers(q, update_cb, cache=None, num_workers=4):
    """
    サムネイル生成ワーカーを複数スレッドで起動する。
    q: Queueインスタンス
    update_cb: サムネイル生成後のコールバック
    cache: サムネイルキャッシュ
    num_workers: 起動するワーカースレッド数
    戻り値: [ThumbnailWorker, ...]
    """
    workers = []
    for _ in range(num_workers):
        worker = ThumbnailWorker(q, update_cb, cache)
        worker.start()
        workers.append(worker)
    return workers

def load_thumb_cache(folder=None):
    """
    サムネイルキャッシュを指定フォルダでロードし、ThumbnailCacheインスタンスを返す。
    """
    cache = ThumbnailCache(folder)
    cache.load()
    return cache

def save_thumb_cache(cache):
    """
    サムネイルキャッシュを保存する。
    """
    cache.save()

def clear_thumbnail_cache(cache_or_folder=None):
    """
    サムネイルキャッシュをクリアし、ファイルにも反映する。
    cache_or_folder: ThumbnailCacheインスタンス または フォルダパス/None
    """
    if cache_or_folder is None:
        cache = ThumbnailCache(None)
    elif isinstance(cache_or_folder, ThumbnailCache):
        cache = cache_or_folder
    else:
        cache = ThumbnailCache(cache_or_folder)
    cache.clear()
    cache.save()
