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
import concurrent.futures
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
            print(f"[pHash cache HIT] {filepath}")
            return cache[filepath]
        val = calc_func(filepath)
        print(f"[pHash cache MISS] {filepath}")
        cache[filepath] = val
        return val
    val = get_features_with_cache(filepath, calc_func, folder)
    # print(f"[pHash cache (get_features_with_cache)] {filepath} -> HIT" if val is not None else f"[pHash cache (get_features_with_cache)] {filepath} -> MISS")
    return val

def get_video_phash(filepath, frame_count=3, folder=None, cache=None):
    filepath = normalize_path(filepath)
    def calc_func(path):
        try:
            cap = cv2.VideoCapture(path)
            # 最初のフレームのみ使用（高速化）
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ret, frame = cap.read()
            cap.release()
            
            if ret:
                pil_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                return imagehash.phash(pil_img)
        except Exception:
            pass
        return None
    
    if cache is not None:
        if filepath in cache:
            return cache[filepath]
        val = calc_func(filepath)
        cache[filepath] = val
        return val
    val = get_features_with_cache(filepath, calc_func, folder)
    return val

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

def group_by_phash(file_hashes, threshold=5):
    groups = []
    used = set()
    for i, (f1, h1) in enumerate(file_hashes):
        if f1 in used or h1 is None:
            continue
        group = [f1]
        for j, (f2, h2) in enumerate(file_hashes):
            if i != j and f2 not in used and h2 is not None:
                # ImageHashオブジェクトのみ処理（リスト型は廃止）
                if hasattr(h1, '__sub__') and hasattr(h2, '__sub__'):
                    try:
                        diff = abs(h1 - h2)
                        if diff < threshold:
                            group.append(f2)
                            used.add(f2)
                    except Exception:
                        continue
        used.add(f1)
        if len(group) > 1:
            groups.append(group)
    return groups

def find_group_for_index(args):
    i, (f1, h1), file_hashes, threshold = args
    if h1 is None:
        return None
    group = [f1]
    for j, (f2, h2) in enumerate(file_hashes):
        if i != j and h2 is not None:
            # ImageHashオブジェクトのみ処理
            if hasattr(h1, '__sub__') and hasattr(h2, '__sub__'):
                try:
                    diff = abs(h1 - h2)
                    if diff < threshold:
                        group.append(f2)
                except Exception:
                    continue
    if len(group) > 1:
        return set(group)
    return None

def group_by_phash_parallel(file_hashes, threshold=5, max_workers=None):
    if len(file_hashes) < 200:
        return group_by_phash(file_hashes, threshold)
    
    if max_workers is None:
        max_workers = min(os.cpu_count() or 4, len(file_hashes) // 50)
    
    args_list = [(i, fh, file_hashes, threshold) for i, fh in enumerate(file_hashes)]
    group_candidates = []
    
    optimal_chunksize = max(1, len(file_hashes) // (max_workers * 4))
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        for res in executor.map(find_group_for_index, args_list, chunksize=optimal_chunksize):
            if res:
                group_candidates.append(res)
    
    final_groups = []
    used = set()
    for group in group_candidates:
        group = group - used
        if len(group) > 1:
            final_groups.append(list(group))
            used.update(group)
    return final_groups

def get_image_and_video_files(folder, image_exts=(".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff"), video_exts=(".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm", ".mpg", ".mpeg", ".3gp")):
    files = []
    for root, dirs, fs in os.walk(folder):
        for f in fs:
            ext = os.path.splitext(f)[1].lower()
            if ext in image_exts or ext in video_exts:
                files.append(os.path.join(root, f))
    return files

def find_duplicates_in_folder(folder, progress_bar=None, progress_callback=None, parallel=True):
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
            h = get_video_phash(f, 3, folder)  # デフォルト値と統一
        file_hashes.append((f, h))
        if progress_callback is not None:
            progress_callback(idx+1, total)
        elif progress_bar is not None:
            progress_bar.setValue(int((idx+1)/total*100))
    # pHashがNoneのファイルを抽出
    error_files = [f for f, h in file_hashes if h is None]
    # グループ化
    valid_file_hashes = [(f, h) for f, h in file_hashes if h is not None]
    if parallel and len(valid_file_hashes) > 100:
        groups = group_by_phash_parallel(valid_file_hashes, threshold=5)
    else:
        groups = group_by_phash(valid_file_hashes, threshold=5)
    # エラー（未分類）ファイルを一番下に追加
    if error_files:
        groups.append(error_files)
    return groups, None
