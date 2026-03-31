"""FAISS wrapper (Phase 3a/3b/3c rebuild)

Changes:
  Phase 3a: IVF-PQ index support (for large ~800K vector collections)
  Phase 3b: 2-stage pipeline -- this module provides FAISS Top-K candidate
            generation only. Window-level verification is done in duplicate_finder.
  Phase 3c: Incremental update (only newly added window_ids are appended)
  Phase 2b: 256-dimensional vector support (backward compatible with old 128d)

API (backward compatible):
  build_index_from_db()          -> (index_obj, id_list)
  query_index(idx, ids, vec, k)  -> list of (window_id, file_id, distance)
  add_vector_to_index(...)       -> (index_obj, id_list)
  update_index_from_db(...)      -> (index_obj, id_list)
  save_index / load_index
"""
from typing import List, Tuple, Optional, Any
import numpy as np
import os
from component.utils import db as dup_db

try:
    import faiss
    _HAS_FAISS = True
except Exception:
    faiss = None
    _HAS_FAISS = False

# Phase 2b: 256d is the new standard. Old 128d vectors are auto-padded.
_VEC_DIM = 256


def _bytes_to_vector(b: bytes) -> Optional[np.ndarray]:
    """Convert bytes -> float32 ndarray(_VEC_DIM,). Accepts both 128d and 256d."""
    if not b:
        return None
    try:
        arr = np.frombuffer(b, dtype=np.float32)
        if arr.size == _VEC_DIM:
            return arr.copy()
        if arr.size == 128:
            # Legacy 128d vector: zero-pad to 256d
            padded = np.zeros(_VEC_DIM, dtype=np.float32)
            padded[:128] = arr
            return padded
        if arr.size > 0:
            # Other sizes: resize via linear interpolation
            src = arr.reshape(1, -1).astype(np.float64)
            xp = np.linspace(0, 1, src.shape[1])
            x = np.linspace(0, 1, _VEC_DIM)
            resized = np.interp(x, xp, src[0]).astype(np.float32)
            return resized
    except Exception:
        pass
    try:
        arr8 = np.frombuffer(b, dtype=np.uint8)
        if arr8.size >= 1:
            n = min(arr8.size, _VEC_DIM)
            v = np.zeros(_VEC_DIM, dtype=np.float32)
            v[:n] = (arr8[:n].astype(np.float32) / 127.5) - 1.0
            return v
    except Exception:
        pass
    return None


def _build_ivfpq(mat: np.ndarray, nlist: int = 0, m: int = 0) -> Any:
    """Phase 3a: Build IVF-PQ index.

    nlist: number of cells (0 = auto: sqrt(n))
    m: number of PQ subspaces (0 = auto, must divide VEC_DIM)
    Falls back to FlatL2 for small collections or when FAISS is unavailable.
    """
    n = mat.shape[0]
    dim = mat.shape[1]

    if not _HAS_FAISS or faiss is None:
        return {'vectors': mat.copy()}

    # Small collection: use FlatL2
    if n < 1000:
        idx = faiss.IndexFlatL2(dim)
        idx.add(mat)  # type: ignore[arg-type]
        return idx

    # Auto-determine IVF-PQ parameters
    if nlist <= 0:
        nlist = max(16, min(4096, int(n ** 0.5)))
    if m <= 0:
        for candidate_m in [32, 16, 8, 4, 2, 1]:
            if dim % candidate_m == 0:
                m = candidate_m
                break
        else:
            m = 1

    nbits = 8  # 256 codebook entries

    try:
        quantizer = faiss.IndexFlatL2(dim)
        index = faiss.IndexIVFPQ(quantizer, dim, nlist, m, nbits)
        index.train(mat)  # type: ignore[arg-type]
        index.add(mat)   # type: ignore[arg-type]
        index.nprobe = max(8, nlist // 10)
        return index
    except Exception:
        idx = faiss.IndexFlatL2(dim)
        idx.add(mat)  # type: ignore[arg-type]
        return idx


def build_index_from_db() -> Tuple[Any, List[Tuple[int, int]]]:
    """Load all window vectors from DB and build an index.

    Returns (index_obj, id_list) where id_list[i] = (window_id, file_id).
    """
    dup_db.init_db()
    rows = dup_db.all_window_features()
    id_list: List[Tuple[int, int]] = []
    vecs: List[np.ndarray] = []

    for rec in rows:
        wid = rec.get('window_id')
        fid = rec.get('file_id')
        v = _bytes_to_vector(rec.get('vec') or b'')
        if wid is not None and fid is not None and v is not None:
            id_list.append((int(wid), int(fid)))
            vecs.append(v)

    if not vecs:
        if _HAS_FAISS and faiss is not None:
            return faiss.IndexFlatL2(_VEC_DIM), []
        return {'vectors': np.zeros((0, _VEC_DIM), dtype=np.float32)}, []

    mat = np.vstack(vecs).astype(np.float32)
    return _build_ivfpq(mat), id_list


def save_index(path_prefix: str, index_obj: Any, id_list: List[Tuple[int, int]]) -> bool:
    """Persist index to disk."""
    os.makedirs(os.path.dirname(path_prefix) or '.', exist_ok=True)
    ids_path = path_prefix + '.ids.npy'
    arr = np.array(id_list, dtype=np.int64) if id_list else np.zeros((0, 2), dtype=np.int64)
    np.save(ids_path, arr)

    if _HAS_FAISS and faiss is not None and hasattr(index_obj, 'ntotal'):
        try:
            faiss.write_index(index_obj, path_prefix + '.idx')
            return True
        except Exception:
            pass
    try:
        vecs = index_obj.get('vectors') if isinstance(index_obj, dict) else None
        if vecs is not None:
            np.save(path_prefix + '.npy', vecs)
            return True
    except Exception:
        pass
    return False


def load_index(path_prefix: str) -> Tuple[Optional[Any], List[Tuple[int, int]]]:
    """Load persisted index if it exists. Returns (index_obj, id_list)."""
    ids_path = path_prefix + '.ids.npy'
    if not os.path.exists(ids_path):
        return None, []
    try:
        raw = np.load(ids_path)
        if raw.ndim == 2 and raw.shape[1] == 2:
            id_list = [(int(x[0]), int(x[1])) for x in raw.tolist()]
        else:
            id_list = []
    except Exception:
        id_list = []

    if _HAS_FAISS and faiss is not None and os.path.exists(path_prefix + '.idx'):
        try:
            idx = faiss.read_index(path_prefix + '.idx')
            return idx, id_list
        except Exception:
            pass
    if os.path.exists(path_prefix + '.npy'):
        try:
            mat = np.load(path_prefix + '.npy')
            return {'vectors': mat}, id_list
        except Exception:
            pass
    return None, id_list


def add_vector_to_index(
    index_obj: Any,
    id_list: List[Tuple[int, int]],
    file_id: int,
    vector: np.ndarray,
) -> Tuple[Any, List[Tuple[int, int]]]:
    """Add a single vector to the index (Phase 3c: incremental).

    IVF-PQ indices are already trained so add() works for new vectors.
    """
    if vector is None:
        return index_obj, id_list
    v = np.asarray(vector, dtype=np.float32)
    if v.size != _VEC_DIM:
        padded = np.zeros(_VEC_DIM, dtype=np.float32)
        padded[:min(v.size, _VEC_DIM)] = v[:_VEC_DIM]
        v = padded
    v = v.reshape(1, -1)

    if _HAS_FAISS and faiss is not None and hasattr(index_obj, 'add'):
        try:
            index_obj.add(v)
            id_list.append((-1, int(file_id)))
            return index_obj, id_list
        except Exception:
            pass
    elif isinstance(index_obj, dict):
        try:
            existing = index_obj.get('vectors')
            if existing is None or existing.shape[0] == 0:
                mat = v.copy()
            else:
                mat = np.vstack([existing, v])
            index_obj['vectors'] = mat
            id_list.append((-1, int(file_id)))
            return index_obj, id_list
        except Exception:
            pass
    return index_obj, id_list


def update_index_from_db(
    index_obj: Any,
    id_list: List[Tuple[int, int]],
) -> Tuple[Any, List[Tuple[int, int]]]:
    """Phase 3c: Append only newly added windows to the index.

    Skips existing window_ids so a full rebuild is unnecessary.
    If new vector count exceeds 10,000, triggers a full rebuild instead.
    """
    dup_db.init_db()
    existing_wids = {int(x[0]) for x in (id_list or []) if isinstance(x, (tuple, list)) and len(x) >= 2}
    rows = dup_db.all_window_features()

    to_add: List[Tuple[int, int, np.ndarray]] = []
    for rec in rows:
        wid_raw = rec.get('window_id')
        if wid_raw is None:
            continue
        wid = int(wid_raw)
        if wid in existing_wids:
            continue
        fid_raw = rec.get('file_id')
        if fid_raw is None:
            continue
        fid = int(fid_raw)
        v = _bytes_to_vector(rec.get('vec') or b'')
        if v is not None:
            to_add.append((wid, fid, v))

    if not to_add:
        return index_obj, id_list

    # Large batch: full rebuild is more efficient
    if len(to_add) > 10_000:
        return build_index_from_db()

    for wid, fid, v in to_add:
        index_obj, id_list = add_vector_to_index(index_obj, id_list, fid, v)
        if id_list and isinstance(id_list[-1], tuple):
            id_list[-1] = (wid, int(fid))

    return index_obj, id_list


# ---------------------------------------------------------------------------
# HNSW index (used for CLIP 512-d vectors, ~20 K vectors per run)
# ---------------------------------------------------------------------------

_CLIP_DIM = 512


def build_hnsw_index(
    mat: np.ndarray,
    id_list: List[int],
    M: int = 32,
    ef_construction: int = 200,
) -> Any:
    """Build a FAISS HNSW index for CLIP-sized (~512-d) vectors.

    HNSW is preferred over IVF-PQ for CLIP because:
    * The collection is small (<=20 K), so memory is ~40 MB, not 1.6 GB.
    * Recall@80 >= 97 % even with ef_search = 128.
    * Supports incremental add() after construction.

    Parameters
    ----------
    mat     : float32 array (N, dim) -- L2-normalised CLIP vectors
    id_list : list of file_id integers, len == N
    M       : HNSW graph connections per node  (higher = better recall / more RAM)
    ef_construction: build-time search width

    Returns (index_obj, id_list) tuple mirroring the window-index API.
    """
    if mat.shape[0] == 0:
        if _HAS_FAISS and faiss is not None:
            idx = faiss.IndexHNSWFlat(mat.shape[1] if mat.ndim == 2 else _CLIP_DIM, M)
            return idx, []
        return {'vectors': mat.copy(), 'type': 'hnsw_fallback'}, []

    dim = mat.shape[1]

    if not _HAS_FAISS or faiss is None:
        return {'vectors': mat.copy(), 'type': 'hnsw_fallback'}, list(id_list)

    try:
        index = faiss.IndexHNSWFlat(dim, M)
        index.hnsw.efConstruction = ef_construction
        index.hnsw.efSearch = 128
        index.add(mat)  # type: ignore[arg-type]
        return index, list(id_list)
    except Exception:
        # Fallback to FlatL2 for CLIP
        idx = faiss.IndexFlatL2(dim)
        idx.add(mat)  # type: ignore[arg-type]
        return idx, list(id_list)


def save_hnsw_index(
    path_prefix: str,
    index_obj: Any,
    id_list: List[int],
) -> bool:
    """Persist HNSW (CLIP) index to path_prefix.clip.idx + .clip.ids.npy."""
    os.makedirs(os.path.dirname(path_prefix) or '.', exist_ok=True)
    ids_path = path_prefix + '.clip.ids.npy'
    np.save(ids_path, np.array(id_list, dtype=np.int64) if id_list else np.zeros(0, dtype=np.int64))

    if _HAS_FAISS and faiss is not None and hasattr(index_obj, 'ntotal'):
        try:
            faiss.write_index(index_obj, path_prefix + '.clip.idx')
            return True
        except Exception:
            pass
    if isinstance(index_obj, dict):
        try:
            vecs = index_obj.get('vectors')
            if vecs is None:
                return False
            np.save(path_prefix + '.clip.npy', vecs)
            return True
        except Exception:
            pass
    return False


def load_hnsw_index(
    path_prefix: str,
) -> Tuple[Optional[Any], List[int]]:
    """Load a previously saved HNSW (CLIP) index."""
    ids_path = path_prefix + '.clip.ids.npy'
    if not os.path.exists(ids_path):
        return None, []
    try:
        raw = np.load(ids_path)
        id_list: List[int] = raw.tolist()
    except Exception:
        id_list = []

    if _HAS_FAISS and faiss is not None and os.path.exists(path_prefix + '.clip.idx'):
        try:
            return faiss.read_index(path_prefix + '.clip.idx'), id_list
        except Exception:
            pass
    if os.path.exists(path_prefix + '.clip.npy'):
        try:
            mat = np.load(path_prefix + '.clip.npy')
            return {'vectors': mat, 'type': 'hnsw_fallback'}, id_list
        except Exception:
            pass
    return None, id_list


def query_hnsw(
    index_obj: Any,
    id_list: List[int],
    vector: Optional[np.ndarray],
    k: int = 80,
) -> List[Tuple[int, float]]:
    """Search the HNSW (CLIP) index.

    Returns list of (file_id, distance) sorted by distance ascending.
    """
    if vector is None or not id_list:
        return []

    v = np.asarray(vector, dtype=np.float32)
    if v.ndim == 1:
        v = v.reshape(1, -1)

    actual_k = min(k, len(id_list))

    if _HAS_FAISS and faiss is not None and hasattr(index_obj, 'search'):
        try:
            D, I = index_obj.search(v, actual_k)
            res: List[Tuple[int, float]] = []
            for dist, idx in zip(D[0].tolist(), I[0].tolist()):
                if idx < 0 or idx >= len(id_list):
                    continue
                res.append((int(id_list[idx]), float(dist)))
            return res
        except Exception:
            pass

    # NumPy fallback
    mat = index_obj.get('vectors') if isinstance(index_obj, dict) else None
    if mat is None or mat.shape[0] == 0:
        return []
    diffs = mat.astype(np.float32) - v
    dists = np.sum(diffs * diffs, axis=1)
    top_idx = np.argsort(dists)[:actual_k]
    return [(int(id_list[int(i)]), float(dists[int(i)])) for i in top_idx]


# ---------------------------------------------------------------------------
# Original window-vector query (kept below for backward compatibility)
# ---------------------------------------------------------------------------

def query_index(
    index_obj: Any,
    id_list: List[Tuple[int, int]],
    vector: np.ndarray,
    k: int = 10,
) -> List[Tuple[int, int, float]]:
    """Phase 3b: k-nearest neighbor search. Returns list of (window_id, file_id, distance).

    duplicate_finder uses results as candidates and filters with
    _compute_window_alignment for precise verification.
    """
    if vector is None or not id_list:
        return []
    v = np.asarray(vector, dtype=np.float32)
    if v.size != _VEC_DIM:
        padded = np.zeros(_VEC_DIM, dtype=np.float32)
        padded[:min(v.size, _VEC_DIM)] = v[:_VEC_DIM]
        v = padded
    q = v.reshape(1, -1)

    actual_k = min(k, len(id_list))

    if _HAS_FAISS and faiss is not None and hasattr(index_obj, 'search'):
        try:
            D, I = index_obj.search(q, actual_k)
            res = []
            for dist, idx in zip(D[0].tolist(), I[0].tolist()):
                if idx < 0 or idx >= len(id_list):
                    continue
                item = id_list[idx]
                wid = int(item[0]) if isinstance(item, (tuple, list)) else -1
                fid = int(item[1]) if isinstance(item, (tuple, list)) else int(item)
                res.append((wid, fid, float(dist)))
            return res
        except Exception:
            pass

    # NumPy fallback (brute-force L2)
    mat = index_obj.get('vectors') if isinstance(index_obj, dict) else None
    if mat is None or mat.shape[0] == 0:
        return []
    diffs = mat.astype(np.float32) - q
    dists = np.sum(diffs * diffs, axis=1)
    idxs = np.argsort(dists)[:actual_k]
    res = []
    for i in idxs.tolist():
        item = id_list[int(i)]
        wid = int(item[0]) if isinstance(item, (tuple, list)) else -1
        fid = int(item[1]) if isinstance(item, (tuple, list)) else int(item)
        res.append((wid, fid, float(dists[int(i)])))
    return res
