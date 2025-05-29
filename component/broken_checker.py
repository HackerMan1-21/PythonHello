# broken_checker.py
# 壊れ検査: 壊れた動画/画像の検出・リストアップ

import cv2
from PIL import Image

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

# ...必要に応じて壊れファイルのリストアップ関数などを追加...
