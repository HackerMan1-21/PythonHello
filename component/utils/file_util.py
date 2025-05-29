# file_util.py
# ファイル操作: ファイル/ディレクトリ移動・削除・ゴミ箱移動
import os
import shutil

def normalize_path(path):
    # 全角「¥」やスラッシュを半角バックスラッシュに統一し、osの正規化も行う
    if not isinstance(path, str):
        return path
    path = path.replace("\uFFE5", "\\")  # 全角→半角バックスラッシュ
    path = path.replace("¥", "\\")        # 万が一の全角
    path = path.replace("/", os.sep).replace("\\", os.sep)
    return os.path.normpath(path)

def collect_files(folder, exts):
    files = []
    for root, dirs, fs in os.walk(folder):
        for f in fs:
            if f.lower().endswith(exts):
                full_path = os.path.join(root, f)
                files.append(normalize_path(full_path))
    return files

def move_to_trash(filepath):
    filepath = normalize_path(filepath)
    try:
        from send2trash import send2trash
        if os.path.isdir(filepath):
            send2trash(filepath)
        else:
            send2trash(filepath)
    except ImportError:
        if os.path.isdir(filepath):
            shutil.rmtree(filepath)
        else:
            os.remove(filepath)
    except Exception as e:
        import logging
        logging.warning(f"ゴミ箱移動失敗: {filepath}: {e}")

def shutil_move(src, dst):
    shutil.move(src, dst)
