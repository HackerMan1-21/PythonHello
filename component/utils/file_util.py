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

def get_folder_state(folder):
    """
    指定フォルダ内のファイル数・合計サイズ・最終更新日時を返す。
    戻り値: (ファイル数, 合計バイト数, 最終更新日時)
    """
    import os
    total_files = 0
    total_bytes = 0
    last_modified = 0
    for root, dirs, files in os.walk(folder):
        for f in files:
            path = os.path.join(root, f)
            try:
                stat = os.stat(path)
                total_files += 1
                total_bytes += stat.st_size
                if stat.st_mtime > last_modified:
                    last_modified = stat.st_mtime
            except Exception:
                pass
    return total_files, total_bytes, last_modified
