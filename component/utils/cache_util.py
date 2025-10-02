"""
cache_util.py
キャッシュユーティリティ（依存関係エラー回避）
"""

import os
import pickle

def save_cache(cache_file, data):
    """キャッシュファイル保存"""
    try:
        with open(cache_file, 'wb') as f:
            f.write(data)
    except Exception as e:
        print(f"[CACHE] 保存エラー: {e}")

def load_cache(cache_file, key_file=None):
    """キャッシュファイル読み込み"""
    try:
        if os.path.exists(cache_file):
            with open(cache_file, 'rb') as f:
                return f.read()
    except Exception as e:
        print(f"[CACHE] 読み込みエラー: {e}")
    return None