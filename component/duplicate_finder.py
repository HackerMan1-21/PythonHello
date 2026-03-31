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
import math
import numpy as np
import imagehash
import cv2
from PIL import Image
import hashlib
import pickle
import subprocess
import json
import concurrent.futures
import time
from component.utils.cache_util import save_cache, load_cache
from component.utils.file_util import normalize_path
from component.utils.pair_cache import PairCache
from component.utils.media_quality import sort_files_by_quality
from component.utils import db as dup_db
from component.utils import feature_extractor as fe
from component.utils import faiss_index as fi
try:
    from component.utils import clip_extractor as _ce
    _CLIP_AVAILABLE = True
except Exception:
    _ce = None  # type: ignore[assignment]
    _CLIP_AVAILABLE = False
try:
    from component.utils import dtw_matcher as _dtw
    _DTW_AVAILABLE = True
except Exception:
    _dtw = None  # type: ignore[assignment]
    _DTW_AVAILABLE = False

# 動画の「切り抜き」等に対応する部分一致設定（デフォルト）
VIDEO_PARTIAL_MATCH_DEFAULT = True
VIDEO_PARTIAL_SAMPLE_INTERVAL_SEC = 0.7
VIDEO_PARTIAL_MAX_SAMPLES = 200
VIDEO_PARTIAL_HASH_DISTANCE_MAX = 10
VIDEO_PARTIAL_HASH_DISTANCE_AVG_MAX = 7
VIDEO_PARTIAL_OVERLAP_RATIO_MIN = 0.20
VIDEO_PARTIAL_MIN_MATCHES = 4
VIDEO_PARTIAL_CANDIDATE_MIN_SHARED = 3
VIDEO_PARTIAL_REQUIRE_ORDER = False
VIDEO_PARTIAL_AVOID_DARK_SCENES = True
VIDEO_PARTIAL_DARK_TRIM_RATIO = 0.25
VIDEO_PARTIAL_MIN_CONTRAST_STD = 8.0
VIDEO_LONG_DURATION_SEC = 20 * 60
VIDEO_LONG_MAX_SAMPLES = 300
VIDEO_EXTS_DEFAULT = (".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm", ".mpg", ".mpeg", ".3gp")

# 候補上限ルール（精度優先 + 爆発抑止）
# 1) FAISS候補: n x 50
# 2) meta近傍候補: FAISS候補 x 3
# 3) 全候補上限: 1,200,000ペア
FAISS_CANDIDATES_PER_FILE = 50
META_CANDIDATE_MULTIPLIER = 3
MAX_TOTAL_CANDIDATE_PAIRS = 1_200_000

# ---------------------------------------------------------------------------
# Multi-metric scoring weights (CLIP + DTW + pHash + Meta)
# ---------------------------------------------------------------------------
W_CLIP = 0.40           # CLIP semantic cosine similarity
W_WINDOW = 0.30         # Window-DTW partial match score
W_PHASH = 0.15          # pHash hamming similarity
W_META = 0.15           # Metadata (duration / resolution ratio) similarity
FINAL_MULTI_SCORE_THRESHOLD = 0.55   # threshold to call a CLIP-only candidate a duplicate
CLIP_CANDIDATES_PER_FILE = 80        # top-K from HNSW per video

# priority_score = faiss_distance + META_PENALTY_WEIGHT * meta_penalty
# - faiss_distance: 正規化L2距離 [0, 1] を想定
# - meta_penalty: 比率差から算出 [0, 1] を想定
META_PENALTY_WEIGHT = 6.0
WINDOW_ALIGNMENT_WEIGHT = 0.5
# window-level 正規化距離で一致とみなす閾値 (normalized L2^2)
# tuned: lower threshold for better recall on borderline families
WINDOW_DIST_PENALTY = 0.2
# Phase 1a: 0.04 -> 0.12 に緩和（再エンコード・切り抜き Recall +10-15pt）
WINDOW_MATCH_DNORM_THRESHOLD = 0.12
# Phase 1b: 0.10 -> 0.35 に緩和（切り抜き候補の取りこぼし大幅減）
FINAL_PRIORITY_SCORE_THRESHOLD = 0.35

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
THRESHOLD_HIGH_PRECISION = 12  # 高精度モード閾値（厳しめ: pHashハミング距離）
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
METADATA_RESOLUTION_RATIO_MAX = 20.0  # ハード除外許可の上限（要件）
METADATA_FILESIZE_RATIO_MAX = 1e18    # ハード除外しない（ペナルティ化のみ）
METADATA_DURATION_RATIO_MAX = 20.0    # ハード除外許可の上限（要件）

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


def _is_video_file(path: str, video_exts=VIDEO_EXTS_DEFAULT) -> bool:
    return os.path.splitext(path)[1].lower() in video_exts


def _is_uniform_frame_hash(h: imagehash.ImageHash) -> bool:
    """単色に近いフレーム（黒画面/白画面など）のハッシュを除外。

    8x8 (64bit) を前提に、極端な偏りを除外する。
    """
    try:
        arr = h.hash.flatten()
        ones = int(arr.sum())
        return ones < 5 or ones > 59
    except Exception:
        return False


def _frame_brightness_contrast(frame_bgr) -> tuple[float, float]:
    """BGRフレームの平均輝度とコントラスト(標準偏差)を返す。"""
    try:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        mean, stddev = cv2.meanStdDev(gray)
        return float(mean[0][0]), float(stddev[0][0])
    except Exception:
        return 0.0, 0.0


def get_video_frame_hashes(
    filepath: str,
    sample_interval_sec: float = VIDEO_PARTIAL_SAMPLE_INTERVAL_SEC,
    max_samples: int = VIDEO_PARTIAL_MAX_SAMPLES,
    avoid_dark_scenes: bool = VIDEO_PARTIAL_AVOID_DARK_SCENES,
    dark_trim_ratio: float = VIDEO_PARTIAL_DARK_TRIM_RATIO,
    min_contrast_std: float = VIDEO_PARTIAL_MIN_CONTRAST_STD,
):
    """動画からフレームをサンプリングして dHash リストを返す。

    - 切り抜き/結合に強い: 複数フレームの集合で比較
    - 画質/拡張子差に強い: 知覚ハッシュ(dHash)
    """
    filepath = normalize_path(filepath)
    if not os.path.exists(filepath):
        return []

    cap = cv2.VideoCapture(filepath)
    if not cap.isOpened():
        cap.release()
        return []

    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration = (frame_count / fps) if fps > 0 and frame_count > 0 else 0.0

        # 長尺（20分超）は、比較精度のためサンプル上限を自動的に引き上げる
        effective_max_samples = int(max_samples)
        if duration > VIDEO_LONG_DURATION_SEC:
            effective_max_samples = max(effective_max_samples, VIDEO_LONG_MAX_SAMPLES)

        # durationが取れない場合は従来の比率サンプルにフォールバック
        if duration <= 0:
            indices = [int(frame_count * (FRAME_SAMPLE_START + (FRAME_SAMPLE_END - FRAME_SAMPLE_START) * i / max(max_samples - 1, 1))) for i in range(min(max_samples, max(frame_count, 1)))]
        else:
            start_t = max(duration * 0.05, 0.0)
            end_t = max(duration * 0.95, start_t)

            # 長尺動画は「間隔そのまま」だと max_samples にすぐ到達して
            # 先頭付近だけを見てしまうため、全体に均等サンプリングへ切り替える。
            if sample_interval_sec <= 0:
                sample_interval_sec = VIDEO_PARTIAL_SAMPLE_INTERVAL_SEC

            span = max(end_t - start_t, 0.0)
            expected = int(span / float(sample_interval_sec)) + 1 if span > 0 else 1

            if expected > effective_max_samples and effective_max_samples > 1:
                step = span / float(effective_max_samples - 1) if span > 0 else 0.0
                indices = [int((start_t + step * i) * fps) for i in range(effective_max_samples)]
            else:
                t = start_t
                indices = []
                while t <= end_t and len(indices) < effective_max_samples:
                    indices.append(int(t * fps))
                    t += float(sample_interval_sec)

        samples: list[tuple[imagehash.ImageHash, float, float]] = []
        for idx in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(max(idx, 0)))
            ret, frame = cap.read()
            if not ret or frame is None:
                continue

            mean_val, std_val = _frame_brightness_contrast(frame)
            # まずは極端な暗転/白飛びを除外（従来の明るさフィルタも維持）
            if not (BRIGHTNESS_MIN < mean_val < BRIGHTNESS_MAX):
                continue
            # 暗転・単調なフレーム(フェード/黒味/低照度)は誤検出の元なので落とす
            if std_val < float(min_contrast_std):
                continue

            frame = cv2.resize(frame, (FRAME_RESIZE_SIZE, FRAME_RESIZE_SIZE))
            pil_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            h = imagehash.dhash(pil_img, hash_size=8)
            if _is_uniform_frame_hash(h):
                continue
            samples.append((h, mean_val, std_val))

        if not samples:
            return []

        # 自動暗転回避: 明るさの下位X%をまとめて捨てる（動画の暗転パートを避ける）
        if avoid_dark_scenes and 0.0 < float(dark_trim_ratio) < 0.9 and len(samples) >= 8:
            samples.sort(key=lambda x: x[1])
            drop_n = int(len(samples) * float(dark_trim_ratio))
            if drop_n > 0:
                samples = samples[drop_n:]

        return [h for (h, _m, _s) in samples]
    finally:
        cap.release()


def video_partial_match(
    hashes1: list[imagehash.ImageHash],
    hashes2: list[imagehash.ImageHash],
    hash_distance_max: int = VIDEO_PARTIAL_HASH_DISTANCE_MAX,
    hash_distance_avg_max: int = VIDEO_PARTIAL_HASH_DISTANCE_AVG_MAX,
    overlap_ratio_min: float = VIDEO_PARTIAL_OVERLAP_RATIO_MIN,
    min_matches: int = VIDEO_PARTIAL_MIN_MATCHES,
    require_order: bool = VIDEO_PARTIAL_REQUIRE_ORDER,
):
    """部分一致の判定。

    - 片方が短い切り抜きでも、短い側の一致率で評価
    """
    if not hashes1 or not hashes2:
        return False, 0, 0.0

    # 常に短い側を外側ループにして比較回数を抑える
    if len(hashes1) <= len(hashes2):
        small, large = hashes1, hashes2
    else:
        small, large = hashes2, hashes1

    matched = 0
    total_dist = 0
    used_large: set[int] = set()
    last_j = -1

    for h in small:
        best_j = None
        best_d = None

        start_j = (last_j + 1) if require_order else 0
        for j in range(start_j, len(large)):
            if j in used_large:
                continue
            h2 = large[j]
            try:
                d = abs(h - h2)
            except Exception:
                continue
            if d <= hash_distance_max and (best_d is None or d < best_d):
                best_d = d
                best_j = j
                if d == 0:
                    break

        if best_j is not None and best_d is not None:
            used_large.add(best_j)
            matched += 1
            total_dist += int(best_d)
            if require_order:
                last_j = best_j

            overlap = matched / max(len(small), 1)
            avg_dist = total_dist / max(matched, 1)
            if (
                matched >= min_matches
                and overlap >= overlap_ratio_min
                and avg_dist <= hash_distance_avg_max
            ):
                return True, matched, overlap

    overlap = matched / max(len(small), 1)
    avg_dist = total_dist / max(matched, 1)
    return (matched >= min_matches and overlap >= overlap_ratio_min and avg_dist <= hash_distance_avg_max), matched, overlap


def _safe_ratio(a: float, b: float, min_denom: float = 1e-6) -> float:
    aa = float(a or 0.0)
    bb = float(b or 0.0)
    hi = max(aa, bb)
    lo = max(min(aa, bb), float(min_denom))
    return hi / lo


def _normalized_faiss_distance(raw_dist: float | None) -> float:
    """FAISS L2距離を [0,1] に正規化。

    Phase 2b: 256-d 正規化ベクトル同士の L2^2 の理論上限は 8.0 (旧 128d: 4.0)。
    /8.0 でクランプすることで閾値の意味が次元数に依存しなくなる。
    """
    if raw_dist is None:
        return 1.0
    try:
        return max(0.0, min(float(raw_dist) / 8.0, 1.0))
    except Exception:
        return 1.0


def _meta_penalty(meta1, meta2) -> float:
    """メタデータ差分を [0,1] のペナルティへ変換（ハード除外ではなく優先度制御）。"""
    if not meta1 or not meta2:
        return 0.35

    try:
        duration_ratio = _safe_ratio(meta1.get('duration', 0.0), meta2.get('duration', 0.0), min_denom=0.1)
        width_ratio = _safe_ratio(meta1.get('width', 0), meta2.get('width', 0), min_denom=1)
        height_ratio = _safe_ratio(meta1.get('height', 0), meta2.get('height', 0), min_denom=1)
        resolution_ratio = max(width_ratio, height_ratio)
        size_ratio = _safe_ratio(meta1.get('size', 0), meta2.get('size', 0), min_denom=1)

        # ハード除外は should_compare で処理。ここはペナルティのみ。
        # 比率1.0->0.0, 比率20.0->1.0 へ対数正規化（低比率差を重視）
        def to_penalty(r: float) -> float:
            import math as _m
            rr = max(1.0, float(r))
            return max(0.0, min(_m.log(rr, 20.0), 1.0))

        p_dur = to_penalty(duration_ratio)
        p_res = to_penalty(resolution_ratio)
        p_size = to_penalty(size_ratio)
        # 再エンコード耐性のため size の重みは弱くする
        penalty = 0.45 * p_dur + 0.45 * p_res + 0.10 * p_size
        return max(0.0, min(penalty, 1.0))
    except Exception:
        return 0.5


def find_partial_duplicate_video_groups(
    video_files: list[str],
    frame_hash_cache: dict[str, list[imagehash.ImageHash]],
    pair_cache: PairCache | None = None,
    sample_interval_sec: float = VIDEO_PARTIAL_SAMPLE_INTERVAL_SEC,
    max_samples: int = VIDEO_PARTIAL_MAX_SAMPLES,
    hash_distance_max: int = VIDEO_PARTIAL_HASH_DISTANCE_MAX,
    hash_distance_avg_max: int = VIDEO_PARTIAL_HASH_DISTANCE_AVG_MAX,
    overlap_ratio_min: float = VIDEO_PARTIAL_OVERLAP_RATIO_MIN,
    min_matches: int = VIDEO_PARTIAL_MIN_MATCHES,
    candidate_min_shared: int = VIDEO_PARTIAL_CANDIDATE_MIN_SHARED,
    require_order: bool = VIDEO_PARTIAL_REQUIRE_ORDER,
    avoid_dark_scenes: bool = VIDEO_PARTIAL_AVOID_DARK_SCENES,
    dark_trim_ratio: float = VIDEO_PARTIAL_DARK_TRIM_RATIO,
    min_contrast_std: float = VIDEO_PARTIAL_MIN_CONTRAST_STD,
    faiss_neighbors: dict | None = None,
    idx_map: dict | None = None,
    progress_callback=None,
):
    """動画を全体横断で部分一致グループ化する（切り抜き対応）。"""
    if not video_files:
        return []

    def _compute_window_alignment(path_a: str, path_b: str, max_comparisons: int = 2000):
        """Compute simple window alignment score between two files.

        Returns (match_count:int, avg_best_dist_norm:float) where dist is normalized to [0,1].
        To limit work, if num_windows_a * num_windows_b > max_comparisons, uniformly sample windows.
        """
        try:
            rec_a = dup_db.get_file_by_path(path_a)
            rec_b = dup_db.get_file_by_path(path_b)
            if not rec_a or not rec_b:
                return 0, 1.0
            wa = dup_db.get_window_features_for_file(rec_a['file_id'])
            wb = dup_db.get_window_features_for_file(rec_b['file_id'])
            if not wa or not wb:
                return 0, 1.0

            vecs_a = []
            for r in wa:
                raw_a = r.get('vec')
                if raw_a is None:
                    continue
                v = fi._bytes_to_vector(raw_a)
                if v is not None:
                    vecs_a.append(v)
            vecs_b = []
            for r in wb:
                raw_b = r.get('vec')
                if raw_b is None:
                    continue
                v = fi._bytes_to_vector(raw_b)
                if v is not None:
                    vecs_b.append(v)

            if not vecs_a or not vecs_b:
                return 0, 1.0

            na = len(vecs_a)
            nb = len(vecs_b)
            # sampling if too large
            if na * nb > max_comparisons:
                # target roughly sqrt(max_comparisons) per side
                m = int(max(2, min(200, int((max_comparisons)**0.5))))
                idx_a = [int(i * na / m) for i in range(m)]
                idx_b = [int(i * nb / m) for i in range(m)]
                vecs_a = [vecs_a[i] for i in idx_a if i < na]
                vecs_b = [vecs_b[i] for i in idx_b if i < nb]
                na = len(vecs_a)
                nb = len(vecs_b)

            A = np.vstack(vecs_a).astype(np.float32)
            B = np.vstack(vecs_b).astype(np.float32)
            # compute pairwise L2^2
            diffs = A[:, None, :] - B[None, :, :]
            d2 = np.sum(diffs * diffs, axis=2)
            # adaptive normalization: scale distances by a data-driven factor
            # previous fixed divisor (4.0) was too small for real vectors; use
            # a scale based on the median pairwise distance to make the
            # normalized threshold robust across codecs/resolutions.
            # Phase 1c: percentile75 ベースの適応スケール (median×4 より安定)
            try:
                p75 = float(np.percentile(d2, 75)) if d2.size else 4.0
            except Exception:
                p75 = 4.0
            scale = max(4.0, p75 * 2.5)
            dnorm = np.clip(d2 / float(scale), 0.0, 1.0)
            # for each window in A, take best match distance
            best_a = np.min(dnorm, axis=1)
            match_count = int((best_a <= WINDOW_MATCH_DNORM_THRESHOLD).sum())
            avg_best = float(best_a.mean())
            return match_count, avg_best
        except Exception:
            return 0, 1.0

    # 1) フレームハッシュ抽出
    for i, vf in enumerate(video_files):
        if vf not in frame_hash_cache:
            frame_hash_cache[vf] = get_video_frame_hashes(
                vf,
                sample_interval_sec=sample_interval_sec,
                max_samples=max_samples,
                avoid_dark_scenes=avoid_dark_scenes,
                dark_trim_ratio=dark_trim_ratio,
                min_contrast_std=min_contrast_std,
            )
        if progress_callback and i % 20 == 0:
            progress_callback(i + 1, len(video_files))

    # prepare FAISS index for fallback neighbor queries if not supplied
    _local_idx = None
    _local_id_list = None
    try:
        _local_idx, _local_id_list = fi.load_index(os.path.join('.thumb_cache', 'faiss_index'))
        if _local_idx is None:
            _local_idx, _local_id_list = fi.build_index_from_db()
    except Exception:
        _local_idx = None
        _local_id_list = None

    # 2) 逆引きインデックス（ハッシュ文字列 -> 動画インデックス）
    index: dict[str, list[int]] = {}
    video_files = list(video_files)
    for i, vf in enumerate(video_files):
        for h in frame_hash_cache.get(vf, []):
            key = str(h)
            index.setdefault(key, []).append(i)

    # 3) Union-Find
    parent = list(range(len(video_files)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # 4) 候補生成（共有ハッシュ数 + FAISS + メタ優先度）→ 確定（距離許容の部分一致）
    total_candidate_pairs = 0
    for i, vf in enumerate(video_files):
        if total_candidate_pairs >= MAX_TOTAL_CANDIDATE_PAIRS:
            print(f"[CANDIDATE CAP] 全候補上限に到達: {MAX_TOTAL_CANDIDATE_PAIRS}")
            break

        counts: dict[int, int] = {}
        faiss_dist_map: dict[int, float] = {}
        for h in frame_hash_cache.get(vf, []):
            for j in index.get(str(h), []):
                if j <= i:
                    continue
                counts[j] = counts.get(j, 0) + 1

        # Merge FAISS neighbors if provided (promote them to candidate_min_shared)
        try:
            if faiss_neighbors and idx_map:
                neighs = faiss_neighbors.get(vf, [])
                added = 0
                for nitem in neighs:
                    if isinstance(nitem, tuple) and len(nitem) >= 2:
                        npath = nitem[0]
                        ndist = nitem[1]
                    else:
                        npath = nitem
                        ndist = None
                    if npath not in idx_map:
                        continue
                    j = idx_map[npath]
                    if j <= i:
                        continue
                    if counts.get(j, 0) < candidate_min_shared:
                        counts[j] = candidate_min_shared
                        added += 1
                    faiss_dist_map[j] = _normalized_faiss_distance(ndist)
                    if added >= FAISS_CANDIDATES_PER_FILE:
                        break
        except Exception:
            pass

        # If no FAISS neighbors were provided, attempt local FAISS query as fallback
        try:
            if (not faiss_neighbors or not idx_map) and _local_idx is not None and _local_id_list is not None:
                # get first window vector for vf
                rec = dup_db.get_file_by_path(vf)
                if rec:
                    wrows = dup_db.get_window_features_for_file(rec['file_id'])
                    if wrows:
                        v = None
                        try:
                            v = fi._bytes_to_vector(wrows[0]['vec'])
                        except Exception:
                            v = None
                        if v is not None:
                            neigh = fi.query_index(_local_idx, _local_id_list, v, k=FAISS_CANDIDATES_PER_FILE)
                            added = 0
                            for wid, fid2, dist in neigh:
                                try:
                                    r2 = dup_db.get_file_by_id(fid2)
                                    if not r2:
                                        continue
                                    npath = r2.get('path')
                                    # allow operation even when idx_map is None by
                                    # building a local mapping from the video_files
                                    # list passed into this function.
                                    try:
                                        if idx_map is not None:
                                            lookup_map = {normalize_path(k): v for k, v in idx_map.items()}
                                        else:
                                            lookup_map = {normalize_path(p): ii for ii, p in enumerate(video_files)}
                                    except Exception:
                                        lookup_map = {normalize_path(p): ii for ii, p in enumerate(video_files)}
                                    npath_norm = normalize_path(npath) if npath else npath
                                    if npath_norm not in lookup_map:
                                        continue
                                    j = lookup_map[npath_norm]
                                    if j <= i:
                                        continue
                                    if counts.get(j, 0) < candidate_min_shared:
                                        counts[j] = candidate_min_shared
                                        added += 1
                                    faiss_dist_map[j] = _normalized_faiss_distance(dist)
                                    if added >= FAISS_CANDIDATES_PER_FILE:
                                        break
                                except Exception:
                                    continue
        except Exception:
            pass

        # limited debug logging for problematic families to trace why
        # they may be excluded from candidates. This prints only when base
        # filename contains known tokens to avoid huge logs.
        try:
            base_name_dbg = os.path.basename(vf)
            if any(tok in base_name_dbg for tok in ('chao', 'fQa', 'Rw6')):
                print(f"[DBG] base={base_name_dbg} counts_sample={list(counts.items())[:12]} faiss_keys_sample={list(faiss_dist_map.items())[:8]}")
        except Exception:
            pass

        # Build candidate list with priority score
        candidates = []
        for j, shared in counts.items():
            if shared < candidate_min_shared:
                continue

            try:
                meta1 = get_video_metadata(video_files[i])
                meta2 = get_video_metadata(video_files[j])
                if not should_compare(meta1, meta2):
                    continue
                meta_penalty = _meta_penalty(meta1, meta2)
            except Exception:
                meta_penalty = 0.5

            faiss_distance = faiss_dist_map.get(j)
            if faiss_distance is None:
                # FAISS非該当候補は shared 数で粗い疑似距離を与える（小さいほど優先）
                faiss_distance = max(0.0, min(1.0, 1.0 - min(float(shared), 12.0) / 12.0))

            priority_score = float(faiss_distance) + float(META_PENALTY_WEIGHT) * float(meta_penalty)
            # apply final priority-score filter to reduce spurious candidates
            try:
                if float(priority_score) > float(FINAL_PRIORITY_SCORE_THRESHOLD):
                    continue
            except Exception:
                pass
            candidates.append((j, shared, priority_score, faiss_distance, meta_penalty))

        # Promote FAISS neighbors into candidates even if priority_score filtered them out.
        # We cap promoted entries to FAISS_CANDIDATES_PER_FILE to avoid explosion.
        try:
            if faiss_dist_map:
                # faiss_dist_map values are normalized distances in [0,1]
                promoted = [jid for jid, _ in sorted(faiss_dist_map.items(), key=lambda x: x[1])[:FAISS_CANDIDATES_PER_FILE]]
                for j in promoted:
                    if j <= i:
                        continue
                    # skip if already present
                    if any(j == c[0] for c in candidates):
                        continue
                    try:
                        meta1 = get_video_metadata(video_files[i])
                        meta2 = get_video_metadata(video_files[j])
                        if not should_compare(meta1, meta2):
                            continue
                        meta_penalty = _meta_penalty(meta1, meta2)
                    except Exception:
                        meta_penalty = 0.5

                    # forced low priority to ensure promotion (but keep some ordering)
                    forced_priority = 0.05
                    candidates.append((j, counts.get(j, candidate_min_shared), forced_priority, faiss_dist_map.get(j), meta_penalty))
        except Exception:
            pass

        try:
            if any(tok in os.path.basename(vf) for tok in ('chao', 'fQa', 'Rw6')):
                print(f"[DBG] base={os.path.basename(vf)} candidates_pre_cap={candidates[:12]}")
        except Exception:
            pass

        # capルール:
        # - FAISS候補: ファイルごと最大50
        # - meta候補: FAISS候補x3
        try:
            faiss_pairs = min(int(len(faiss_neighbors.get(vf, []))) if faiss_neighbors else 0, FAISS_CANDIDATES_PER_FILE)
        except Exception:
            faiss_pairs = 0

        if faiss_pairs > 0:
            cap = max(candidate_min_shared, META_CANDIDATE_MULTIPLIER * faiss_pairs)
        else:
            cap = max(candidate_min_shared, META_CANDIDATE_MULTIPLIER * FAISS_CANDIDATES_PER_FILE)

        # sort by priority asc, shared desc
        candidates.sort(key=lambda x: (x[2], -x[1]))
        if len(candidates) > cap:
            candidates = candidates[:cap]

        # 2段目: 上位候補についてウィンドウ時系列整合性で再ランク（計算リミットあり）
        try:
            if candidates:
                top_k_rerank = min(len(candidates), 60)
                # get windows count for base video
                try:
                    rec_base = dup_db.get_file_by_path(video_files[i])
                    base_windows = dup_db.get_window_features_for_file(rec_base['file_id']) if rec_base else []
                    base_wcount = max(1, len(base_windows))
                except Exception:
                    base_wcount = 1

                rerank_list = []
                for idx_c in range(top_k_rerank):
                    j, shared, pscore, fd, mp = candidates[idx_c]
                    other_path = video_files[j]
                    match_count, avg_best = _compute_window_alignment(video_files[i], other_path)
                    match_norm = float(match_count) / float(base_wcount)
                    alignment_bonus = WINDOW_ALIGNMENT_WEIGHT * match_norm - WINDOW_DIST_PENALTY * float(avg_best)
                    new_score = float(pscore) - float(alignment_bonus)
                    rerank_list.append((j, shared, new_score, fd, mp, match_count, avg_best))

                # merge reranked back: replace top_k entries with reranked sorted by new_score
                rerank_list.sort(key=lambda x: (x[2], -x[1]))
                for ridx, item in enumerate(rerank_list):
                    j, shared, new_score, fd, mp, mc, ab = item
                    candidates[ridx] = (j, shared, new_score, fd, mp)
                # ensure remaining candidates keep original tuple shape (j, shared, pscore, fd, mp)
        except Exception:
            pass

        total_candidate_pairs += len(candidates)
        if total_candidate_pairs > MAX_TOTAL_CANDIDATE_PAIRS:
            overflow = total_candidate_pairs - MAX_TOTAL_CANDIDATE_PAIRS
            if overflow > 0 and overflow < len(candidates):
                candidates = candidates[:-overflow]
            elif overflow >= len(candidates):
                candidates = []
            total_candidate_pairs = MAX_TOTAL_CANDIDATE_PAIRS

        for j, shared, _pscore, _fd, _mp in candidates:
            a = video_files[i]
            b = video_files[j]
            cached = None
            if pair_cache is not None:
                try:
                    cached = pair_cache.get(a, b)
                except Exception:
                    cached = None
            if cached is True:
                union(i, j)
                continue
            if cached is False:
                continue

            # short-clip fallback: if one side has very few sampled frames,
            # relax matching requirements to improve recall for trims/shorts.
            fa = frame_hash_cache.get(a, [])
            fb = frame_hash_cache.get(b, [])
            small_len = min(len(fa), len(fb))
            _min_matches = int(min_matches)
            _overlap = float(overlap_ratio_min)
            _hash_avg_max = int(hash_distance_avg_max)
            _hash_max = int(hash_distance_max)
            _require_order = require_order
            if small_len <= 4:
                _min_matches = max(2, int(min_matches) - 2)
                _overlap = max(0.08, float(overlap_ratio_min) * 0.5)
                _hash_avg_max = max(int(hash_distance_avg_max), int(hash_distance_avg_max) + 3)
                _hash_max = max(int(hash_distance_max), int(hash_distance_max) + 3)
                _require_order = False
            elif small_len <= 8:
                _min_matches = max(2, int(min_matches) - 1)
                _overlap = max(0.12, float(overlap_ratio_min) * 0.75)

            # Conditional relaxation: if window-alignment shows a reasonably
            # strong signal, relax distance thresholds to allow partial-match
            # to succeed (improves recall for borderline families like chao/ fQa).
            try:
                wc, wavg = _compute_window_alignment(a, b)
                if int(wc) >= 8 and float(wavg) <= 0.09:
                    _hash_avg_max = max(_hash_avg_max, int(hash_distance_avg_max) + 4)
                    _hash_max = max(_hash_max, int(hash_distance_max) + 4)
            except Exception:
                pass

            ok, mcount, overlap = video_partial_match(
                fa,
                fb,
                hash_distance_max=_hash_max,
                hash_distance_avg_max=_hash_avg_max,
                overlap_ratio_min=_overlap,
                min_matches=_min_matches,
                require_order=_require_order,
            )
            # Fallback: if frame-hash partial match fails but window-vector
            # alignment shows strong signal, accept based on window stats.
            try:
                if not ok:
                    # compute window alignment (reuse helper above)
                    try:
                        wc, wavg = _compute_window_alignment(a, b)
                    except Exception:
                        wc, wavg = 0, 1.0
                    # heuristics: require a reasonable number of matching windows
                    # and reasonably low average normalized distance. These values
                    # chosen to capture chao<->fQa like borderline families while
                    # keeping short-file false positives low.
                    if int(wc) >= 8 and float(wavg) <= 0.09:
                        ok = True
                        mcount = int(wc)
                        try:
                            overlap = float(mcount) / float(max(len(fa), 1))
                        except Exception:
                            overlap = 0.0
            except Exception:
                pass
            try:
                if pair_cache is not None:
                    pair_cache.set(a, b, bool(ok), details={"matches": int(mcount), "overlap": float(overlap)})
            except Exception:
                pass
            try:
                # post-filter: avoid short-file vs long-file false positives
                try:
                    max_len = max(len(fa), len(fb))
                    if min(len(fa), len(fb)) <= 4 and max_len > 20 and int(mcount) < min(len(fa), len(fb)):
                        ok = False
                except Exception:
                    pass
                if ok:
                    # persist verified pair into dup_engine DB so grouping can use it
                    try:
                        ra = dup_db.get_file_by_path(a)
                        rb = dup_db.get_file_by_path(b)
                        if ra and rb:
                            pdata = {
                                'ok': True,
                                'matches': int(mcount),
                                'overlap': float(overlap),
                                'method': 'video_partial',
                                'ts': int(time.time()),
                            }
                            dup_db.store_pair_result(ra['file_id'], rb['file_id'], pickle.dumps(pdata))
                    except Exception:
                        pass
                    union(i, j)
            except Exception:
                pass

        if progress_callback and i % 20 == 0:
            progress_callback(i + 1, len(video_files))

    # 5) グループ化
    # Phase 1d: O(n²)全ペアスキャンを廃止。
    # pair_results の反映は FAISS + 部分一致パスで済んでいるため不要。
    # 残すのは union-find からのグループ生成のみ。
    groups: dict[int, list[str]] = {}
    for i, vf in enumerate(video_files):
        r = find(i)
        groups.setdefault(r, []).append(vf)

    return [g for g in groups.values() if len(g) > 1]

def get_video_metadata(filepath, cache=None):
    """動画メタデータ取得（キャッシュ対応）。

    優先: `ffprobe` を呼んで正確な `duration`/`r_frame_rate`/`width`/`height`/`rotation` を取得。
    フォールバック: `cv2.VideoCapture` の値。
    取得結果はキャッシュ (`FastCache.set_metadata`) に保存される。
    """
    if cache is not None and hasattr(cache, 'get_metadata'):
        try:
            cached = cache.get_metadata(filepath)
            if cached:
                return cached
        except Exception:
            pass

    metadata = None
    try:
        cmd = [
            'ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', '-show_streams', filepath
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode == 0 and proc.stdout:
            info = json.loads(proc.stdout)
            fmt = info.get('format', {})
            streams = info.get('streams', [])

            # duration from format (seconds, float) if available
            try:
                duration = float(fmt.get('duration')) if fmt.get('duration') is not None else 0.0
            except Exception:
                duration = 0.0

            # prefer video stream for width/height/fps/rotation
            width = 0
            height = 0
            fps = 0.0
            rotation = 0
            for s in streams:
                if s.get('codec_type') == 'video':
                    try:
                        width = int(s.get('width') or 0)
                        height = int(s.get('height') or 0)
                    except Exception:
                        width = width or 0
                        height = height or 0

                    # r_frame_rate can be like '30000/1001'
                    rfr = s.get('r_frame_rate') or s.get('avg_frame_rate') or None
                    if rfr and isinstance(rfr, str) and '/' in rfr:
                        try:
                            num, den = rfr.split('/')
                            fps = float(num) / float(den) if float(den) != 0 else 0.0
                        except Exception:
                            fps = 0.0
                    else:
                        try:
                            fps = float(rfr) if rfr else 0.0
                        except Exception:
                            fps = 0.0

                    # rotation tag
                    tags = s.get('tags') or {}
                    try:
                        rotation = int(tags.get('rotate') or 0)
                    except Exception:
                        rotation = 0

                    # side_data_list may contain rotation
                    sdl = s.get('side_data_list') or []
                    for sd in sdl:
                        if sd.get('rotation') is not None:
                            try:
                                rotation = int(sd.get('rotation') or rotation)
                            except Exception:
                                pass

                    break

            metadata = {
                'size': os.path.getsize(filepath) if os.path.exists(filepath) else 0,
                'width': width,
                'height': height,
                'duration': duration,
                'fps': fps,
                'rotation': rotation,
            }

    except Exception:
        metadata = None

    # fallback to cv2 if ffprobe failed or returned nothing useful
    if not metadata or metadata.get('duration', 0) == 0.0:
        cap = None
        try:
            cap = cv2.VideoCapture(filepath)
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            duration = (frame_count / fps) if (fps and frame_count) else 0.0
            metadata = {
                'size': os.path.getsize(filepath) if os.path.exists(filepath) else 0,
                'width': width,
                'height': height,
                'duration': duration,
                'fps': fps,
                'rotation': 0,
            }
        except Exception:
            metadata = None
        finally:
            if cap is not None:
                cap.release()

    # persist to cache if available
    try:
        if cache is not None and metadata and hasattr(cache, 'set_metadata'):
            cache.set_metadata(filepath, metadata)
    except Exception:
        pass

    return metadata

def should_compare(meta1, meta2):
    """メタデータによる事前フィルタリング

    解像度重視フィルタ:
    - 解像度比: 2.5倍以内（厳格維持）
    - ファイルサイズ比: 50倍以内（緩和）
    - 動画長比: 50倍以内（緩和）
    """
    if meta1 is None or meta2 is None:
        return True

    # 要件: ハード除外は duration_ratio>20 / resolution_ratio>20 のみ
    # 注意: 一部のファイルで幅/高さやdurationが取得できない(0)場合がある。
    # その場合は誤って巨大比率にならないよう除外判定を保守的に扱う。
    try:
        w1 = int(meta1.get('width') or 0)
        h1 = int(meta1.get('height') or 0)
        w2 = int(meta2.get('width') or 0)
        h2 = int(meta2.get('height') or 0)

        if w1 == 0 or w2 == 0 or h1 == 0 or h2 == 0:
            resolution_ratio = 1.0
        else:
            width_ratio = _safe_ratio(w1, w2, min_denom=1)
            height_ratio = _safe_ratio(h1, h2, min_denom=1)
            resolution_ratio = max(width_ratio, height_ratio)

        if resolution_ratio > METADATA_RESOLUTION_RATIO_MAX:
            return False

        # NOTE: duration によるハード除外はここでは行わない。
        # 短い切り抜き (source vs short clip) を検出したいケースがあるため、
        # duration の差は後続のペナルティ化に任せ、比較自体は行う。
        return True
    except Exception:
        # メタデータ処理に失敗した場合は除外しない
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
def find_duplicates_in_folder(
    folder,
    progress_bar=None,
    progress_callback=None,
    parallel=True,
    use_advanced=False,
    enable_video_partial_match: bool = VIDEO_PARTIAL_MATCH_DEFAULT,
    video_partial_sample_interval_sec: float = VIDEO_PARTIAL_SAMPLE_INTERVAL_SEC,
    video_partial_max_samples: int = VIDEO_PARTIAL_MAX_SAMPLES,
    video_partial_hash_distance_max: int = VIDEO_PARTIAL_HASH_DISTANCE_MAX,
    video_partial_hash_distance_avg_max: int = VIDEO_PARTIAL_HASH_DISTANCE_AVG_MAX,
    video_partial_overlap_ratio_min: float = VIDEO_PARTIAL_OVERLAP_RATIO_MIN,
    video_partial_min_matches: int = VIDEO_PARTIAL_MIN_MATCHES,
    video_partial_candidate_min_shared: int = VIDEO_PARTIAL_CANDIDATE_MIN_SHARED,
    video_partial_require_order: bool = VIDEO_PARTIAL_REQUIRE_ORDER,
    video_partial_avoid_dark_scenes: bool = VIDEO_PARTIAL_AVOID_DARK_SCENES,
    video_partial_dark_trim_ratio: float = VIDEO_PARTIAL_DARK_TRIM_RATIO,
    video_partial_min_contrast_std: float = VIDEO_PARTIAL_MIN_CONTRAST_STD,
):
    image_exts = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff")
    video_exts = VIDEO_EXTS_DEFAULT
    files = get_image_and_video_files(folder, image_exts, video_exts)
    print(f"[PERF] 対象ファイル数: {len(files)}")

    from component.thumbnail.thumbnail_util import FastCache
    cache = FastCache()
    settings_key = (
        f"vpm={int(bool(enable_video_partial_match))}"
        f";vsi={video_partial_sample_interval_sec}"
        f";vms={video_partial_max_samples}"
        f";vhd={video_partial_hash_distance_max}"
        f";vhda={video_partial_hash_distance_avg_max}"
        f";vor={video_partial_overlap_ratio_min}"
        f";vmm={video_partial_min_matches}"
        f";vcs={video_partial_candidate_min_shared}"
        f";vord={int(bool(video_partial_require_order))}"
        f";vdark={int(bool(video_partial_avoid_dark_scenes))}"
        f";vtrim={video_partial_dark_trim_ratio}"
        f";vstd={video_partial_min_contrast_std}"
    )
    cached_groups = cache.get_group_cache(folder, files, use_advanced, settings_key=settings_key)
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

    # 動画の部分一致（切り抜き/画質差/拡張子差）を追加し、誤検出も抑制する
    if enable_video_partial_match:
        try:
            print(
                "[VIDEO PARTIAL] 動画の部分一致判定を適用中 "
                f"(interval={video_partial_sample_interval_sec}s, max={video_partial_max_samples}, "
                f"dist<={video_partial_hash_distance_max}, avg_dist<={video_partial_hash_distance_avg_max}, "
                f"overlap>={video_partial_overlap_ratio_min}, min_matches={video_partial_min_matches}, "
                f"order={video_partial_require_order}, dark_skip={video_partial_avoid_dark_scenes}, "
                f"dark_trim={video_partial_dark_trim_ratio}, min_std={video_partial_min_contrast_std})"
            )

            image_groups: list[list[str]] = []
            candidate_video_groups: list[list[str]] = []
            for g in groups:
                vids = [p for p in g if _is_video_file(p, video_exts)]
                imgs = [p for p in g if not _is_video_file(p, video_exts)]
                if len(imgs) > 1:
                    image_groups.append(imgs)
                if len(vids) > 1:
                    candidate_video_groups.append(vids)

            video_files_all = [f for f, _h in valid_file_hashes if _is_video_file(f, video_exts)]
            video_files_all = list(dict.fromkeys(video_files_all))
            # normalize paths for robust lookup between DB and filesystem
            idx_map = {normalize_path(p): i for i, p in enumerate(video_files_all)}

            # Ensure per-window vectors exist for videos (for FAISS neighbor retrieval).
            # For large collections this is expensive; only do eagerly for reasonably small sets.
            try:
                if len(video_files_all) <= 2000:
                    for v in video_files_all:
                        try:
                            rec = dup_db.get_file_by_path(v)
                            need_make = True
                            if rec:
                                wid_val = rec.get('file_id')
                                if wid_val is not None:
                                    wfeat = dup_db.get_window_features_for_file(int(wid_val))
                                    if wfeat:
                                        need_make = False
                            if not need_make:
                                continue
                            # extract windows and persist
                            try:
                                windows = fe.extract_window_vectors(v, window_sec=10.0, frames_per_window=8, overlap=0.25)
                            except Exception:
                                windows = []
                            meta = get_video_metadata(v, cache=None)
                            try:
                                mtime_ns_val = int(os.path.getmtime(v) * 1e9)
                            except Exception:
                                import time as _t
                                mtime_ns_val = int(_t.time() * 1e9)
                            size_bytes = os.path.getsize(v)
                            duration_ms = int(meta.get('duration', 0) * 1000) if meta else None
                            width = int(meta.get('width') or 0) if meta else None
                            height = int(meta.get('height') or 0) if meta else None
                            file_id = dup_db.upsert_file(v, mtime_ns_val, size_bytes, duration_ms, width, height)
                            window_rows = []
                            for (widx, s_ms, e_ms, vec) in windows:
                                try:
                                    window_rows.append((int(widx), vec.tobytes(), int(s_ms), int(e_ms)))
                                except Exception:
                                    continue
                            if window_rows:
                                dup_db.upsert_window_features(file_id, window_rows)
                        except Exception:
                            continue
            except Exception:
                pass

            parent = list(range(len(video_files_all)))

            def find(x: int) -> int:
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            def union(a: int, b: int) -> None:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[rb] = ra

            frame_hash_cache: dict[str, list[imagehash.ImageHash]] = {}
            pair_cache = PairCache(os.path.join('.thumb_cache', 'pair_cache.db'))

            # FAISSを使って近傍候補を事前取得（利用可能なファイルのみ）
            faiss_neighbors: dict[str, list[str]] = {}
            idx_obj = None
            id_list = []
            try:
                # try to load persisted index first
                idx_obj, id_list = fi.load_index(os.path.join('.thumb_cache', 'faiss_index'))
                if idx_obj is None:
                    idx_obj, id_list = fi.build_index_from_db()
                else:
                    # ensure index includes any new DB features
                    idx_obj, id_list = fi.update_index_from_db(idx_obj, id_list)
                # build mapping file_id -> path
                id_to_path = {}
                for rec in dup_db.all_files():
                    id_to_path[rec['file_id']] = rec['path']

                # for each video in our set, query neighbors if it has a stored vector
                for v in video_files_all:
                    try:
                        rec = dup_db.get_file_by_path(v)
                        if not rec:
                            continue
                        fid = rec['file_id']
                        feat = dup_db.get_feature(fid)
                        if not feat or not feat.get('vec'):
                            continue
                        vec = fi._bytes_to_vector(feat['vec'])
                        if vec is None:
                            continue
                        neighbors = fi.query_index(idx_obj, id_list, vec, k=FAISS_CANDIDATES_PER_FILE)
                        # aggregate by file_id: collect distances per file
                        cand_map = {}
                        for wid, fid2, dist in neighbors:
                            p = id_to_path.get(fid2)
                            if not p or p == v:
                                continue
                            cand_map.setdefault(p, []).append(float(dist))

                        # produce sorted list by average distance (lower better)
                        neigh_paths = []
                        for pth, dlist in cand_map.items():
                            avgd = sum(dlist) / max(1, len(dlist))
                            neigh_paths.append((pth, avgd, len(dlist)))

                        if neigh_paths:
                            neigh_paths.sort(key=lambda x: (x[1], -x[2]))
                            # trim to FAISS_CANDIDATES_PER_FILE
                            neigh_paths = neigh_paths[:FAISS_CANDIDATES_PER_FILE]
                            # store tuples (path, avg_dist, matches)
                            faiss_neighbors[v] = neigh_paths
                    except Exception:
                        continue
                # persist updated index
                try:
                    fi.save_index(os.path.join('.thumb_cache', 'faiss_index'), idx_obj, id_list)
                except Exception:
                    pass
            except Exception:
                faiss_neighbors = {}

            # ----------------------------------------------------------------
            # CLIP-HNSW candidate enrichment
            # (Optional — skip gracefully if clip_extractor not installed)
            # ----------------------------------------------------------------
            clip_vecs: dict[str, np.ndarray] = {}        # path -> 512d L2-normalised
            hnsw_idx_obj = None
            hnsw_id_list: list[int] = []
            hnsw_path_list: list[str] = []
            if _CLIP_AVAILABLE and _ce is not None:
                try:
                    # 1) Extract / cache CLIP features for all video files
                    clip_cache_db_ver = "clip_vitb32_v1"
                    for v in video_files_all:
                        try:
                            rec = dup_db.get_file_by_path(v)
                            if rec:
                                fid_v = rec['file_id']
                                cached_cf = dup_db.get_clip_feature(fid_v)
                                if cached_cf and cached_cf.get('vec'):
                                    arr = np.frombuffer(cached_cf['vec'], dtype=np.float32).copy()
                                    if arr.shape == (512,):
                                        clip_vecs[v] = arr
                                        continue
                        except Exception:
                            pass
                        # extract fresh
                        try:
                            vec = _ce.extract_clip_features(v)
                            if vec is not None and vec.shape == (512,):
                                clip_vecs[v] = vec
                                # persist to DB
                                try:
                                    rec2 = dup_db.get_file_by_path(v)
                                    if rec2:
                                        dup_db.upsert_clip_feature(
                                            rec2['file_id'],
                                            vec.tobytes(),
                                            _ce.FRAMES_FOR_CLIP,
                                            clip_cache_db_ver,
                                        )
                                except Exception:
                                    pass
                        except Exception:
                            pass

                    # 2) Build HNSW index
                    if clip_vecs:
                        paths_with_vec = [p for p in video_files_all if p in clip_vecs]
                        mat = np.vstack([clip_vecs[p] for p in paths_with_vec]).astype(np.float32)
                        hnsw_path_list = paths_with_vec
                        hnsw_id_list = list(range(len(paths_with_vec)))
                        hnsw_idx_obj, _ = fi.build_hnsw_index(mat, hnsw_id_list, M=32, ef_construction=200)
                        try:
                            fi.save_hnsw_index(
                                os.path.join('.thumb_cache', 'faiss_hnsw'),
                                hnsw_idx_obj,
                                hnsw_id_list,
                            )
                        except Exception:
                            pass

                    # 3) Query HNSW per video and merge into faiss_neighbors
                    if hnsw_idx_obj is not None and hnsw_path_list:
                        for v in video_files_all:
                            vec = clip_vecs.get(v)
                            if vec is None:
                                continue
                            hits = fi.query_hnsw(hnsw_idx_obj, hnsw_id_list, vec, k=CLIP_CANDIDATES_PER_FILE + 1)
                            existing = {item[0] if isinstance(item, tuple) else item for item in faiss_neighbors.get(v, [])}
                            new_items: list[tuple[str, float, int]] = []
                            for raw_idx, dist in hits:
                                if raw_idx < 0 or raw_idx >= len(hnsw_path_list):
                                    continue
                                npath = hnsw_path_list[raw_idx]
                                if npath == v:
                                    continue
                                if npath in existing:
                                    continue
                                # L2² dist in HNSW for L2-normalised vecs ≈ 2*(1-cos)
                                clip_cos = max(0.0, 1.0 - dist / 2.0)
                                new_items.append((npath, float(dist), 1))
                                existing.add(npath)
                                if len(new_items) >= CLIP_CANDIDATES_PER_FILE:
                                    break
                            if new_items:
                                existing_list = list(faiss_neighbors.get(v, []))
                                faiss_neighbors[v] = existing_list + new_items
                except Exception as _clip_err:
                    print(f"[CLIP] CLIP-HNSW候補生成でエラー (続行): {_clip_err}")

            # 1) 既存グループの動画部分は「部分一致」で検証して繋ぐ（誤検出を削る）
            for vg in candidate_video_groups:
                for v in vg:
                    if v not in frame_hash_cache:
                        frame_hash_cache[v] = get_video_frame_hashes(
                            v,
                            sample_interval_sec=video_partial_sample_interval_sec,
                            max_samples=video_partial_max_samples,
                            avoid_dark_scenes=video_partial_avoid_dark_scenes,
                            dark_trim_ratio=video_partial_dark_trim_ratio,
                            min_contrast_std=video_partial_min_contrast_std,
                        )
                        # 付加: 128次元ベクトルを生成してDBに保存（失敗しても処理継続）
                        try:
                            # generate per-window vectors (精度優先)
                            try:
                                windows = fe.extract_window_vectors(v, window_sec=10.0, frames_per_window=8, overlap=0.25)
                            except Exception:
                                windows = []

                            meta = get_video_metadata(v, cache=None)
                            try:
                                try:
                                    mtime_ns_val = int(os.path.getmtime(v) * 1e9)
                                except Exception:
                                    mtime_ns_val = int(time.time() * 1e9)
                                size_bytes = os.path.getsize(v)
                                duration_ms = None
                                width = None
                                height = None
                                if meta:
                                    try:
                                        duration_ms = int(meta.get('duration', 0) * 1000)
                                        width = int(meta.get('width') or 0)
                                        height = int(meta.get('height') or 0)
                                    except Exception:
                                        pass
                                file_id = dup_db.upsert_file(v, mtime_ns_val, size_bytes, duration_ms, width, height)

                                # prepare window vecs for DB (window_idx, vec_bytes, start_ms, end_ms)
                                window_rows = []
                                for (widx, s_ms, e_ms, vec) in windows:
                                    try:
                                        window_rows.append((int(widx), vec.tobytes(), int(s_ms), int(e_ms)))
                                    except Exception:
                                        continue

                                if window_rows:
                                    dup_db.upsert_window_features(file_id, window_rows)
                                    # refresh in-memory index with new windows
                                    try:
                                        if idx_obj is not None:
                                            idx_obj, id_list = fi.update_index_from_db(idx_obj, id_list)
                                    except Exception:
                                        pass
                                else:
                                    # fallback: single aggregated vector
                                    hashes = frame_hash_cache.get(v, [])
                                    vec = fe.hashes_to_128vec(hashes)
                                    if vec is not None:
                                        dup_db.upsert_feature(file_id, vec.tobytes(), len(hashes), math.ceil(len(hashes)/5) if hashes else 0)
                                        try:
                                            if idx_obj is not None:
                                                idx_obj, id_list = fi.add_vector_to_index(idx_obj, id_list, file_id, vec)
                                        except Exception:
                                            pass
                            except Exception:
                                pass
                        except Exception:
                            pass

                    for i in range(len(vg)):
                        for j in range(i + 1, len(vg)):
                            v1, v2 = vg[i], vg[j]
                            cached = None
                            try:
                                cached = pair_cache.get(v1, v2) if pair_cache is not None else None
                            except Exception:
                                cached = None
                            if cached is True:
                                union(idx_map[v1], idx_map[v2])
                                continue
                            if cached is False:
                                continue

                            # short-clip fallback for candidate video pairs
                            f1_hashes = frame_hash_cache.get(v1, [])
                            f2_hashes = frame_hash_cache.get(v2, [])
                            small_len2 = min(len(f1_hashes), len(f2_hashes))
                            _min_m2 = int(video_partial_min_matches)
                            _over2 = float(video_partial_overlap_ratio_min)
                            _avg2 = int(video_partial_hash_distance_avg_max)
                            _ord2 = video_partial_require_order
                            if small_len2 <= 4:
                                _min_m2 = max(2, int(video_partial_min_matches) - 2)
                                _over2 = max(0.08, float(video_partial_overlap_ratio_min) * 0.5)
                                _avg2 = max(int(video_partial_hash_distance_avg_max), int(video_partial_hash_distance_avg_max) + 3)
                                _ord2 = False
                            elif small_len2 <= 8:
                                _min_m2 = max(2, int(video_partial_min_matches) - 1)
                                _over2 = max(0.12, float(video_partial_overlap_ratio_min) * 0.75)

                            ok, _m, _o = video_partial_match(
                                f1_hashes,
                                f2_hashes,
                                hash_distance_max=video_partial_hash_distance_max,
                                hash_distance_avg_max=_avg2,
                                overlap_ratio_min=_over2,
                                min_matches=_min_m2,
                                require_order=_ord2,
                            )
                            try:
                                if pair_cache is not None:
                                    pair_cache.set(v1, v2, bool(ok), details={"matches": int(_m), "overlap": float(_o)})
                            except Exception:
                                pass
                            try:
                                # post-filter for short vs long
                                try:
                                    max_len2 = max(len(f1_hashes), len(f2_hashes))
                                    if min(len(f1_hashes), len(f2_hashes)) <= 4 and max_len2 > 20 and int(_m) < min(len(f1_hashes), len(f2_hashes)):
                                        ok = False
                                except Exception:
                                    pass
                                if ok:
                                    try:
                                        ra = dup_db.get_file_by_path(v1)
                                        rb = dup_db.get_file_by_path(v2)
                                        if ra and rb:
                                            pdata = {
                                                'ok': True,
                                                'matches': int(_m),
                                                'overlap': float(_o),
                                                'method': 'video_partial',
                                                'ts': int(time.time()),
                                            }
                                            dup_db.store_pair_result(ra['file_id'], rb['file_id'], pickle.dumps(pdata))
                                    except Exception:
                                        pass
                                    union(idx_map[v1], idx_map[v2])
                            except Exception:
                                pass

            # 2) pHash候補外の「切り抜き」を拾うため、動画全体から追加グループを探索（件数が多い場合はスキップ）
            # 2a) CLIP 4-metric scoring — pHash未検出の意味的重複ペアを結合
            #     CLIP cos_sim >= 0.80 かつ multi-metric score >= FINAL_MULTI_SCORE_THRESHOLD で union
            if _CLIP_AVAILABLE and _DTW_AVAILABLE and clip_vecs and hnsw_idx_obj is not None:
                try:
                    processed_clip_pairs: set[tuple[str, str]] = set()
                    for v in video_files_all:
                        vec_v = clip_vecs.get(v)
                        if vec_v is None:
                            continue
                        if v not in idx_map:
                            continue
                        hits = fi.query_hnsw(hnsw_idx_obj, hnsw_id_list, vec_v, k=CLIP_CANDIDATES_PER_FILE + 1)
                        for raw_idx, raw_dist in hits:
                            if raw_idx < 0 or raw_idx >= len(hnsw_path_list):
                                continue
                            v2 = hnsw_path_list[raw_idx]
                            if v2 == v or v2 not in idx_map:
                                continue
                            pair_key = (min(v, v2), max(v, v2))
                            if pair_key in processed_clip_pairs:
                                continue
                            processed_clip_pairs.add(pair_key)
                            # quick CLIP filter
                            vec_v2 = clip_vecs.get(v2)
                            if vec_v2 is None:
                                continue
                            clip_cos = float(np.dot(vec_v, vec_v2))  # already L2-normalised
                            clip_sim = (clip_cos + 1.0) / 2.0        # map [-1,1] -> [0,1]
                            if clip_sim < 0.72:
                                continue
                            # window DTW score
                            dtw_score = 0.5  # neutral default
                            if _dtw is not None:
                                try:
                                    rec_v = dup_db.get_file_by_path(v)
                                    rec_v2 = dup_db.get_file_by_path(v2)
                                    if rec_v and rec_v2:
                                        wa = dup_db.get_window_features_for_file(rec_v['file_id'])
                                        wb = dup_db.get_window_features_for_file(rec_v2['file_id'])
                                        if wa and wb:
                                            def _load_vecs(rows):
                                                out = []
                                                for r in rows:
                                                    bv = r.get('vec')
                                                    if bv:
                                                        vv = fi._bytes_to_vector(bv)
                                                        if vv is not None:
                                                            out.append(vv)
                                                return np.vstack(out) if out else None
                                            ma = _load_vecs(wa)
                                            mb = _load_vecs(wb)
                                            if ma is not None and mb is not None:
                                                raw_dtw, _, _ = _dtw.compute_window_dtw_score(ma, mb)
                                                dtw_score = 1.0 - raw_dtw  # invert: high=similar
                                except Exception:
                                    pass
                            # pHash similarity
                            phash_sim = 0.5
                            try:
                                fh_v = frame_hash_cache.get(v)
                                fh_v2 = frame_hash_cache.get(v2)
                                if fh_v and fh_v2:
                                    min_len = min(len(fh_v), len(fh_v2))
                                    if min_len > 0:
                                        dists = [fh_v[k] - fh_v2[k] for k in range(min_len)]
                                        avg_d = sum(dists) / min_len
                                        phash_sim = max(0.0, 1.0 - avg_d / (PHASH_SIZE * PHASH_SIZE))
                            except Exception:
                                pass
                            # meta similarity (duration ratio)
                            meta_sim = 0.7
                            try:
                                meta_v = get_video_metadata(v, cache=None)
                                meta_v2 = get_video_metadata(v2, cache=None)
                                if meta_v and meta_v2:
                                    d1 = meta_v.get('duration', 0) or 0
                                    d2 = meta_v2.get('duration', 0) or 0
                                    if d1 > 0 and d2 > 0:
                                        r = min(d1, d2) / max(d1, d2)
                                        meta_sim = float(r)
                            except Exception:
                                pass
                            final_score = (W_CLIP * clip_sim + W_WINDOW * dtw_score
                                           + W_PHASH * phash_sim + W_META * meta_sim)
                            if final_score >= FINAL_MULTI_SCORE_THRESHOLD:
                                union(idx_map[v], idx_map[v2])
                except Exception as _ms_err:
                    print(f"[CLIP] 4-metricスコアリングでエラー (続行): {_ms_err}")

            if len(video_files_all) <= 12000:
                extra_groups = find_partial_duplicate_video_groups(
                    video_files_all,
                    frame_hash_cache,
                    sample_interval_sec=video_partial_sample_interval_sec,
                    max_samples=video_partial_max_samples,
                    hash_distance_max=video_partial_hash_distance_max,
                    hash_distance_avg_max=video_partial_hash_distance_avg_max,
                    overlap_ratio_min=video_partial_overlap_ratio_min,
                    min_matches=video_partial_min_matches,
                    candidate_min_shared=video_partial_candidate_min_shared,
                    require_order=video_partial_require_order,
                    avoid_dark_scenes=video_partial_avoid_dark_scenes,
                    dark_trim_ratio=video_partial_dark_trim_ratio,
                    min_contrast_std=video_partial_min_contrast_std,
                    faiss_neighbors=faiss_neighbors,
                    idx_map=idx_map,
                )
                for eg in extra_groups:
                    base = eg[0]
                    for other in eg[1:]:
                        union(idx_map[base], idx_map[other])
            else:
                print(f"[VIDEO PARTIAL] 動画が多いため全探索をスキップ: {len(video_files_all)}件")

            # 3) Union-Find から動画グループ化
            grouped: dict[int, list[str]] = {}
            for p in video_files_all:
                r = find(idx_map[p])
                grouped.setdefault(r, []).append(p)
            video_groups = [g for g in grouped.values() if len(g) > 1]

            groups = image_groups + video_groups
            print(f"[VIDEO PARTIAL] 画像グループ: {len(image_groups)}, 動画グループ: {len(video_groups)}")
        except Exception as e:
            print(f"[VIDEO PARTIAL] 部分一致判定で例外: {e}")

    group_elapsed = time.time() - group_start
    print(f"[PERF] グループ化: {group_elapsed:.2f}秒, 重複グループ: {len(groups)}")

    if error_files:
        print(f"[WARNING] pHash計算失敗ファイル: {len(error_files)}件 (グループ化から除外)")
        print(f"[INFO] 失敗理由: 明るさフィルタ除外、コーデック非対応、破損ファイル等")

    print(f"[PERF] 総処理時間: {time.time() - hash_start:.2f}秒")

    if len(groups) > 100:
        print(f"[WARNING] グループ数が異常に多い: {len(groups)} (閾値を下げることを推奨)")

    # グループ内を画質スコアでソート（高画質順）
    try:
        sorted_groups = []
        for g in groups:
            try:
                scored = sort_files_by_quality(g)
                sorted_groups.append([p for p, _s in scored])
            except Exception:
                sorted_groups.append(g)
        groups = sorted_groups
    except Exception as e:
        print(f"[QUALITY SORT] 失敗: {e}")

    cache.set_group_cache(folder, files, use_advanced, groups, settings_key=settings_key)
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
