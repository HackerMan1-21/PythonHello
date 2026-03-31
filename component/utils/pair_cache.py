import sqlite3
import os
import hashlib
import json
import time
from typing import Optional


class PairCache:
    def __init__(self, db_path: str = ".pair_cache.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pair_cache (
                    id TEXT PRIMARY KEY,
                    file_a TEXT,
                    file_b TEXT,
                    result INTEGER,
                    details TEXT,
                    created_at REAL
                )
                """
            )

    def _make_id(self, a: str, b: str) -> str:
        # canonical order
        x, y = (a, b) if a <= b else (b, a)
        return hashlib.sha1((x + '||' + y).encode('utf-8')).hexdigest()

    def get(self, a: str, b: str) -> Optional[bool]:
        key = self._make_id(a, b)
        try:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.execute("SELECT result FROM pair_cache WHERE id=?", (key,))
                r = cur.fetchone()
                if r is None:
                    return None
                return bool(r[0])
        except Exception:
            return None

    def set(self, a: str, b: str, result: bool, details: Optional[dict] = None) -> None:
        key = self._make_id(a, b)
        details_s = json.dumps(details, ensure_ascii=False) if details is not None else None
        ts = time.time()
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "REPLACE INTO pair_cache (id,file_a,file_b,result,details,created_at) VALUES (?,?,?,?,?,?)",
                    (key, a, b, int(bool(result)), details_s, ts),
                )
        except Exception:
            pass

    def clear(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM pair_cache")
        except Exception:
            pass
