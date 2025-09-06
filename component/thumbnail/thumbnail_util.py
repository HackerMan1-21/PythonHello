import os
import threading
import pickle
from PIL import Image, ImageDraw
from PIL.Image import Resampling
import cv2
from PyQt5.QtGui import QPixmap, QImage
from PyQt5.QtCore import QThread, QCoreApplication
import time

def get_thumb_cache_file(folder):
    if folder is None:
        folder = 'global'
    folder = os.path.abspath(folder)
    import hashlib
    h = hashlib.sha1(folder.encode('utf-8')).hexdigest()[:12]
    return f".thumb_cache_{h}.pkl"

class ThumbnailCache:
    def __init__(self):
        self.cache = {}

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

def get_no_thumbnail_image(size=(180, 180)):
    img = Image.new("RGB", size, (60, 60, 60))
    draw = ImageDraw.Draw(img)
    w, h = size
    draw.line((10, 10, w - 10, h - 10), fill=(200, 80, 80), width=6)
    draw.line((w - 10, 10, 10, h - 10), fill=(200, 80, 80), width=6)
    draw.rectangle((0, 0, w - 1, h - 1), outline=(180, 180, 180), width=2)
    return img

def get_image_thumbnail(filepath, size=(180, 180)):
    try:
        img = Image.open(filepath)
        img.thumbnail(size, resample=Resampling.NEAREST)
        # 背景に合わせてセンタリング
        bg = Image.new("RGB", size, (60, 60, 60))
        offset = ((size[0] - img.width) // 2, (size[1] - img.height) // 2)
        bg.paste(img, offset)
        return bg
    except Exception:
        return get_no_thumbnail_image(size)

def get_video_thumbnail(filepath, size=(180, 180)):
    try:
        cap = cv2.VideoCapture(filepath)
        ret, frame = cap.read()
        cap.release()
        if ret:
            img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(img)
            pil_img.thumbnail(size, resample=Resampling.NEAREST)
            # 背景に合わせてセンタリング
            bg = Image.new("RGB", size, (60, 60, 60))
            offset = ((size[0] - pil_img.width) // 2, (size[1] - pil_img.height) // 2)
            bg.paste(pil_img, offset)
            return bg
    except Exception:
        pass
    return get_no_thumbnail_image(size)

def get_thumbnail_for_file(filepath, size=(180, 180)):
    ext = os.path.splitext(filepath)[1].lower()
    # 画像・動画の拡張子を明確に区別
    image_exts = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff')
    video_exts = ('.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm', '.mpg', '.mpeg', '.3gp')
    
    if ext in video_exts:
        return get_video_thumbnail(filepath, size)
    elif ext in image_exts:
        return get_image_thumbnail(filepath, size)
    else:
        return get_no_thumbnail_image(size)

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
            try:
                path, size = item
                result = get_thumbnail_for_file(path, size)
                self.update_cb(path, result)
            except Exception as e:
                print(f"[ThumbnailWorker] Error processing {path}: {e}")
                self.update_cb(path, get_no_thumbnail_image(size))
            finally:
                self.q.task_done()

def start_thumbnail_workers(q, update_cb, num_workers=2):
    workers = []
    for _ in range(num_workers):
        worker = ThumbnailWorker(q, update_cb)
        worker.start()
        workers.append(worker)
    return workers

def load_thumb_cache():
    return ThumbnailCache()

def save_thumb_cache(cache):
    pass

def clear_thumbnail_cache():
    pass