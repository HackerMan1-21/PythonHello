import os
import time
from cryptography.fernet import Fernet
import tempfile
import pickle

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

def load_cache(cache_file, key_file=None):
    import pickle
    try:
        with open(cache_file, 'rb') as f:
            data = f.read()
        if data:
            return pickle.loads(data)
        else:
            return None
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f"[キャッシュ読込エラー] {e}")
        return None

def save_cache(cache_file, data: bytes, **kwargs) -> None:
    # 指定されたキャッシュファイルパスにそのまま保存する
    print(f"[キャッシュ保存] {os.path.abspath(cache_file)} ({len(data)} bytes)")
    dirpath = os.path.dirname(cache_file)
    if dirpath:
        os.makedirs(dirpath, exist_ok=True)
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
