"""DTW-based partial-match scorer for window feature sequences.

Used in Stage 3 verification of the duplicate-detection pipeline.

Key functions
-------------
compute_window_dtw_score(vecs_a, vecs_b) -> (dtw_score, match_count, match_ratio)
    dtw_score  : float [0, 1]   low = similar
    match_count: int             per-window greedy-match count
    match_ratio: float [0, 1]   match_count / min(len_a, len_b)

Design choices
--------------
* Subsequence DTW (all-prefixes start): handles clip shorter than source.
* Sakoe-Chiba band restricts the DP to O(n * bandwidth) instead of O(n*m).
* NumPy vectorised row-wise computation -- no Python loops for distance.
* Per-window greedy match uses percentile-60 adaptive threshold so the
  count is meaningful regardless of absolute vector scale.
"""
from __future__ import annotations

import numpy as np
from typing import Tuple


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _l2_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Return pairwise squared L2 distance matrix (n, m)."""
    # a: (n, d), b: (m, d)
    diff = a[:, None, :] - b[None, :, :]    # (n, m, d)
    return np.sum(diff * diff, axis=2)       # (n, m)


def _dtw_band(d2: np.ndarray, bandwidth: int) -> float:
    """Sakoe-Chiba band DTW on a precomputed cost matrix d2 (n, m).

    Standard end-to-end alignment (both sequences start at [0,0] and end at [n,m]).
    """
    n, m = d2.shape
    INF = np.inf
    # Use 1-indexed DP: cost[0,:] = cost[:,0] = INF except cost[0,0] = 0
    cost = np.full((n + 1, m + 1), INF, dtype=np.float64)
    cost[0, 0] = 0.0

    for i in range(1, n + 1):
        j_lo = max(1, i - bandwidth)
        j_hi = min(m, i + bandwidth)
        for j in range(j_lo, j_hi + 1):
            c = float(d2[i - 1, j - 1])
            prev = min(cost[i - 1, j], cost[i, j - 1], cost[i - 1, j - 1])
            cost[i, j] = c + prev

    return float(cost[n, m])


def _subseq_dtw_band(d2: np.ndarray, bandwidth: int) -> Tuple[float, int, int]:
    """Subsequence DTW: query (rows) can align to any position inside reference (cols).

    The first row is initialised to 0 so the query can start anywhere in the
    reference.  The minimum along the last row of d2 gives the best end point.

    Returns (normalised_distance, start_col_approx, end_col).
    """
    n, m = d2.shape
    INF = np.inf
    cost = np.full((n + 1, m + 1), INF, dtype=np.float64)
    cost[0, :] = 0.0          # free start anywhere in reference

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if abs(i - j) > bandwidth:
                continue
            c = float(d2[i - 1, j - 1])
            prev = min(cost[i - 1, j], cost[i, j - 1], cost[i - 1, j - 1])
            cost[i, j] = c + prev

    # Best end position in the reference
    last_row = cost[n, 1:]   # shape (m,)
    end_j = int(np.argmin(last_row)) + 1       # 1-indexed col
    min_cost = float(last_row[end_j - 1])
    start_j = max(0, end_j - n)
    return min_cost / max(n, 1), start_j, end_j


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_window_dtw_score(
    vecs_a: np.ndarray,
    vecs_b: np.ndarray,
    max_comparisons: int = 6000,
    bandwidth_ratio: float = 0.4,
) -> Tuple[float, int, float]:
    """Compute DTW-based similarity between two window-vector sequences.

    Parameters
    ----------
    vecs_a, vecs_b : float32 arrays of shape (n, dim) and (m, dim)
    max_comparisons: cap on n*m before sub-sampling
    bandwidth_ratio: Sakoe-Chiba band as fraction of the shorter sequence

    Returns
    -------
    (dtw_score, match_count, match_ratio)
        dtw_score  : float [0, 1]   0 = identical
        match_count: greedy per-window match count
        match_ratio: match_count / min(n, m)
    """
    if vecs_a.ndim != 2 or vecs_b.ndim != 2:
        return 1.0, 0, 0.0
    if vecs_a.shape[0] == 0 or vecs_b.shape[0] == 0:
        return 1.0, 0, 0.0

    n, m = vecs_a.shape[0], vecs_b.shape[0]

    # --- Sub-sample if cost matrix would be too large ---
    if n * m > max_comparisons:
        ratio = (max_comparisons / (n * m)) ** 0.5
        step_a = max(1, int(1.0 / ratio))
        step_b = max(1, int(1.0 / ratio))
        vecs_a = vecs_a[::step_a].copy()
        vecs_b = vecs_b[::step_b].copy()
        n, m = vecs_a.shape[0], vecs_b.shape[0]

    vecs_a = np.asarray(vecs_a, dtype=np.float32)
    vecs_b = np.asarray(vecs_b, dtype=np.float32)

    d2 = _l2_matrix(vecs_a, vecs_b)           # (n, m)
    shorter_len = min(n, m)
    bandwidth = max(5, int(shorter_len * bandwidth_ratio))

    # Choose alignment mode
    if n < m * 0.65:
        # vecs_a is the clip -> subsequence DTW
        raw_dist, _s, _e = _subseq_dtw_band(d2, bandwidth)
    elif m < n * 0.65:
        # vecs_b is the clip -> transpose
        raw_dist, _s, _e = _subseq_dtw_band(d2.T, bandwidth)
    else:
        raw_dist = _dtw_band(d2, bandwidth) / max(n + m, 1)

    # Normalise: empirical scale ~ 2.0 for totally dissimilar 256-d unit vecs
    dtw_score = float(np.clip(raw_dist / 2.0, 0.0, 1.0))

    # --- Greedy match count (query ← shorter side) ---
    shorter = vecs_a if n <= m else vecs_b
    longer  = vecs_b if n <= m else vecs_a
    diff_g = shorter[:, None, :] - longer[None, :, :]
    d2_g   = np.sum(diff_g * diff_g, axis=2)           # (ns, nl)
    best_per_q = np.min(d2_g, axis=1)                  # (ns,)

    # adaptive threshold: percentile-60 of the best-match distances
    if best_per_q.size > 0:
        thr = float(np.percentile(best_per_q, 60))
    else:
        thr = 1.0
    thr = max(thr, 1e-6)

    match_count = int((best_per_q <= thr).sum())
    match_ratio = float(match_count) / float(max(shorter.shape[0], 1))

    return dtw_score, match_count, match_ratio
