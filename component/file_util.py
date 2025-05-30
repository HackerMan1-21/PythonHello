import os
import shutil
import logging

try:
    from send2trash import send2trash
    SEND2TRASH_AVAILABLE = True
except ImportError:
    send2trash = None
    SEND2TRASH_AVAILABLE = False

def normalize_path(path):
    return os.path.abspath(os.path.expanduser(path))

def move_to_trash(filepath):
    filepath = normalize_path(filepath)
    try:
        if os.path.isdir(filepath):
            if SEND2TRASH_AVAILABLE:
                send2trash(filepath)
            else:
                shutil.rmtree(filepath)
        else:
            if SEND2TRASH_AVAILABLE:
                send2trash(filepath)
            else:
                os.remove(filepath)
    except Exception as e:
        logging.warning(f"ゴミ箱移動失敗: {filepath}: {e}")

def get_folder_state(folder):
    state = []
    for root, dirs, files in os.walk(folder):
        for f in files:
            try:
                path = os.path.join(root, f)
                stat = os.stat(path)
                state.append((path, stat.st_size, stat.st_mtime))
            except Exception:
                continue
    return sorted(state)
