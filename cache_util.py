import os
import time
from cryptography.fernet import Fernet
import tempfile

def get_key(key_file):
    if not os.path.exists(key_file):
        key = Fernet.generate_key()
        with open(key_file, "wb") as f:
            f.write(key)
        # 隠し属性を付与
        try:
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(key_file, 2)
        except Exception:
            pass
    else:
        with open(key_file, "rb") as f:
            key = f.read()
    return key

def load_cache(cache_file, key_file) -> bytes:
    # キャッシュファイルのパーミッションを変更する
    try:
        os.chmod(cache_file, 0o644)
    except Exception:
        pass  # パーミッション変更に失敗しても無視
    # キャッシュファイルを読み込む
    with open(cache_file, 'rb') as f:
        return f.read()

def save_cache(cache_file, data: bytes, **kwargs) -> None:
    # キャッシュファイルを別の場所に保存する
    cache_dir = os.path.join(os.path.dirname(cache_file), 'cache')
    os.makedirs(cache_dir, exist_ok=True)
    cache_file = os.path.join(cache_dir, os.path.basename(cache_file))
    with open(cache_file, 'wb') as f:
        f.write(data)

def delete_cache(cache_file, key_file):
    removed = False
    for f in [cache_file, key_file]:
        if os.path.exists(f):
            try:
                os.remove(f)
                removed = True
            except Exception:
                pass
    return removed
