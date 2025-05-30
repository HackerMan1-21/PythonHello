# broken_checker.py
# 壊れ検査: 壊れた動画/画像の検出・リストアップ

import cv2
from PIL import Image
import os

def is_broken_image(filepath):
    """
    画像ファイルが壊れているかどうかを判定
    """
    try:
        img = Image.open(filepath)
        img.verify()
        return False
    except Exception:
        return True

def is_broken_video(filepath):
    """
    動画ファイルが壊れているかどうかを判定
    """
    try:
        cap = cv2.VideoCapture(filepath)
        if not cap.isOpened():
            return True
        ret, _ = cap.read()
        cap.release()
        return not ret
    except Exception:
        return True

def check_broken_videos(folder, video_exts=None, with_reason=False):
    """
    指定フォルダ内の動画ファイルを走査し、壊れているファイルのリストを返す
    with_reason=Trueの場合は(ファイルパス,理由)のタプルリストを返す
    """
    if video_exts is None:
        video_exts = ('.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm', '.mpg', '.mpeg', '.3gp')
    broken_files = []
    for root, dirs, files in os.walk(folder):
        for f in files:
            if os.path.splitext(f)[1].lower() in video_exts:
                path = os.path.join(root, f)
                try:
                    cap = cv2.VideoCapture(path)
                    if not cap.isOpened():
                        if with_reason:
                            broken_files.append((path, "open failed"))
                        else:
                            broken_files.append(path)
                        continue
                    ret, _ = cap.read()
                    cap.release()
                    if not ret:
                        if with_reason:
                            broken_files.append((path, "read failed"))
                        else:
                            broken_files.append(path)
                except Exception as e:
                    if with_reason:
                        broken_files.append((path, f"exception: {e}"))
                    else:
                        broken_files.append(path)
    return broken_files

def check_broken_images(folder, image_exts=None, with_reason=False, log_progress=False):
    """
    指定フォルダ内の画像ファイルを走査し、壊れているファイルのリストを返す
    with_reason=Trueの場合は(ファイルパス,理由,サイズ)のタプルリストを返す
    log_progress=Trueの場合は進捗をprint出力
    """
    if image_exts is None:
        image_exts = ('.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff')
    broken_files = []
    all_files = []
    for root, dirs, files in os.walk(folder):
        for f in files:
            if os.path.splitext(f)[1].lower() in image_exts:
                all_files.append(os.path.join(root, f))
    total = len(all_files)
    for idx, path in enumerate(all_files):
        try:
            img = Image.open(path)
            img.verify()
        except Exception as e:
            size = None
            try:
                size = os.path.getsize(path)
            except Exception:
                pass
            if with_reason:
                broken_files.append((path, f"exception: {e}", size))
            else:
                broken_files.append(path)
        if log_progress and (idx+1) % 100 == 0:
            print(f"[check_broken_images] {idx+1}/{total} files checked...")
    if log_progress:
        print(f"[check_broken_images] Done. Broken: {len(broken_files)}/{total}")
    return broken_files
