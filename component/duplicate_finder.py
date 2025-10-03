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
from PIL import Image
import hashlib
import pickle
import concurrent.futures
from component.utils.cache_util import save_cache, load_cache
from component.utils.file_util import normalize_path

def get_image_phash(filepath, folder=None, cache=None):
    filepath = normalize_path(filepath)

    # FastCacheのpHashキャッシュを優先使用
    if cache is not None and hasattr(cache, 'get_phash'):
        cached_hash = cache.get_phash(filepath)  # type: ignore[union-attr]
        if cached_hash:
            try:
                return imagehash.hex_to_hash(cached_hash)
            except:
                pass

    def calc_func(path):
        try:
            img = Image.open(path).convert("RGB")
            phash = imagehash.phash(img)
            # FastCacheに保存
            if cache is not None and hasattr(cache, 'set_phash'):
                cache.set_phash(path, str(phash))  # type: ignore[union-attr]
            return phash
        except Exception:
            return None

    if cache is not None and not hasattr(cache, 'get_phash'):
        if filepath in cache:
            return cache[filepath]
        val = calc_func(filepath)
        cache[filepath] = val
        return val

    return calc_func(filepath)

def get_video_semantic_hash(filepath, cache=None):
    """動画の意味的ハッシュを計算（複数フレーム+メタデータ）"""
    filepath = normalize_path(filepath)

    if cache is not None and hasattr(cache, 'get_phash'):
        cached_hash = cache.get_phash(filepath)  # type: ignore[union-attr]
        if cached_hash:
            try:
                return imagehash.hex_to_hash(cached_hash)
            except:
                pass

    def calc_func(path):
        try:
            import numpy as np
            import random

            cap = cv2.VideoCapture(path)
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

            random.seed(int(os.path.getsize(path)))
            frame_hashes = []
            attempts = 0

            while len(frame_hashes) < 5 and attempts < 20:
                pos = int(total_frames * (0.1 + 0.8 * random.random()))
                cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
                ret, frame = cap.read()

                if ret and frame is not None:
                    mean_val = float(np.mean(frame))  # type: ignore[arg-type]
                    if 15 < mean_val < 240:
                        frame = cv2.resize(frame, (64, 64))
                        pil_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                        frame_hashes.append(imagehash.phash(pil_img, hash_size=8))

                attempts += 1

            cap.release()

            if frame_hashes:
                combined_hash = frame_hashes[0]
                for h in frame_hashes[1:]:
                    combined_hash = imagehash.ImageHash(combined_hash.hash ^ h.hash)

                if cache is not None and hasattr(cache, 'set_phash'):
                    cache.set_phash(path, str(combined_hash))
                return combined_hash
        except Exception as e:
            print(f"[ERROR] 動画ハッシュ計算失敗: {path} - {e}")
        return None

def get_video_phash(filepath, frame_count=3, folder=None, cache=None):
    """後方互換性のため残存"""
    return get_video_semantic_hash(filepath, cache)

def calculate_dynamic_threshold(duration_seconds):
    """動画長に応じた動的閾値"""
    if duration_seconds < 120:
        return 10
    elif duration_seconds < 300:
        return 8
    elif duration_seconds < 900:
        return 6
    elif duration_seconds < 1800:
        return 5
    return 4

def is_uniform_video_hash(phash):
    """単色動画（黒画面・白画面）検出"""
    if phash is None:
        return False
    hash_array = phash.hash.flatten()
    ones_count = hash_array.sum()
    return ones_count < 5 or ones_count > 59

def get_video_metadata(filepath, cache=None):
    """動画メタデータ取得（キャッシュ対応）"""
    if cache is not None and hasattr(cache, 'get_metadata'):
        cached = cache.get_metadata(filepath)
        if cached:
            return cached
    
    try:
        cap = cv2.VideoCapture(filepath)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = frame_count / fps if fps > 0 else 0
        cap.release()
        
        metadata = {'size': os.path.getsize(filepath), 'width': width, 'height': height, 'duration': duration}
        
        if cache is not None and hasattr(cache, 'set_metadata'):
            cache.set_metadata(filepath, metadata)
        
        return metadata
    except:
        return None

def should_compare(meta1, meta2):
    """メタデータによる事前フィルタリング"""
    if meta1 is None or meta2 is None:
        return True
    size_ratio = max(meta1['size'], meta2['size']) / max(min(meta1['size'], meta2['size']), 1)
    if size_ratio > 10:
        return False
    width_ratio = max(meta1['width'], meta2['width']) / max(min(meta1['width'], meta2['width']), 1)
    height_ratio = max(meta1['height'], meta2['height']) / max(min(meta1['height'], meta2['height']), 1)
    if width_ratio > 2.5 or height_ratio > 2.5:
        return False
    duration_ratio = max(meta1['duration'], meta2['duration']) / max(min(meta1['duration'], meta2['duration']), 0.1)
    if duration_ratio > 10:
        return False
    return True

def group_by_phash_advanced(file_data, metadata_dict, progress_callback=None):
    """改善版グループ化: 動的閾値・メタデータフィルター・レベル分類"""
    level1, level2, level3 = [], [], []
    used = set()
    total = len(file_data)
    
    for i, (f1, h1) in enumerate(file_data):
        if f1 in used or h1 is None or is_uniform_video_hash(h1):
            continue
        
        meta1 = metadata_dict.get(f1)
        threshold1 = calculate_dynamic_threshold(meta1['duration']) if meta1 else 5
        group = [(f1, 0)]
        
        for j, (f2, h2) in enumerate(file_data):
            if i == j or f2 in used or h2 is None or is_uniform_video_hash(h2):
                continue
            
            meta2 = metadata_dict.get(f2)
            if not should_compare(meta1, meta2):
                continue
            
            threshold = min(threshold1, calculate_dynamic_threshold(meta2['duration']) if meta2 else 5)
            
            if hasattr(h1, '__sub__') and hasattr(h2, '__sub__'):
                try:
                    diff = abs(h1 - h2)
                    if diff <= 12:
                        group.append((f2, diff))
                        used.add(f2)
                except:
                    continue
        
        used.add(f1)
        
        if len(group) > 1:
            files_only = [f for f, _ in group]
            max_diff = max(d for _, d in group)
            
            if max_diff <= 3:
                level1.append(files_only)
            elif max_diff <= 8:
                level2.append(files_only)
            else:
                level3.append(files_only)
        
        if progress_callback and i % 10 == 0:
            progress_callback(i + 1, total)
    
    if progress_callback:
        progress_callback(total, total)
    
    return {'level1': level1, 'level2': level2, 'level3': level3}

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

    # 大規模データではワーカー数を制限
    if max_workers is None:
        max_workers = min(4, os.cpu_count() or 4)  # 最大4コアに制限

    print(f"[PERF] 並列処理: {max_workers}ワーカーで{len(file_hashes)}ファイルを処理")

    # バッチ処理でメモリ使用量を抑制
    batch_size = 5000
    all_groups = []

    for i in range(0, len(file_hashes), batch_size):
        batch = file_hashes[i:i+batch_size]
        print(f"[PERF] バッチ {i//batch_size + 1}: {len(batch)}ファイル")

        args_list = [(j, fh, batch, threshold) for j, fh in enumerate(batch)]
        group_candidates = []

        optimal_chunksize = max(1, len(batch) // (max_workers * 4))

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            for res in executor.map(find_group_for_index, args_list, chunksize=optimal_chunksize):
                if res:
                    group_candidates.append(res)

        # バッチ内でグループ化
        used = set()
        for group in group_candidates:
            group = group - used
            if len(group) > 1:
                all_groups.append(list(group))
                used.update(group)

    return all_groups

def get_image_and_video_files(folder, image_exts=(".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff"), video_exts=(".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm", ".mpg", ".mpeg", ".3gp")):
    files = []
    for root, dirs, fs in os.walk(folder):
        for f in fs:
            ext = os.path.splitext(f)[1].lower()
            if ext in image_exts or ext in video_exts:
                files.append(os.path.join(root, f))
    return files

import time
from functools import wraps

def measure_time(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        elapsed = time.time() - start
        print(f"[PERF] {func.__name__}: {elapsed:.2f}秒")
        return result
    return wrapper

@measure_time
def find_duplicates_in_folder(folder, progress_bar=None, progress_callback=None, parallel=True, use_advanced=False):
    image_exts = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff")
    video_exts = (".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm", ".mpg", ".mpeg", ".3gp")
    files = get_image_and_video_files(folder, image_exts, video_exts)
    print(f"[PERF] 対象ファイル数: {len(files)}")

    # メモリ制限チェック
    if len(files) > 50000:
        print(f"[WARNING] 大量ファイル検出: {len(files)}件 - ストリーミング処理に切り替え")
        return find_duplicates_streaming(folder, files, progress_callback, parallel)

    from component.thumbnail.thumbnail_util import FastCache
    cache = FastCache()

    file_hashes = []
    total = len(files)
    hash_start = time.time()

    # バッチ処理でメモリ効率化
    batch_size = 1000
    for batch_start in range(0, len(files), batch_size):
        batch_files = files[batch_start:batch_start + batch_size]

        for idx, f in enumerate(batch_files):
            global_idx = batch_start + idx
            ext = os.path.splitext(f)[1].lower()

            if ext in image_exts:
                h = get_image_phash(f, folder, cache)
            else:
                h = get_video_semantic_hash(f, cache)  # 改良版を使用

            file_hashes.append((f, h))

            if progress_callback is not None:
                progress_callback(global_idx+1, total)
            elif progress_bar is not None:
                progress_bar.setValue(int((global_idx+1)/total*100))

        # バッチ完了時にメモリクリーンアップ
        if batch_start % 5000 == 0:
            import gc
            gc.collect()

    hash_elapsed = time.time() - hash_start
    print(f"[PERF] pHash計算: {hash_elapsed:.2f}秒 ({len(files)/hash_elapsed:.1f}ファイル/秒)")
    # pHashがNoneのファイルを抽出
    error_files = [f for f, h in file_hashes if h is None]
    # グループ化
    valid_file_hashes = [(f, h) for f, h in file_hashes if h is not None]
    print(f"[PERF] 有効ファイル: {len(valid_file_hashes)}/{len(file_hashes)}")

    # 整合性チェック: pHashの分布を確認
    hash_counts = {}
    for f, h in valid_file_hashes:
        hash_str = str(h)
        hash_counts[hash_str] = hash_counts.get(hash_str, 0) + 1

    identical_hashes = sum(1 for count in hash_counts.values() if count > 1)
    print(f"[INTEGRITY] 同一pHash: {identical_hashes}種類, 平均重複: {len(valid_file_hashes)/len(hash_counts):.1f}")

    group_start = time.time()
    
    if use_advanced:
        print("[ADVANCED] 高精度モード: メタデータ取得中...")
        metadata_dict = {}
        video_files = [f for f, h in valid_file_hashes if os.path.splitext(f)[1].lower() in video_exts]
        
        meta_start = time.time()
        for idx, vf in enumerate(video_files):
            metadata_dict[vf] = get_video_metadata(vf, cache)
            
            if progress_callback is not None:
                # メタデータ取得の進捗を通知 (totalの2倍で調整)
                progress_callback(total + idx + 1, total + len(video_files))
        
        print(f"[ADVANCED] メタデータ取得完了: {len(metadata_dict)}動画 ({time.time() - meta_start:.1f}秒)")
        
        def grouping_progress(current, total_items):
            if progress_callback:
                progress_callback(total + len(video_files) + current, total + len(video_files) + total_items)
        
        result = group_by_phash_advanced(valid_file_hashes, metadata_dict, grouping_progress)
        groups = result['level1'] + result['level2'] + result['level3']
        print(f"[ADVANCED] レベル1: {len(result['level1'])}, レベル2: {len(result['level2'])}, レベル3: {len(result['level3'])}")
    else:
        threshold = 2 if len(valid_file_hashes) > 15000 else 3 if len(valid_file_hashes) > 5000 else 5
        print(f"[PERF] 使用閾値: {threshold} (ファイル数: {len(valid_file_hashes)})")
        
        if parallel and len(valid_file_hashes) > 50:
            groups = group_by_phash_parallel(valid_file_hashes, threshold=threshold)
        else:
            groups = group_by_phash(valid_file_hashes, threshold=threshold)

    # 整合性チェック: グループの妥当性を検証
    validate_groups(groups[:10])  # 最初の10グループを検証

    group_elapsed = time.time() - group_start
    print(f"[PERF] グループ化: {group_elapsed:.2f}秒, 重複グループ: {len(groups)}")
    # エラー（未分類）ファイルを一番下に追加
    if error_files:
        groups.append(error_files)
        print(f"[PERF] エラーファイル: {len(error_files)}")

    print(f"[PERF] 総処理時間: {time.time() - hash_start:.2f}秒")

    # 最終整合性チェック
    if len(groups) > 100:
        print(f"[WARNING] グループ数が異常に多い: {len(groups)} (閾値を下げることを推奨)")

    return groups, None

def find_duplicates_streaming(folder, files, progress_callback=None, parallel=True):
    """大量ファイル用ストリーミング処理"""
    print(f"[STREAM] ストリーミング処理開始: {len(files)}ファイル")

    from component.thumbnail.thumbnail_util import FastCache
    cache = FastCache()

    # チャンク単位で処理
    chunk_size = 5000
    all_groups = []
    processed_hashes = {}

    for chunk_start in range(0, len(files), chunk_size):
        chunk_files = files[chunk_start:chunk_start + chunk_size]
        print(f"[STREAM] チャンク {chunk_start//chunk_size + 1}: {len(chunk_files)}ファイル")

        chunk_hashes = []
        for idx, f in enumerate(chunk_files):
            ext = os.path.splitext(f)[1].lower()
            if ext in (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff"):
                h = get_image_phash(f, folder, cache)
            else:
                h = get_video_semantic_hash(f, cache)

            if h is not None:
                hash_str = str(h)
                if hash_str in processed_hashes:
                    processed_hashes[hash_str].append(f)
                else:
                    processed_hashes[hash_str] = [f]

            if progress_callback:
                progress_callback(chunk_start + idx + 1, len(files))

        # メモリクリーンアップ
        import gc
        gc.collect()

    # 重複グループを抽出
    for hash_str, file_list in processed_hashes.items():
        if len(file_list) > 1:
            all_groups.append(file_list)

    print(f"[STREAM] 完了: {len(all_groups)}グループ検出")
    return all_groups, None

def validate_groups(groups):
    """グループの整合性を検証"""
    print(f"[INTEGRITY] グループ検証開始: {len(groups)}グループ")

    for i, group in enumerate(groups):
        if len(group) < 2:
            continue

        # 最初の2ファイルのpHashを比較
        try:
            from component.thumbnail.thumbnail_util import FastCache
            cache = FastCache()

            file1, file2 = group[0], group[1]
            hash1 = cache.get_phash(file1)
            hash2 = cache.get_phash(file2)

            if hash1 and hash2:
                try:
                    import imagehash
                    h1 = imagehash.hex_to_hash(hash1)
                    h2 = imagehash.hex_to_hash(hash2)
                    diff = abs(h1 - h2)

                    print(f"[INTEGRITY] グループ{i+1}: {len(group)}ファイル, pHash差分: {diff}")

                    if diff > 5:
                        print(f"[WARNING] グループ{i+1}の差分が大きすぎる: {diff}")
                        print(f"  ファイル1: {os.path.basename(file1)}")
                        print(f"  ファイル2: {os.path.basename(file2)}")
                except Exception as e:
                    print(f"[INTEGRITY] pHash比較エラー: {e}")
        except Exception as e:
            print(f"[INTEGRITY] グループ検証エラー: {e}")

        if i >= 9:  # 最初の10グループのみ
            break
