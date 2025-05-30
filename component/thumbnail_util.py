"""
thumbnail_util.py
画像・動画のサムネイル生成・キャッシュ・非同期処理のユーティリティ。

主な機能:
- 画像/動画のサムネイル生成（PIL, OpenCV）
- サムネイルキャッシュ管理（メモリ・ファイル）
- サムネイル取得の型変換統一
- エラー処理・キャッシュ自動/手動クリーンアップ

依存:
- Pillow, OpenCV, numpy, pickle, threading
"""

import os
import threading
from PIL import Image
import cv2
import numpy as np
import pickle

thumb_cache = {}
thumb_cache_lock = threading.Lock()

def get_thumb_cache_file(folder):
    if folder is None:
        folder = 'global'
    folder = os.path.abspath(folder)
    import hashlib
    h = hashlib.sha1(folder.encode('utf-8')).hexdigest()[:12]
    return f".thumb_cache_{h}.pkl"

def load_thumb_cache(folder=None):
    global thumb_cache
    try:
        cache_file = get_thumb_cache_file(folder)
        with open(cache_file, "rb") as f:
            thumb_cache.update(pickle.load(f))
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

def get_thumbnail_for_file(filepath, size=(120, 90), error_files=None):
    ext = os.path.splitext(filepath)[1].lower()
    video_exts = ('.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm', '.mpg', '.mpeg', '.3gp')
    if ext in video_exts:
        return get_video_thumbnail(filepath, size, error_files)
    else:
        return get_image_thumbnail(filepath, size)
