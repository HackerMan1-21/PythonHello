"""
file_util.py
ファイルユーティリティ（依存関係エラー回避）
"""

import os
import shutil
import hashlib

def normalize_path(path):
    """パス正規化"""
    return os.path.abspath(os.path.normpath(path))

def move_to_trash(file_path):
    """ファイルをゴミ箱に移動"""
    try:
        # パスを正規化
        file_path = normalize_path(file_path)

        if os.name == 'nt':
            import send2trash
            send2trash.send2trash(file_path)
        else:
            trash_dir = os.path.expanduser("~/.Trash")
            if not os.path.exists(trash_dir):
                os.makedirs(trash_dir)
            filename = os.path.basename(file_path)
            shutil.move(file_path, os.path.join(trash_dir, filename))
    except ImportError:
        os.remove(file_path)
    except Exception as e:
        print(f"[FILE] ゴミ箱移動エラー: {e}")
        raise

def get_folder_state(folder):
    """フォルダ状態取得"""
    try:
        files = []
        for root, dirs, fs in os.walk(folder):
            for f in fs:
                files.append(os.path.join(root, f))

        # ファイル一覧のハッシュを計算
        file_list = sorted(files)
        hash_str = ''.join(file_list)
        return hashlib.md5(hash_str.encode()).hexdigest()
    except Exception:
        return None


def collect_files(folder: str, exts=None, follow_symlinks: bool = True):
    """フォルダ配下を再帰走査してファイルパス一覧を返す。

    - exts: None または拡張子のイテラブル。'.mp4' のようにピリオド付き、
      あるいは 'mp4' のどちらでも受け付ける。
    - follow_symlinks: シンボリックリンクをたどるか。
    """
    import os
    from typing import List, Iterable, Optional

    exts_set = None
    if exts:
        exts_set = set()
        for e in exts:
            if e is None:
                continue
            ee = e.lower()
            if not ee.startswith('.'):
                ee = '.' + ee
            exts_set.add(ee)

    out: List[str] = []
    root_abs = os.path.abspath(folder)
    for dirpath, dirnames, filenames in os.walk(root_abs, followlinks=follow_symlinks):
        for fn in filenames:
            path = os.path.join(dirpath, fn)
            if exts_set:
                _, e = os.path.splitext(fn)
                if e.lower() not in exts_set:
                    continue
            out.append(path)
    return sorted(out)
