import os
import hashlib

def save_cache(cache_file, data):
    """
    キャッシュデータをバイナリで保存する。ファイル名はcache_file。
    """
    tmp_file = cache_file + ".tmp"
    with open(tmp_file, "wb") as f:
        f.write(data)
    os.replace(tmp_file, cache_file)

def load_cache(cache_file, key_file=None):
    """
    キャッシュデータをバイナリで読み込む。ファイル名はcache_file。
    key_fileは未使用（互換性用）。
    """
    if not os.path.exists(cache_file):
        return None
    with open(cache_file, "rb") as f:
        return f.read()
