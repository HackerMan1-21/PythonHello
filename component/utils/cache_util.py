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

def save_cache(cache_file, data):
    with open(cache_file, "wb") as f:
        f.write(data)

def load_cache(cache_file, key_file=None):
    try:
        with open(cache_file, "rb") as f:
            return f.read()
    except Exception:
        return None

def delete_cache(cache_file):
    try:
        os.remove(cache_file)
    except Exception:
        pass
