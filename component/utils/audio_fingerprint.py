"""audio_fingerprint.py — FFmpegベース音声ウィンドウ指紋モジュール.

120分動画 vs 1分切り抜き専用設計:
  1. FFmpeg パイプで PCM 音声取得 (8 kHz mono int16)
  2. 30秒ウィンドウ・15秒ステップで 64-d スペクトルベクトル生成
  3. サブシーケンスマッチング: 短側の各ウィンドウを長側全体から最近傍探索
  4. fpcalc (Chromaprint) 不要 — 外部依存なし

パラメータ:
  AUDIO_SR        = 8000    # サンプリングレート (Hz)
  AUDIO_WIN_SEC   = 30.0    # ウィンドウ長 (秒)
  AUDIO_OVERLAP   = 0.5     # オーバーラップ率 (ステップ = 15 秒)
  AUDIO_N_BINS    = 64      # スペクトルビン数 (出力次元)
  AUDIO_FFT_SIZE  = 512     # FFT フレームサイズ
  AUDIO_HOP       = 128     # FFT ホップサイズ

主要 API:
  is_audio_available()                                  -> bool
  extract_audio_windows(path, ...)    -> List[(idx, start_sec, end_sec, vec)]
  audio_subseq_similarity(q_vecs, r_vecs, threshold)   -> float [0, 1]
  compare_audio(vecs_a, vecs_b)                        -> float [0, 1]
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import List, Optional, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# パラメータ
# ---------------------------------------------------------------------------
AUDIO_SR: int = 8000            # FFmpeg リサンプリング先 (Hz)
AUDIO_WIN_SEC: float = 30.0     # ウィンドウ長 (秒)
AUDIO_OVERLAP: float = 0.5      # オーバーラップ率; ステップ = WIN_SEC*(1-OVERLAP) = 15 秒
AUDIO_N_BINS: int = 64          # 出力スペクトルベクトルの次元数
AUDIO_FFT_SIZE: int = 512       # FFT フレームサイズ (64 ms @ 8kHz)
AUDIO_HOP: int = 128            # FFT ホップサイズ (16 ms @ 8kHz)
AUDIO_MAX_WINDOWS: int = 512    # 1ファイルあたりの最大ウィンドウ数 (120分→480ウィンドウ以内)

# サブシーケンス照合の閾値
AUDIO_SIM_THRESHOLD: float = 0.72   # コサイン類似度がこれ以上でウィンドウ一致とみなす
AUDIO_MATCH_RATIO_MIN: float = 0.50 # クエリの何割以上が一致すれば "類似" と判定

# ハード受容閾値 (duplicate_finder で直接 union-find に追加)
AUDIO_HARD_ACCEPT: float = 0.70     # compare_audio がこれ以上なら无条件重複

# AudioWindow 型エイリアス
AudioWindow = Tuple[int, float, float, np.ndarray]  # (window_idx, start_sec, end_sec, vec64)


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def is_audio_available() -> bool:
    """FFmpeg コマンドが利用可能か確認する."""
    return shutil.which('ffmpeg') is not None


def _spectral_vec(samples: np.ndarray, n_bins: int = AUDIO_N_BINS) -> Optional[np.ndarray]:
    """サンプル列 (float32, mono) から L2 正規化スペクトルベクトルを返す.

    stride trick で一括 FFT → 対数スペクトル → mel的ビン集約.
    Python ループなし (ビン集約のみ最小ループ).
    """
    fft_size = AUDIO_FFT_SIZE
    hop = AUDIO_HOP
    n_frames = (len(samples) - fft_size) // hop
    if n_frames <= 0:
        return None

    try:
        from numpy.lib.stride_tricks import as_strided
        shape = (n_frames, fft_size)
        # samples は連続配列が前提; 必要なら .copy() 済み要素を渡すこと
        strides = (samples.strides[0] * hop, samples.strides[0])
        frames = as_strided(samples, shape=shape, strides=strides)  # view, コピーなし
    except Exception:
        return None

    # Hanning 窓 * 一括 rfft
    window_fn = np.hanning(fft_size).astype(np.float32)
    windowed = frames * window_fn               # (n_frames, fft_size)
    spec = np.abs(np.fft.rfft(windowed, axis=1))  # (n_frames, fft_size//2+1)
    mag_avg = spec.mean(axis=0)                 # (fft_size//2+1,)

    # 対数スケールのメル的ビン (80 Hz 〜 4000 Hz)
    n_pos = int(mag_avg.shape[0])
    lo_bin = max(1, int(80.0 * fft_size / AUDIO_SR))
    hi_bin = min(n_pos - 1, int(4000.0 * fft_size / AUDIO_SR))
    if hi_bin <= lo_bin:
        hi_bin = lo_bin + 1

    edges = np.exp(
        np.linspace(np.log(float(lo_bin)), np.log(float(hi_bin)), n_bins + 1)
    ).astype(int)

    vec = np.zeros(n_bins, dtype=np.float32)
    for i in range(n_bins):
        a = int(edges[i])
        b = int(edges[i + 1])
        if b <= a:
            b = a + 1
        b = min(b, n_pos)
        if a < n_pos:
            vec[i] = float(np.log1p(mag_avg[a:b].mean()))

    norm = float(np.linalg.norm(vec))
    if norm < 1e-7:
        return None
    vec /= norm
    return vec


# ---------------------------------------------------------------------------
# 音声ウィンドウ抽出
# ---------------------------------------------------------------------------

def extract_audio_windows(
    path: str,
    window_sec: float = AUDIO_WIN_SEC,
    overlap: float = AUDIO_OVERLAP,
    sr: int = AUDIO_SR,
    n_bins: int = AUDIO_N_BINS,
    max_windows: int = AUDIO_MAX_WINDOWS,
) -> List[AudioWindow]:
    """動画ファイルから音声ウィンドウベクトルリストを抽出する.

    Returns
    -------
    list of (window_idx, start_sec, end_sec, vec_float32_ndarray)
    空リスト: FFmpeg 利用不可 / 音声トラックなし / 処理失敗

    メモリ使用量:
      8 kHz mono int16 で 120分 = 115 MB → float32 変換後は 230 MB
      max_windows=512 で 120分の全ウィンドウ (480本) を網羅可能
    """
    if not is_audio_available():
        return []
    if not os.path.exists(path):
        return []

    sr = int(sr)
    step_sec = float(window_sec) * (1.0 - float(overlap))
    window_samples = int(window_sec * sr)
    step_samples = int(step_sec * sr)

    cmd = [
        'ffmpeg', '-loglevel', 'error',
        '-i', path,
        '-vn',               # ビデオトラックをスキップ
        '-ac', '1',          # モノラルへ変換
        '-ar', str(sr),      # リサンプリング
        '-f', 's16le',       # 符号付き 16-bit PCM (little endian)
        '-',                 # stdout へ出力
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        raw = proc.stdout.read()  # type: ignore[union-attr]
        proc.wait()
    except Exception:
        return []

    if not raw:
        return []

    try:
        # int16 → float32 正規化 (連続配列として確保)
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        samples = np.ascontiguousarray(samples)
    except Exception:
        return []

    results: List[AudioWindow] = []
    window_idx = 0
    pos = 0

    while pos + window_samples <= len(samples) and len(results) < max_windows:
        chunk = np.ascontiguousarray(samples[pos: pos + window_samples])
        vec = _spectral_vec(chunk, n_bins)
        if vec is not None:
            start_sec = float(pos) / sr
            end_sec = float(pos + window_samples) / sr
            results.append((window_idx, start_sec, end_sec, vec))
        pos += step_samples
        window_idx += 1

    return results


# ---------------------------------------------------------------------------
# 類似度計算
# ---------------------------------------------------------------------------

def audio_subseq_similarity(
    query_vecs: List[np.ndarray],
    ref_vecs: List[np.ndarray],
    threshold: float = AUDIO_SIM_THRESHOLD,
) -> float:
    """クエリ側の各ウィンドウが参照側のどこかに一致する割合を返す.

    短い動画 (クエリ) を長い動画 (参照) のウィンドウ集合に照合する
    サブシーケンスマッチング。

    - L2 正規化済みベクトル同士のドット積 = コサイン類似度
    - NumPy 行列積 (Q @ R.T) で O(|q|×|r|) ベクトル化計算
    - |q|=4, |r|=480 程度なら数マイクロ秒

    Returns
    -------
    float [0, 1]: クエリウィンドウのうち threshold 以上で一致した割合
    """
    if not query_vecs or not ref_vecs:
        return 0.0

    try:
        Q = np.vstack(query_vecs).astype(np.float32)   # (nq, d)
        R = np.vstack(ref_vecs).astype(np.float32)     # (nr, d)

        # 再正規化 (DB から読み込んだ際の数値誤差対策)
        q_norms = np.linalg.norm(Q, axis=1, keepdims=True)
        r_norms = np.linalg.norm(R, axis=1, keepdims=True)
        q_norms[q_norms < 1e-7] = 1.0
        r_norms[r_norms < 1e-7] = 1.0
        Q /= q_norms
        R /= r_norms

        # (nq, nr) コサイン類似度行列
        sims = Q @ R.T                              # (nq, nr)
        best_per_query = sims.max(axis=1)           # (nq,) 各クエリの最高類似度
        match_ratio = float((best_per_query >= float(threshold)).mean())
        return match_ratio
    except Exception:
        return 0.0


def compare_audio(
    vecs_a: List[np.ndarray],
    vecs_b: List[np.ndarray],
) -> float:
    """2動画の音声ウィンドウリストから類似度スコアを返す [0, 1].

    短い方をクエリ、長い方を参照としてサブシーケンスマッチングを行う。
    """
    if not vecs_a or not vecs_b:
        return 0.0
    if len(vecs_a) <= len(vecs_b):
        return audio_subseq_similarity(vecs_a, vecs_b)
    return audio_subseq_similarity(vecs_b, vecs_a)
