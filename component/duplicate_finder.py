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

# 定数定義: pHash計算パラメータ
# pHashサイズを16x16に拡張（情報量4倍、誤検出率大幅低減）
PHASH_SIZE = 16  # pHashのサイズ (16x16 = 256ビット、8x8の4倍の情報量)
FRAME_RESIZE_SIZE = 128  # フレームリサイズサイズ (128x128、64x64の4倍の情報量)
MAX_FRAMES = 10  # 動画から抽出する最大フレーム数（切り抜き・結合検出に必須）
MAX_FRAME_ATTEMPTS = 30  # フレーム抽出の最大試行回数（10フレーム対応）
FRAME_SAMPLE_START = 0.05  # サンプリング開始位置 (5%、冒頭スキップ最小化)
FRAME_SAMPLE_END = 0.95  # サンプリング終了位置 (95%、広範囲カバー)
BRIGHTNESS_MIN = 10  # 明るさフィルタ最小値（暗いシーン対応）
BRIGHTNESS_MAX = 245  # 明るさフィルタ最大値（明るいシーン対応）

# 定数定義: 重複判定閾値（16x16対応、厳格化）
# 16x16 (256ビット) では情報量が4倍、閾値は慎重に設定
# - 高精度モード: 16 (約6.25%の許容誤差、切り抜き・リサイズ対応)
# - 通常モード: 8 (約3.1%の許容誤差、ほぼ同一のみ)
THRESHOLD_HIGH_PRECISION = 35  # 高精度モード閾値（10フレーム×256ビット対応、誤検出防止）
THRESHOLD_NORMAL = 8  # 通常モード閾値（1フレームあたり256ビットの約3%）

# 定数定義: メタデータフィルタ閾値
# 設計方針:
# - 解像度: 切り抜きでも変わらないため厳格に維持（2.5倍）
# - ファイルサイズ: 切り抜きで大幅に変わるため緩和（50倍）
# - 動画長: 切り抜きで大幅に変わるため緩和（50倍）
# 50倍の根拠:
# - 1時間動画の1/20切り抜き（3分）にも対応
# - ショート動画化（1/30〜1/50）にも対応
# - 極端な差（100倍超）のみ除外
# - pHash判定が本質的な類似度判定を行う
METADATA_RESOLUTION_RATIO_MAX = 3.0   # 解像度比の上限（緩和）
METADATA_FILESIZE_RATIO_MAX = 100     # ファイルサイズ比の上限（結合動画対応）
METADATA_DURATION_RATIO_MAX = 100     # 動画長比の上限（結合動画対応）

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
            if not os.path.exists(path):
                print(f"[ERROR] ファイルが存在しません: {os.path.basename(path)}")
                return None
            img = Image.open(path).convert("RGB")
            phash = imagehash.phash(img, hash_size=PHASH_SIZE)
            print(f"[DEBUG] 画像pHash成功: {os.path.basename(path)}, hash={str(phash)[:16]}...")
            # FastCacheに保存
            if cache is not None and hasattr(cache, 'set_phash'):
                cache.set_phash(path, str(phash))  # type: ignore[union-attr]
            return phash
        except Exception as e:
            print(f"[ERROR] 画像pHash計算失敗: {os.path.basename(path)} - {e}")
            import traceback
            traceback.print_exc()
            return None

    print(f"[DEBUG] キャッシュタイプチェック: cache={'None' if cache is None else 'OK'}, has_get_phash={hasattr(cache, 'get_phash') if cache else False}")

    if cache is not None and not hasattr(cache, 'get_phash'):
        print(f"[DEBUG] 旧キャッシュパス")
        if filepath in cache:
            return cache[filepath]
        val = calc_func(filepath)
        cache[filepath] = val
        return val

    print(f"[DEBUG] calc_func呼び出し直前: {os.path.basename(filepath)}")
    result = calc_func(filepath)
    print(f"[DEBUG] calc_func結果: {os.path.basename(filepath)}, result={'None' if result is None else 'OK'}")
    return result

def get_video_semantic_hash(filepath, cache=None, use_advanced=False):
    """動画の意味的ハッシュを計算（複数フレーム+メタデータ）"""
    print(f"[DEBUG] get_video_semantic_hash開始: {os.path.basename(filepath)}")
    try:
        filepath = normalize_path(filepath)
    except Exception as e:
        print(f"[ERROR] normalize_path失敗: {filepath} - {e}")
        return None

    if cache is not None and hasattr(cache, 'get_phash'):
        print(f"[DEBUG] キャッシュチェック: {os.path.basename(filepath)}")
        cached_hash = cache.get_phash(filepath)  # type: ignore[union-attr]
        if cached_hash:
            try:
                result = imagehash.hex_to_hash(cached_hash)
                print(f"[DEBUG] キャッシュから読み込み: {os.path.basename(filepath)}, hash={cached_hash[:16]}...")
                return result
            except Exception as e:
                print(f"[ERROR] キャッシュ読み込み失敗: {os.path.basename(filepath)} - {e}")
                pass
        print(f"[DEBUG] キャッシュミス、calc_func定義へ")

    print(f"[DEBUG] calc_func定義開始: {os.path.basename(filepath)}")
    def calc_func(path):
        try:
            import numpy as np

            if not os.path.exists(path):
                print(f"[ERROR] 動画ファイルが存在しません: {os.path.basename(path)}")
                return None

            cap = cv2.VideoCapture(path)
            if not cap.isOpened():
                print(f"[ERROR] 動画を開けません: {os.path.basename(path)}")
                cap.release()
                return None

            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if total_frames <= 0:
                print(f"[ERROR] フレーム数が0: {os.path.basename(path)}")
                cap.release()
                return None

            frame_hashes = []

            # 高速モード: 等間隔サンプリング
            if not use_advanced:
                for i in range(MAX_FRAMES):
                    ratio = FRAME_SAMPLE_START + (FRAME_SAMPLE_END - FRAME_SAMPLE_START) * i / (MAX_FRAMES - 1)
                    pos = int(total_frames * ratio)
                    cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
                    ret, frame = cap.read()

                    if ret and frame is not None:
                        mean_val = float(np.mean(frame))  # type: ignore[arg-type]
                        if BRIGHTNESS_MIN < mean_val < BRIGHTNESS_MAX:
                            frame = cv2.resize(frame, (FRAME_RESIZE_SIZE, FRAME_RESIZE_SIZE))
                            pil_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                            frame_hashes.append(imagehash.phash(pil_img, hash_size=PHASH_SIZE))
            # 高精度モード: ランダムサンプリング
            else:
                import random
                random.seed(int(os.path.getsize(path)))
                attempts = 0
                while len(frame_hashes) < MAX_FRAMES and attempts < MAX_FRAME_ATTEMPTS:
                    pos = int(total_frames * (FRAME_SAMPLE_START + (FRAME_SAMPLE_END - FRAME_SAMPLE_START) * random.random()))
                    cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
                    ret, frame = cap.read()

                    if ret and frame is not None:
                        mean_val = float(np.mean(frame))  # type: ignore[arg-type]
                        if BRIGHTNESS_MIN < mean_val < BRIGHTNESS_MAX:
                            frame = cv2.resize(frame, (FRAME_RESIZE_SIZE, FRAME_RESIZE_SIZE))
                            pil_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                            frame_hashes.append(imagehash.phash(pil_img, hash_size=PHASH_SIZE))
                    attempts += 1

            cap.release()

            if not frame_hashes:
                print(f"[ERROR] 有効なフレームが抽出できません: {os.path.basename(path)} (total_frames={total_frames})")
                return None

            combined_hash = frame_hashes[0]
            for h in frame_hashes[1:]:
                combined_hash = imagehash.ImageHash(combined_hash.hash ^ h.hash)

            print(f"[DEBUG] 動画pHash成功: {os.path.basename(path)}, frames={len(frame_hashes)}, hash={str(combined_hash)[:16]}...")
            if cache is not None and hasattr(cache, 'set_phash'):
                cache.set_phash(path, str(combined_hash))
            return combined_hash
        except Exception as e:
            print(f"[ERROR] 動画ハッシュ計算失敗: {os.path.basename(path)} - {e}")
            import traceback
            traceback.print_exc()
        return None

    print(f"[DEBUG] calc_func呼び出し: {os.path.basename(filepath)}")
    return calc_func(filepath)

def get_video_phash(filepath, frame_count=3, folder=None, cache=None):
    """後方互換性のため残存"""
    return get_video_semantic_hash(filepath, cache)

def is_uniform_video_hash(phash):
    """単色動画（黒画面・白画面）検出
    16x16 (256ビット) 対応: 8x8の4倍の範囲に調整
    """
    if phash is None:
        return False
    hash_array = phash.hash.flatten()
    ones_count = hash_array.sum()
    # 16x16では256ビットなので、8x8時の4倍に調整
    # 8x8: < 5 or > 59 (64ビット中)
    # 16x16: < 25 or > 230 (256ビット中、最適化)
    return ones_count < 25 or ones_count > 230

def get_video_metadata(filepath, cache=None):
    """動画メタデータ取得（キャッシュ対応）"""
    if cache is not None and hasattr(cache, 'get_metadata'):
        cached = cache.get_metadata(filepath)
        if cached:
            return cached

    cap = None
    try:
        cap = cv2.VideoCapture(filepath)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = frame_count / fps if fps > 0 else 0

        metadata = {'size': os.path.getsize(filepath), 'width': width, 'height': height, 'duration': duration}

        if cache is not None and hasattr(cache, 'set_metadata'):
            cache.set_metadata(filepath, metadata)

        return metadata
    except:
        return None
    finally:
        if cap is not None:
            cap.release()

def should_compare(meta1, meta2):
    """メタデータによる事前フィルタリング

    解像度重視フィルタ:
    - 解像度比: 2.5倍以内（厳格維持）
    - ファイルサイズ比: 50倍以内（緩和）
    - 動画長比: 50倍以内（緩和）
    """
    if meta1 is None or meta2 is None:
        return True

    # ファイルサイズ比チェック（50倍まで許容）
    size_ratio = max(meta1['size'], meta2['size']) / max(min(meta1['size'], meta2['size']), 1)
    if size_ratio > METADATA_FILESIZE_RATIO_MAX:
        return False

    # 解像度比チェック（2.5倍まで許容、厳格維持）
    width_ratio = max(meta1['width'], meta2['width']) / max(min(meta1['width'], meta2['width']), 1)
    height_ratio = max(meta1['height'], meta2['height']) / max(min(meta1['height'], meta2['height']), 1)
    if width_ratio > METADATA_RESOLUTION_RATIO_MAX or height_ratio > METADATA_RESOLUTION_RATIO_MAX:
        return False

    # 動画長比チェック（50倍まで許容）
    duration_ratio = max(meta1['duration'], meta2['duration']) / max(min(meta1['duration'], meta2['duration']), 0.1)
    if duration_ratio > METADATA_DURATION_RATIO_MAX:
        return False

    return True

def group_by_phash_advanced(file_data, metadata_dict, progress_callback=None):
    """改善版グループ化: 固定閾値・メタデータフィルター・レベル分類"""
    level1, level2, level3 = [], [], []
    used = set()
    total = len(file_data)

    for i, (f1, h1) in enumerate(file_data):
        if f1 in used or h1 is None:
            continue

        if is_uniform_video_hash(h1):
            print(f"[SKIP] 単色動画をスキップ: {os.path.basename(f1)}")
            continue

        meta1 = metadata_dict.get(f1)
        group = [(f1, 0)]
        compared_count = 0

        for j, (f2, h2) in enumerate(file_data):
            if i == j or f2 in used or h2 is None:
                continue

            if is_uniform_video_hash(h2):
                continue

            meta2 = metadata_dict.get(f2)
            if not should_compare(meta1, meta2):
                if compared_count < 3:
                    print(f"[FILTER] メタデータフィルタで除外: {os.path.basename(f1)} <-> {os.path.basename(f2)}")
                compared_count += 1
                continue
            compared_count += 1

            if hasattr(h1, '__sub__') and hasattr(h2, '__sub__'):
                try:
                    diff = abs(h1 - h2)
                    if diff <= THRESHOLD_HIGH_PRECISION:
                        print(f"[MATCH] {os.path.basename(f1)} <-> {os.path.basename(f2)}: diff={diff}, 閾値={THRESHOLD_HIGH_PRECISION}")
                        group.append((f2, diff))
                        used.add(f2)
                    elif diff <= 50:
                        print(f"[NEAR] {os.path.basename(f1)} <-> {os.path.basename(f2)}: diff={diff} (閾値外)")
                except:
                    continue

        used.add(f1)

        if len(group) > 1:
            files_only = [f for f, _ in group]
            max_diff = max(d for _, d in group)

            if max_diff <= 5:
                level1.append(files_only)
            elif max_diff <= 16:
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

def group_by_phash(file_hashes, threshold=5, progress_callback=None):
    groups = []
    used = set()
    total = len(file_hashes)
    for i, (f1, h1) in enumerate(file_hashes):
        if f1 in used or h1 is None:
            continue
        group = [f1]
        for j, (f2, h2) in enumerate(file_hashes):
            if i != j and f2 not in used and h2 is not None:
                if hasattr(h1, '__sub__') and hasattr(h2, '__sub__'):
                    try:
                        diff = abs(h1 - h2)
                        # debug info: file sizes and hashes
                        try:
                            size1 = os.path.getsize(f1)
                            size2 = os.path.getsize(f2)
                            size_ratio = max(size1, size2) / (min(size1, size2) + 1)
                        except Exception:
                            size1 = size2 = size_ratio = None
                        if diff <= threshold:
                            print(f"[DBG GROUP] MATCH: {f1} <-> {f2} diff={diff} threshold={threshold} size_ratio={size_ratio}")
                            group.append(f2)
                            used.add(f2)
                    except Exception:
                        continue
        used.add(f1)
        if len(group) > 1:
            groups.append(group)

        if progress_callback and i % 10 == 0:
            progress_callback(i + 1, total)

    if progress_callback:
        progress_callback(total, total)
    return groups

def find_group_for_index(args):
    i, (f1, h1), file_hashes, threshold = args
    if h1 is None:
        return None
    group = [f1]
    for j, (f2, h2) in enumerate(file_hashes):
        if i != j and h2 is not None:
            if hasattr(h1, '__sub__') and hasattr(h2, '__sub__'):
                try:
                    diff = abs(h1 - h2)
                    try:
                        size1 = os.path.getsize(f1)
                        size2 = os.path.getsize(f2)
                        size_ratio = max(size1, size2) / (min(size1, size2) + 1)
                    except Exception:
                        size1 = size2 = size_ratio = None
                    if diff <= threshold:
                        print(f"[DBG GROUP] Worker MATCH: {f1} <-> {f2} diff={diff} threshold={threshold} size_ratio={size_ratio}")
                        group.append(f2)
                except Exception:
                    continue
    if len(group) > 1:
        return set(group)
    return None

def group_by_phash_parallel(file_hashes, threshold=5, max_workers=None, progress_callback=None):
    if len(file_hashes) < 200:
        return group_by_phash(file_hashes, threshold, progress_callback)

    if max_workers is None:
        # CPU数の80%を使用、最低2・最大8で制限
        cpu_count = os.cpu_count() or 4
        max_workers = max(2, min(8, int(cpu_count * 0.8)))

    print(f"[PERF] 並列処理: {max_workers}ワーカーで{len(file_hashes)}ファイルを処理")

    # ファイル数に応じた動的バッチサイズ
    if len(file_hashes) < 10000:
        batch_size = len(file_hashes)  # 小規模: バッチ不要
    elif len(file_hashes) < 50000:
        batch_size = 1000  # 中規模: 1000ファイル
    else:
        batch_size = 2000  # 大規模: 2000ファイル

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

        used = set()
        for group in group_candidates:
            group = group - used
            if len(group) > 1:
                lst = list(group)
                # debug print for resulting group
                try:
                    sizes = [os.path.getsize(p) for p in lst]
                except Exception:
                    sizes = None
                print(f"[DBG GROUP] Batch grouped: count={len(lst)} files sample={lst[:3]} sizes={sizes}")
                all_groups.append(lst)
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

    from component.thumbnail.thumbnail_util import FastCache
    cache = FastCache()
    cached_groups = cache.get_group_cache(folder, files, use_advanced)
    if cached_groups:
        print(f"[PERF] キャッシュからグループ読み込み: {len(cached_groups)}グループ")
        return cached_groups, None

    if len(files) > 50000:
        print(f"[WARNING] 大量ファイル検出: {len(files)}件 - ストリーミング処理に切り替え")
        return find_duplicates_streaming(folder, files, progress_callback, parallel)

    file_hashes = []
    total = len(files)
    hash_start = time.time()

    # ファイル数に応じた動的バッチサイズ（pHash計算用）
    if len(files) < 10000:
        batch_size = len(files)  # 小規模: バッチ不要
    elif len(files) < 50000:
        batch_size = 1000  # 中規模: 1000ファイル
    else:
        batch_size = 2000  # 大規模: 2000ファイル

    for batch_start in range(0, len(files), batch_size):
        batch_files = files[batch_start:batch_start + batch_size]

        for idx, f in enumerate(batch_files):
            global_idx = batch_start + idx
            ext = os.path.splitext(f)[1].lower()

            if ext in image_exts:
                h = get_image_phash(f, folder, cache)
                if h is None:
                    print(f"[ERROR] 画像pHash失敗: {os.path.basename(f)}")
            else:
                h = get_video_semantic_hash(f, cache, use_advanced)
                if h is None:
                    print(f"[ERROR] 動画pHash失敗: {os.path.basename(f)}")

            file_hashes.append((f, h))

            if progress_callback is not None:
                progress_callback(global_idx+1, total)
            elif progress_bar is not None:
                progress_bar.setValue(int((global_idx+1)/total*100))

        if batch_start % 5000 == 0:
            import gc
            gc.collect()

    hash_elapsed = time.time() - hash_start
    print(f"[PERF] pHash計算: {hash_elapsed:.2f}秒 ({len(files)/hash_elapsed:.1f}ファイル/秒)")

    error_files = [f for f, h in file_hashes if h is None]
    valid_file_hashes = [(f, h) for f, h in file_hashes if h is not None]
    print(f"[PERF] 有効ファイル: {len(valid_file_hashes)}/{len(file_hashes)}")

    if error_files:
        print(f"[ERROR] pHash計算失敗ファイル: {len(error_files)}件")
        for ef in error_files[:5]:
            print(f"  - {os.path.basename(ef)}")
        if len(error_files) > 5:
            print(f"  ... 他{len(error_files)-5}件")

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
                progress_callback(total + idx + 1, total + len(video_files))

        print(f"[ADVANCED] メタデータ取得完了: {len(metadata_dict)}動画 ({time.time() - meta_start:.1f}秒)")
        print(f"[ADVANCED] 使用閾値: {THRESHOLD_HIGH_PRECISION} (固定値)")

        def grouping_progress(current, total_items):
            if progress_callback:
                progress_callback(total + len(video_files) + current, total + len(video_files) + total_items)

        result = group_by_phash_advanced(valid_file_hashes, metadata_dict, grouping_progress)
        groups = result['level1'] + result['level2'] + result['level3']
        print(f"[ADVANCED] レベル1: {len(result['level1'])}, レベル2: {len(result['level2'])}, レベル3: {len(result['level3'])}")
    else:
        threshold = THRESHOLD_NORMAL
        print(f"[PERF] 使用閾値: {threshold} (固定値、ファイル数: {len(valid_file_hashes)})")

        def grouping_progress(current, total_items):
            if progress_callback:
                progress_callback(total + current, total + total_items)

        if parallel and len(valid_file_hashes) > 50:
            groups = group_by_phash_parallel(valid_file_hashes, threshold=threshold, progress_callback=grouping_progress)
        else:
            groups = group_by_phash(valid_file_hashes, threshold=threshold, progress_callback=grouping_progress)

    validate_groups(groups[:10])

    group_elapsed = time.time() - group_start
    print(f"[PERF] グループ化: {group_elapsed:.2f}秒, 重複グループ: {len(groups)}")

    if error_files:
        print(f"[WARNING] pHash計算失敗ファイル: {len(error_files)}件 (グループ化から除外)")
        print(f"[INFO] 失敗理由: 明るさフィルタ除外、コーデック非対応、破損ファイル等")

    print(f"[PERF] 総処理時間: {time.time() - hash_start:.2f}秒")

    if len(groups) > 100:
        print(f"[WARNING] グループ数が異常に多い: {len(groups)} (閾値を下げることを推奨)")

    cache.set_group_cache(folder, files, use_advanced, groups)
    print(f"[PERF] グループをキャッシュに保存: {len(groups)}グループ")

    return groups, None

def find_duplicates_streaming(folder, files, progress_callback=None, parallel=True):
    """大量ファイル用ストリーミング処理"""
    print(f"[STREAM] ストリーミング処理開始: {len(files)}ファイル")

    from component.thumbnail.thumbnail_util import FastCache
    cache = FastCache()

    # ファイル数に応じた動的チャンクサイズ（ストリーミング処理用）
    if len(files) < 50000:
        chunk_size = 1000
    else:
        chunk_size = 2000

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

        import gc
        gc.collect()

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

        if i >= 9:
            break
