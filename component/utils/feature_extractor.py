import subprocess
import json
import math
import os
import shutil
import concurrent.futures
from typing import List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image
import imagehash

# -----------------------------------------------------------------------
# Phase 2b: 256次元ベクトル設定
# pHash 16x16 = 256bit/frame を直接 float32 化し情報損失を排除
# -----------------------------------------------------------------------
VEC_DIM = 256          # 特徴ベクトル次元 (16x16 pHash)
PHASH_WIN_SIZE = 16    # per-window pHash サイズ
FRAMES_PER_HASH = 5    # majority vote に使うフレーム数

# Phase 1e: 並列抽出ワーカー数
_EXTRACT_WORKERS = max(2, min(8, (os.cpu_count() or 4)))


def clamp(x, a, b):
    return max(a, min(b, int(x)))


def _ffprobe_frames_info(path: str) -> Optional[List[dict]]:
    """Return list of frame info dicts using ffprobe (may be large)."""
    if not shutil.which("ffprobe"):
        return None
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_frames",
        "-print_format",
        "json",
        path,
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
        data = json.loads(out)
        return data.get("frames", [])
    except Exception:
        return None


def extract_keyframe_times(path: str, max_frames: int = 10000) -> List[float]:
    """Return list of keyframe times (seconds) using ffprobe; empty if unavailable."""
    try:
        frames = _ffprobe_frames_info(path)
        if not frames:
            return []
        times = []
        for f in frames:
            if f.get("pict_type") == "I":
                t = f.get("best_effort_timestamp_time") or f.get("pkt_pts_time")
                try:
                    times.append(float(t))
                except Exception:
                    continue
                if len(times) >= max_frames:
                    break
        return times
    except Exception:
        return []


def compute_target_frame_count(duration_sec: float) -> int:
    # clamp(duration/90, 30, 80)
    n = duration_sec / 90.0 if duration_sec and duration_sec > 0 else 30
    return clamp(n, 30, 80)


def sample_frames_cv2(path: str, indices: List[int]) -> List[np.ndarray]:
    cap = cv2.VideoCapture(path)
    frames = []
    try:
        for idx in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(max(0, idx)))
            ret, frame = cap.read()
            if not ret or frame is None:
                continue
            frames.append(frame)
    finally:
        cap.release()
    return frames


def uniform_frame_indices(frame_count: int, fps: float, total_frames: int) -> List[int]:
    if fps <= 0 or total_frames <= 0:
        # fallback: evenly spaced by frame index
        return [int(i) for i in np.linspace(0, max(0, total_frames - 1), frame_count)]
    duration = total_frames / fps
    start_t = max(duration * 0.05, 0.0)
    end_t = max(duration * 0.95, start_t)
    span = end_t - start_t
    if span <= 0:
        return [0] * frame_count
    indices = []
    step = span / float(max(frame_count - 1, 1))
    for i in range(frame_count):
        t = start_t + step * i
        indices.append(int(t * fps))
    return indices


def extract_frame_hashes(path: str, desired_count: int) -> List[imagehash.ImageHash]:
    """Extract frame dHash hashes, preferring keyframes via ffprobe, fallback to uniform sampling via cv2."""
    # try cv2 metadata
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return []
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.release()

    # try keyframes first
    key_times = extract_keyframe_times(path)
    hashes = []
    if key_times:
        # sample up to desired_count keyframes uniformly
        if len(key_times) <= desired_count:
            target_times = key_times
        else:
            step = len(key_times) / float(desired_count)
            target_times = [key_times[int(i * step)] for i in range(desired_count)]
        # convert times to frame indices
        indices = [int(t * fps) for t in target_times if fps > 0]
        frames = sample_frames_cv2(path, indices)
        for fr in frames:
            pil = Image.fromarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB))
            h = imagehash.dhash(pil, hash_size=8)
            hashes.append(h)

    # if insufficient, fallback to uniform sampling
    if len(hashes) < desired_count:
        cap = cv2.VideoCapture(path)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        cap.release()
        indices = uniform_frame_indices(desired_count, fps, total)
        frames = sample_frames_cv2(path, indices)
        hashes = []
        for fr in frames:
            pil = Image.fromarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB))
            h = imagehash.dhash(pil, hash_size=8)
            hashes.append(h)

    return hashes[:desired_count]


def hashes_to_128vec(hashes: List[imagehash.ImageHash]) -> Optional[np.ndarray]:
    """後方互換ラッパー: 旧 128 次元 API。内部は hashes_to_vec(256d) を呼び、先頭128dを返す。"""
    v = hashes_to_vec(hashes)
    if v is None:
        return None
    # 256d -> 128d スライス（旧コードとの互換性維持）
    return v[:128].astype(np.float32)


def hashes_to_vec(hashes: List[imagehash.ImageHash]) -> Optional[np.ndarray]:
    """Phase 2a-2d: pHash 16x16 (256bit) の多数決ベクトルを生成。

    旧実装の問題点:
      - dHash 8x8 (64bit) で情報が少なすぎた
      - XOR 結合で情報が打ち消されていた
      - 128次元へのパック変換で精度が落ちていた

    新実装:
      - pHash 16x16 (256bit) フレームごとの生ビットを多数決集計
      - 各ハッシュを 256bit float32 配列として直接ベクトル化
      - [-1, +1] に正規化して FAISS L2 距離に適合
    """
    if not hashes:
        return None

    # 各ハッシュを 256bit bool 配列に変換
    bits_matrix = []
    for h in hashes:
        arr = np.asarray(h.hash, dtype=np.uint8).flatten()
        # PHASH_WIN_SIZE が 16 なら 256bit、8 なら 64bit になる。
        # サイズが異なる場合は 256 にリサイズしてゼロパディング
        if arr.size < VEC_DIM:
            arr = np.pad(arr, (0, VEC_DIM - arr.size))
        else:
            arr = arr[:VEC_DIM]
        bits_matrix.append(arr.astype(np.float32))

    if not bits_matrix:
        return None

    # 多数決: 平均 > 0.5 なら 1、それ以外 0
    mat = np.vstack(bits_matrix)          # shape: (n_frames, 256)
    vote = mat.mean(axis=0)               # shape: (256,)
    majority = (vote >= 0.5).astype(np.float32)

    # [-1, +1] 正規化 (0->-1, 1->+1)
    vec = majority * 2.0 - 1.0
    return vec.astype(np.float32)


import shutil


def extract_window_vectors(path: str, window_sec: float = 10.0, frames_per_window: int = 8, overlap: float = 0.0) -> List[tuple]:
    """Phase 1e + 2a-2d: per-time-window 256-d ベクトルを抽出。

    Returns list of tuples: (window_idx, start_ms, end_ms, vec(np.ndarray shape=(256,)))
    overlap: fraction [0..0.9)

    変更点:
      - pHash 16x16 (256bit) に変更
      - _extract_one_window をモジュールレベル関数化して ProcessPool に対応
      - ただし ProcessPool は fork 問題があるため ThreadPool を使用
        (cv2/PIL の GIL リリースで実質的に並列動作)
    """
    try:
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            return []
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration = (total_frames / fps) if fps > 0 and total_frames > 0 else 0.0
        cap.release()
    except Exception:
        return []

    if duration <= 0:
        hashes = extract_frame_hashes(path, desired_count=max(frames_per_window, 8))
        vec = hashes_to_vec(hashes)
        if vec is None:
            return []
        return [(0, 0, 0, vec)]

    step = window_sec * max(0.1, 1.0 - overlap)

    # ウィンドウ定義リストを生成
    tasks: List[Tuple[str, float, float, float, int, int]] = []
    idx = 0
    t = 0.0
    while t < duration:
        start_t = t
        end_t = min(duration, t + window_sec)
        tasks.append((path, start_t, end_t, fps, total_frames, frames_per_window))
        idx += 1
        t += step

    # Phase 1e: ThreadPoolExecutor で並列抽出
    results_map: dict[int, tuple] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=_EXTRACT_WORKERS) as pool:
        future_to_idx = {pool.submit(_extract_one_window, i, *task): i for i, task in enumerate(tasks)}
        for future in concurrent.futures.as_completed(future_to_idx):
            i = future_to_idx[future]
            try:
                res = future.result()
                if res is not None:
                    results_map[i] = res
            except Exception:
                pass

    return [results_map[i] for i in sorted(results_map)]


def _extract_one_window(
    idx: int,
    path: str,
    start_t: float,
    end_t: float,
    fps: float,
    total_frames: int,
    frames_per_window: int,
) -> Optional[tuple]:
    """1 ウィンドウ分のベクトルを抽出する (ThreadPool ワーカー)。"""
    try:
        if fps > 0:
            start_frame = int(start_t * fps)
            end_frame = max(start_frame + 1, int(end_t * fps))
            frame_count = end_frame - start_frame
            if frame_count <= 0:
                indices = [start_frame]
            else:
                n = min(frames_per_window, max(1, frame_count))
                indices = uniform_frame_indices(n, fps, frame_count)
                indices = [start_frame + i for i in indices]
        else:
            return None

        frames = sample_frames_cv2(path, indices)
        if not frames:
            return None

        hashes = []
        for fr in frames:
            pil = Image.fromarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB))
            # Phase 2a: pHash 16x16 に変更 (旧: dhash 8x8)
            h = imagehash.phash(pil, hash_size=PHASH_WIN_SIZE)
            hashes.append(h)

        # Phase 2b: 256次元ベクトル化
        vec = hashes_to_vec(hashes)
        if vec is None:
            return None

        return (idx, int(start_t * 1000), int(end_t * 1000), vec)
    except Exception:
        return None
