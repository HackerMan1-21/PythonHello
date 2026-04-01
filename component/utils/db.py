import sqlite3
import os
import time
from typing import Optional, List, Dict, Any, Tuple

DB_PATH = os.path.join('.thumb_cache', 'dup_engine.db')
EXTRACTOR_VER = 1


def _conn():
    os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init_db():
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute('''
        CREATE TABLE IF NOT EXISTS files (
            file_id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT UNIQUE NOT NULL,
            mtime_ns INTEGER NOT NULL,
            size_bytes INTEGER NOT NULL,
            duration_ms INTEGER,
            width INTEGER,
            height INTEGER
        )''')

        cur.execute('''
        CREATE TABLE IF NOT EXISTS video_features (
            file_id INTEGER PRIMARY KEY,
            vec BLOB NOT NULL,
            frame_count INTEGER NOT NULL,
            windows_used INTEGER NOT NULL,
            extractor_ver INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            FOREIGN KEY(file_id) REFERENCES files(file_id) ON DELETE CASCADE
        )''')

        cur.execute('''
        CREATE TABLE IF NOT EXISTS video_window_features (
            window_id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL,
            window_idx INTEGER NOT NULL,
            vec BLOB NOT NULL,
            start_ms INTEGER,
            end_ms INTEGER,
            extractor_ver INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            FOREIGN KEY(file_id) REFERENCES files(file_id) ON DELETE CASCADE
        )''')

        cur.execute('CREATE INDEX IF NOT EXISTS idx_window_file ON video_window_features(file_id)')

        cur.execute('''
        CREATE TABLE IF NOT EXISTS pair_results (
            file_id_a INTEGER NOT NULL,
            file_id_b INTEGER NOT NULL,
            result BLOB NOT NULL,
            PRIMARY KEY (file_id_a, file_id_b)
        )''')

        # CLIP semantic feature vectors (512-d, L2-normalised float32)
        cur.execute('''
        CREATE TABLE IF NOT EXISTS clip_features (
            file_id INTEGER PRIMARY KEY,
            vec BLOB NOT NULL,
            num_frames INTEGER NOT NULL,
            extractor_ver INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            FOREIGN KEY(file_id) REFERENCES files(file_id) ON DELETE CASCADE
        )''')

        # Audio fingerprint windows (64-d FFT-based spectral vectors)
        # 120分動画 vs 1分切り抜き検出のためのサブシーケンス音声照合用
        cur.execute('''
        CREATE TABLE IF NOT EXISTS audio_windows (
            aw_id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL,
            window_idx INTEGER NOT NULL,
            vec BLOB NOT NULL,
            start_sec REAL,
            end_sec REAL,
            FOREIGN KEY(file_id) REFERENCES files(file_id) ON DELETE CASCADE
        )''')

        cur.execute('CREATE INDEX IF NOT EXISTS idx_files_mtime_size ON files(mtime_ns, size_bytes)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_pair_a ON pair_results(file_id_a)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_pair_b ON pair_results(file_id_b)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_aw_file ON audio_windows(file_id)')
        # WAL mode for better concurrent read/write performance
        cur.execute('PRAGMA journal_mode=WAL')
        conn.commit()


def upsert_file(path: str, mtime_ns: int, size_bytes: int, duration_ms: Optional[int], width: Optional[int], height: Optional[int]) -> int:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute('''INSERT INTO files(path, mtime_ns, size_bytes, duration_ms, width, height)
                       VALUES(?,?,?,?,?,?)
                       ON CONFLICT(path) DO UPDATE SET mtime_ns=excluded.mtime_ns, size_bytes=excluded.size_bytes, duration_ms=excluded.duration_ms, width=excluded.width, height=excluded.height''',
                    (path, mtime_ns, size_bytes, duration_ms, width, height))
        conn.commit()
        cur.execute('SELECT file_id FROM files WHERE path=?', (path,))
        r = cur.fetchone()
        return int(r[0])


def get_file_by_path(path: str) -> Optional[Dict[str, Any]]:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute('SELECT file_id, path, mtime_ns, size_bytes, duration_ms, width, height FROM files WHERE path=?', (path,))
        r = cur.fetchone()
        if not r:
            return None
        keys = ['file_id','path','mtime_ns','size_bytes','duration_ms','width','height']
        return dict(zip(keys, r))

def get_file_by_id(file_id: int) -> Optional[Dict[str, Any]]:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute('SELECT file_id, path, mtime_ns, size_bytes, duration_ms, width, height FROM files WHERE file_id=?', (file_id,))
        r = cur.fetchone()
        if not r:
            return None
        keys = ['file_id','path','mtime_ns','size_bytes','duration_ms','width','height']
        return dict(zip(keys, r))


def upsert_feature(file_id: int, vec_bytes: bytes, frame_count: int, windows_used: int, extractor_ver: int = EXTRACTOR_VER):
    ts = int(time.time())
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute('''INSERT INTO video_features(file_id, vec, frame_count, windows_used, extractor_ver, updated_at)
                       VALUES(?,?,?,?,?,?)
                       ON CONFLICT(file_id) DO UPDATE SET vec=excluded.vec, frame_count=excluded.frame_count, windows_used=excluded.windows_used, extractor_ver=excluded.extractor_ver, updated_at=excluded.updated_at''',
                    (file_id, vec_bytes, frame_count, windows_used, extractor_ver, ts))
        conn.commit()


def upsert_window_features(file_id: int, window_vecs: list, extractor_ver: int = EXTRACTOR_VER):
    """Replace window feature rows for a file with provided list of tuples:
    window_vecs: list of (window_idx:int, vec_bytes:bytes, start_ms:Optional[int], end_ms:Optional[int])
    """
    ts = int(time.time())
    with _conn() as conn:
        cur = conn.cursor()
        # delete existing windows for file
        cur.execute('DELETE FROM video_window_features WHERE file_id=?', (file_id,))
        for (widx, vecb, s_ms, e_ms) in window_vecs:
            cur.execute('''INSERT INTO video_window_features(file_id, window_idx, vec, start_ms, end_ms, extractor_ver, updated_at)
                           VALUES(?,?,?,?,?,?,?)''', (file_id, int(widx), vecb, s_ms, e_ms, extractor_ver, ts))
        conn.commit()


def get_feature(file_id: int) -> Optional[Dict[str, Any]]:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute('SELECT file_id, vec, frame_count, windows_used, extractor_ver, updated_at FROM video_features WHERE file_id=?', (file_id,))
        r = cur.fetchone()
        if not r:
            return None
        keys = ['file_id','vec','frame_count','windows_used','extractor_ver','updated_at']
        return dict(zip(keys, r))


def get_window_features_for_file(file_id: int) -> List[Dict[str, Any]]:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute('SELECT window_id, file_id, window_idx, vec, start_ms, end_ms, extractor_ver, updated_at FROM video_window_features WHERE file_id=? ORDER BY window_idx', (file_id,))
        rows = cur.fetchall()
        keys = ['window_id','file_id','window_idx','vec','start_ms','end_ms','extractor_ver','updated_at']
        return [dict(zip(keys, r)) for r in rows]


def all_window_features() -> List[Dict[str, Any]]:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute('SELECT window_id, file_id, window_idx, vec, start_ms, end_ms FROM video_window_features')
        rows = cur.fetchall()
        keys = ['window_id','file_id','window_idx','vec','start_ms','end_ms']
        return [dict(zip(keys, r)) for r in rows]


def clear_window_features_all() -> int:
    """Phase 3c/11: 全ウィンドウ特徴量を削除 (128d→256d 移行時などに使用)。

    Returns: 削除件数
    """
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM video_window_features')
        conn.commit()
        return cur.rowcount


# ---------------------------------------------------------------------------
# CLIP feature CRUD
# ---------------------------------------------------------------------------

def upsert_clip_feature(
    file_id: int,
    vec_bytes: bytes,
    num_frames: int,
    extractor_ver: int = EXTRACTOR_VER,
) -> None:
    """Insert or update a CLIP 512-d feature vector for a file."""
    ts = int(time.time())
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            '''
            INSERT INTO clip_features(file_id, vec, num_frames, extractor_ver, updated_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(file_id) DO UPDATE SET
                vec=excluded.vec,
                num_frames=excluded.num_frames,
                extractor_ver=excluded.extractor_ver,
                updated_at=excluded.updated_at
            ''',
            (file_id, vec_bytes, num_frames, extractor_ver, ts),
        )
        conn.commit()


def get_clip_feature(file_id: int) -> Optional[Dict[str, Any]]:
    """Return the stored CLIP feature row for a file, or None."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT file_id, vec, num_frames, extractor_ver, updated_at'
            ' FROM clip_features WHERE file_id=?',
            (file_id,),
        )
        r = cur.fetchone()
        if not r:
            return None
        return dict(zip(['file_id', 'vec', 'num_frames', 'extractor_ver', 'updated_at'], r))


def all_clip_features() -> List[Dict[str, Any]]:
    """Return all rows from clip_features."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute('SELECT file_id, vec, num_frames FROM clip_features')
        rows = cur.fetchall()
        return [dict(zip(['file_id', 'vec', 'num_frames'], r)) for r in rows]


def clear_clip_features_all() -> int:
    """Delete all CLIP feature rows (use when re-extracting with a new model).

    Returns: deleted row count
    """
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM clip_features')
        conn.commit()
        return cur.rowcount


def store_pair_result(file_id_a: int, file_id_b: int, result_bytes: bytes):
    a, b = (file_id_a, file_id_b) if file_id_a <= file_id_b else (file_id_b, file_id_a)
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute('REPLACE INTO pair_results(file_id_a, file_id_b, result) VALUES (?,?,?)', (a, b, result_bytes))
        conn.commit()


def get_pair_result(file_id_a: int, file_id_b: int) -> Optional[bytes]:
    a, b = (file_id_a, file_id_b) if file_id_a <= file_id_b else (file_id_b, file_id_a)
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute('SELECT result FROM pair_results WHERE file_id_a=? AND file_id_b=?', (a, b))
        r = cur.fetchone()
        return r[0] if r else None


def delete_pairs_for_file(file_id: int):
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM pair_results WHERE file_id_a = ? OR file_id_b = ?', (file_id, file_id))
        conn.commit()


def delete_file_and_related(path: str):
    rec = get_file_by_path(path)
    if not rec:
        return
    fid = rec['file_id']
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM video_features WHERE file_id=?', (fid,))
        cur.execute('DELETE FROM pair_results WHERE file_id_a=? OR file_id_b=?', (fid, fid))
        cur.execute('DELETE FROM audio_windows WHERE file_id=?', (fid,))
        cur.execute('DELETE FROM files WHERE file_id=?', (fid,))
        conn.commit()


# ---------------------------------------------------------------------------
# Audio windows CRUD
# ---------------------------------------------------------------------------

def upsert_audio_windows(file_id: int, windows: list) -> None:
    """Replace audio window rows for a file.

    windows: list of (window_idx, start_sec, end_sec, vec)
             vec can be np.ndarray or bytes.
    """
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM audio_windows WHERE file_id=?', (file_id,))
        for item in windows:
            widx, ss, es, vec = item[0], item[1], item[2], item[3]
            try:
                import numpy as _np
                if isinstance(vec, _np.ndarray):
                    vec_bytes = vec.astype(_np.float32).tobytes()
                else:
                    vec_bytes = bytes(vec)
            except Exception:
                vec_bytes = bytes(vec)
            cur.execute(
                'INSERT INTO audio_windows(file_id, window_idx, vec, start_sec, end_sec)'
                ' VALUES(?,?,?,?,?)',
                (file_id, int(widx), vec_bytes, float(ss), float(es)),
            )
        conn.commit()


def get_audio_windows_for_file(file_id: int) -> List[Dict[str, Any]]:
    """Return all audio window rows for a file, ordered by window_idx."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT aw_id, file_id, window_idx, vec, start_sec, end_sec'
            ' FROM audio_windows WHERE file_id=? ORDER BY window_idx',
            (file_id,),
        )
        rows = cur.fetchall()
        keys = ['aw_id', 'file_id', 'window_idx', 'vec', 'start_sec', 'end_sec']
        return [dict(zip(keys, r)) for r in rows]


def has_audio_windows(file_id: int) -> bool:
    """Return True if audio windows are cached for this file."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute('SELECT 1 FROM audio_windows WHERE file_id=? LIMIT 1', (file_id,))
        return cur.fetchone() is not None


def all_files() -> List[Dict[str, Any]]:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute('SELECT file_id, path, mtime_ns, size_bytes, duration_ms, width, height FROM files')
        rows = cur.fetchall()
        keys = ['file_id','path','mtime_ns','size_bytes','duration_ms','width','height']
        return [dict(zip(keys, r)) for r in rows]
