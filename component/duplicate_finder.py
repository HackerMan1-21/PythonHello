"""
duplicate_finder.py
ファイル/動画/画像の重複判定・グループ化ユーティリティ。

主な機能:
- 画像/動画のpHash計算
- キャッシュ利用による高速化
- 重複グループの検出

依存:
- imagehash, OpenCV, numpy, Pillow, os, pickle
"""

# 重複検査: ファイル/動画/画像の重複判定・グループ化
import os
import imagehash
import cv2
import numpy as np
from PIL import Image
import hashlib
import pickle
from component.utils.cache_util import save_cache, load_cache
from component.utils.file_util import normalize_path

def get_image_phash(filepath, folder=None, cache=None):
    filepath = normalize_path(filepath)
    def calc_func(path):
        try:
            img = Image.open(path).convert("RGB")
            return imagehash.phash(img)
        except Exception:
            return None
    if cache is not None:
        if filepath in cache:
            return cache[filepath]
        val = calc_func(filepath)
        cache[filepath] = val
        return val
    return get_features_with_cache(filepath, calc_func, folder)

def get_video_phash(filepath, frame_count=7, folder=None, cache=None):
    filepath = normalize_path(filepath)
    def calc_func(path):
        cap = cv2.VideoCapture(path)
        length = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        hashes = []
        if length == 0 or frame_count == 0:
            cap.release()
            return None
        indices = set([0, length-1, length//2])
        if frame_count > 3:
            for i in range(frame_count-3):
                idx = int(length * (i+1)/(frame_count-2))
                indices.add(min(max(0, idx), length-1))
        indices = sorted(indices)
        for frame_no in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
            ret, frame = cap.read()
            if not ret:
                continue
            try:
                img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                pil_img = Image.fromarray(img)
                hash_val = imagehash.phash(pil_img)
                hashes.append(hash_val)
            except Exception:
                continue
        cap.release()
        if not hashes:
            return None
        return hashes
    if cache is not None:
        if filepath in cache:
            return cache[filepath]
        val = calc_func(filepath)
        cache[filepath] = val
        return val
    return get_features_with_cache(filepath, calc_func, folder)

def get_cache_files(folder):
    folder = os.path.abspath(folder)
    h = hashlib.sha1(folder.encode('utf-8')).hexdigest()[:12]
    cache_file = f".video_cache_{h}.enc"
    key_file = f".video_cache_{h}.key"
    return cache_file, key_file

def get_features_with_cache(filepath, calc_func, folder=None):
    filepath = normalize_path(filepath)
    if folder is None:
        folder = os.path.dirname(filepath)
    cache_file, key_file = get_cache_files(folder)
    cache = None
    for i in range(5):
        try:
            cache_bytes = load_cache(cache_file, key_file)
            if cache_bytes is not None:
                try:
                    cache = pickle.loads(cache_bytes)
                    break
                except Exception as e:
                    if i == 4:
                        try:
                            os.remove(cache_file)
                        except Exception:
                            pass
                        cache = {}
                        break
                    import time
                    time.sleep(0.3)
                    continue
            else:
                cache = {}
                break
        except FileNotFoundError:
            cache = {}
            break
        except Exception:
            cache = {}
            break
    if cache is None:
        cache = {}
    if filepath in cache:
        return cache[filepath]
    result = calc_func(filepath)
    if result is not None:
        cache[filepath] = result
        for _ in range(5):
            try:
                save_cache(cache_file, pickle.dumps(cache))
                break
            except Exception:
                import time
                time.sleep(0.2)
    return result

def group_by_phash(file_hashes, threshold=8):
    groups = []
    used = set()
    for i, (f1, h1) in enumerate(file_hashes):
        if f1 in used or h1 is None:
            continue
        group = [f1]
        for j, (f2, h2) in enumerate(file_hashes):
            if i != j and f2 not in used and h2 is not None:
                if isinstance(h1, list) and isinstance(h2, list):
                    minlen = min(len(h1), len(h2))
                    try:
                        dist = sum(h1[k] - h2[k] if hasattr(h1[k], '__sub__') else abs(int(h1[k]) - int(h2[k])) for k in range(minlen))
                        dist = abs(dist)
                    except Exception:
                        continue
                    if dist < threshold * minlen:
                        group.append(f2)
                        used.add(f2)
                elif not isinstance(h1, list) and not isinstance(h2, list):
                    try:
                        if hasattr(h1, '__sub__') and hasattr(h2, '__sub__'):
                            diff = abs(h1 - h2)
                        else:
                            diff = abs(int(h1) - int(h2))
                        if diff < threshold:
                            group.append(f2)
                            used.add(f2)
                    except Exception:
                        continue
        used.add(f1)
        if len(group) > 1:
            groups.append(group)
    return groups

def get_image_and_video_files(folder, image_exts=(".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff"), video_exts=(".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm", ".mpg", ".mpeg", ".3gp")):
    files = []
    for root, dirs, fs in os.walk(folder):
        for f in fs:
            ext = os.path.splitext(f)[1].lower()
            if ext in image_exts or ext in video_exts:
                files.append(os.path.join(root, f))
    return files

def find_duplicates_in_folder(folder, progress_bar=None, progress_callback=None):
    image_exts = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff")
    video_exts = (".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm", ".mpg", ".mpeg", ".3gp")
    files = get_image_and_video_files(folder, image_exts, video_exts)
    file_hashes = []
    total = len(files)
    for idx, f in enumerate(files):
        ext = os.path.splitext(f)[1].lower()
        if ext in image_exts:
            h = get_image_phash(f, folder)
        else:
            h = get_video_phash(f, 7, folder)
        file_hashes.append((f, h))
        if progress_callback is not None:
            progress_callback(idx+1, total)
        elif progress_bar is not None:
            progress_bar.setValue(int((idx+1)/total*100))
    groups = group_by_phash(file_hashes)
    return groups, None
