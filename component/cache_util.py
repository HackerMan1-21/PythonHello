"""
cache_util.py
バイナリキャッシュの保存・読み込みユーティリティ。

主な機能:
- キャッシュファイルの安全な保存（tmp→rename）
- キャッシュファイルの読み込み

依存:
- os, hashlib
"""

import os
import hashlib

def save_cache(cache_file, data):
    """
    キャッシュデータをバイナリで保存する。ファイル名はcache_file。
    """
    tmp_file = cache_file + ".tmp"
    try:
        with open(tmp_file, "wb") as f:
            f.write(data)
        os.replace(tmp_file, cache_file)
    except Exception as e:
        print(f"[save_cache] キャッシュ保存失敗: {cache_file}: {e}")
        raise

def load_cache(cache_file, key_file=None):
    """
    キャッシュデータをバイナリで読み込む。ファイル名はcache_file。
    key_fileは未使用（互換性用）。
    """
    if not os.path.exists(cache_file):
        return None
    try:
        with open(cache_file, "rb") as f:
            return f.read()
    except Exception as e:
        print(f"[load_cache] キャッシュ読込失敗: {cache_file}: {e}")
        return None
