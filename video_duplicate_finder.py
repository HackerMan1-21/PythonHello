import os
import sys
import cv2
import numpy as np
import queue
import threading
import time
import subprocess
import shutil
import tempfile
import pickle
import logging
from PIL import Image
import imagehash
import hashlib
from component.utils.file_util import normalize_path, collect_files, move_to_trash, shutil_move
from component.utils.cache_util import save_cache, load_cache
from component.gui.gui_main import DuplicateFinderGUI
from component.thumbnail.thumbnail_util import get_thumb_cache_file, load_thumb_cache, save_thumb_cache, get_image_thumbnail, get_video_thumbnail, ThumbnailWorker
from component.duplicate_finder import get_image_phash, get_video_phash, get_cache_files, get_features_with_cache, group_by_phash
from component.ai.face_grouping import get_face_encoding, get_video_face_encoding, group_by_face
from PyQt5.QtWidgets import (QApplication, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QFileDialog, QListWidget, QMessageBox, QScrollArea, QGroupBox, QProgressBar, QInputDialog, QDialog, QGridLayout, QLineEdit, QDialogButtonBox, QListWidgetItem, QProgressDialog, QCheckBox)
from PyQt5.QtGui import QPixmap, QImage, QCursor, QIcon
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QObject, QSize, QTimer

# logging設定（1回のみ）
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

# --- サムネイルキャッシュ ---
# これらの関数・クラスは component/thumbnail_util.py へ移動済み

try:
    from send2trash import send2trash
    SEND2TRASH_AVAILABLE = True
except ImportError:
    send2trash = None
    SEND2TRASH_AVAILABLE = False

try:
    import face_recognition
except ImportError:
    face_recognition = None

# --- パス正規化 ---
# これらの関数は component/file_util.py へ移動済み

# --- ファイル収集 ---
# これらの関数は component/file_util.py へ移動済み

# --- 特徴量抽出 ---
# これらの関数は component/duplicate_finder.py, component/face_grouping.py へ移動済み

# --- キャッシュ ---
# これらの関数は component/duplicate_finder.py, component/cache_util.py へ移動済み

# --- グループ化 ---
# これらの関数は component/duplicate_finder.py, component/face_grouping.py へ移動済み

# --- ゴミ箱移動 ---
# これらの関数は component/file_util.py へ移動済み

# --- GUI ---
# DuplicateFinderGUI クラスは component/gui_main.py へ移動

# --- 依存関数・クラスはすべて component/ 配下からimportする形に整理済み ---

if __name__ == "__main__":
    import traceback
    print("[main] 起動開始")
    logging.info("[main] 起動開始")
    try:
        app = QApplication(sys.argv)
        print("[main] QApplication生成済み")
        logging.info("[main] QApplication生成済み")
        try:
            mainWin = DuplicateFinderGUI()
            print("[main] DuplicateFinderGUI生成済み")
            logging.info("[main] DuplicateFinderGUI生成済み")
            try:
                mainWin.show()
                print("[main] show()呼び出し")
                logging.info("[main] show()呼び出し")
                sys.exit(app.exec_())
            except Exception as e:
                print("[main] show()またはexec_()で例外:", e)
                traceback.print_exc()
        except Exception as e:
            print("[main] DuplicateFinderGUI生成で例外:", e)
            traceback.print_exc()
    except Exception as e:
        print("[main] QApplication生成で例外:", e)
        traceback.print_exc()
