import os
import sys
import cv2
import numpy as np
from PIL import Image
import imagehash
import concurrent.futures
import tempfile
from shutil import move as shutil_move
import hashlib
import subprocess
import shutil
import threading
import queue
import pickle
import logging
from PyQt5.QtWidgets import (QApplication, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QFileDialog, QListWidget, QMessageBox, QScrollArea, QGroupBox, QProgressBar, QInputDialog, QDialog, QGridLayout, QLineEdit, QDialogButtonBox, QListWidgetItem, QProgressDialog, QCheckBox)
from PyQt5.QtGui import QPixmap, QImage, QCursor, QIcon
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QObject, QSize, QTimer
from cache_util import save_cache, load_cache

# logging設定（1回のみ）
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

# --- サムネイルキャッシュ ---
def get_thumb_cache_file(folder):
    folder = os.path.abspath(folder)
    h = hashlib.sha1(folder.encode('utf-8')).hexdigest()[:12]
    return f".thumb_cache_{h}.pkl"

thumb_cache = {}
thumb_cache_lock = threading.Lock()

def load_thumb_cache(folder=None):
    global thumb_cache
    try:
        cache_file = get_thumb_cache_file(folder) if folder else ".thumb_cache.pkl"
        with open(cache_file, "rb") as f:
            thumb_cache = pickle.load(f)
    except Exception:
        thumb_cache = {}

def save_thumb_cache(folder=None):
    with thumb_cache_lock:
        try:
            cache_file = get_thumb_cache_file(folder) if folder else ".thumb_cache.pkl"
            with open(cache_file, "wb") as f:
                pickle.dump(thumb_cache, f)
        except Exception:
            pass

def get_image_thumbnail(filepath, size=(240,240)):
    key = (filepath, size)
    with thumb_cache_lock:
        if key in thumb_cache:
            return thumb_cache[key]
    try:
        img = Image.open(filepath).convert("RGB")
        img.thumbnail(size, Image.LANCZOS)
        with thumb_cache_lock:
            thumb_cache[key] = img.copy()
        return img
    except Exception:
        return None

def get_video_thumbnail(filepath, size=(240,240), error_files=None):
    key = (filepath, size)
    with thumb_cache_lock:
        if key in thumb_cache:
            return thumb_cache[key]
    try:
        cap = cv2.VideoCapture(filepath)
        ret, frame = cap.read()
        cap.release()
        if not ret:
            if error_files is not None:
                error_files.append(filepath)
            return None
        img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(img)
        pil_img.thumbnail(size, Image.LANCZOS)
        with thumb_cache_lock:
            thumb_cache[key] = pil_img.copy()
        return pil_img
    except Exception:
        if error_files is not None:
            error_files.append(filepath)
        return None

# --- サムネイル非同期生成用 ---
class ThumbnailWorker(threading.Thread):
    def __init__(self, q, update_cb):
        super().__init__(daemon=True)
        self.q = q
        self.update_cb = update_cb
    def run(self):
        while True:
            item = self.q.get()
            if item is None:
                break
            path, size, is_video, error_files = item
            if is_video:
                get_video_thumbnail(path, size, error_files)
            else:
                get_image_thumbnail(path, size)
            self.update_cb(path)
            self.q.task_done()

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
def normalize_path(path):
    # 全角「¥」やスラッシュを半角バックスラッシュに統一し、osの正規化も行う
    if not isinstance(path, str):
        return path
    path = path.replace("\uFFE5", "\\")  # 全角→半角バックスラッシュ
    path = path.replace("¥", "\\")        # 万が一の全角
    path = path.replace("/", os.sep).replace("\\", os.sep)
    return os.path.normpath(path)

# --- ファイル収集 ---
def collect_files(folder, exts):
    files = []
    for root, dirs, fs in os.walk(folder):
        for f in fs:
            if f.lower().endswith(exts):
                full_path = os.path.join(root, f)
                files.append(normalize_path(full_path))
    return files

# --- 特徴量抽出 ---
def get_image_phash(filepath, folder=None, cache=None):
    filepath = normalize_path(filepath)
    def calc_func(path):
        try:
            img = Image.open(path).convert("RGB")
            return imagehash.phash(img)
        except Exception:
            return None
    if cache is not None:
        if filepath in cache:
            return cache[filepath]
        val = calc_func(filepath)
        cache[filepath] = val
        return val
    return get_features_with_cache(filepath, calc_func, folder)

def get_video_phash(filepath, frame_count=7, folder=None, cache=None):
    filepath = normalize_path(filepath)
    def calc_func(path):
        cap = cv2.VideoCapture(path)
        length = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        hashes = []
        if length == 0 or frame_count == 0:
            cap.release()
            return None
        indices = set([0, length-1, length//2])
        if frame_count > 3:
            for i in range(frame_count-3):
                idx = int(length * (i+1)/(frame_count-2))
                indices.add(min(max(0, idx), length-1))
        indices = sorted(indices)
        for frame_no in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
            ret, frame = cap.read()
            if not ret:
                continue
            try:
                img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                pil_img = Image.fromarray(img)
                hash_val = imagehash.phash(pil_img)
                hashes.append(hash_val)
            except Exception:
                continue
        cap.release()
        if not hashes:
            return None
        return hashes
    if cache is not None:
        if filepath in cache:
            return cache[filepath]
        val = calc_func(filepath)
        cache[filepath] = val
        return val
    return get_features_with_cache(filepath, calc_func, folder)

def get_face_encoding(filepath):
    filepath = normalize_path(filepath)
    def calc_func(path):
        img = Image.open(path).convert("RGB")
        arr = np.array(img)
        if face_recognition:
            faces = face_recognition.face_encodings(arr)
            return faces[0] if faces else np.zeros(128)
        else:
            return np.zeros(128)
    return get_features_with_cache(filepath, calc_func)

def get_video_face_encoding(filepath, sample_frames=7):
    filepath = normalize_path(filepath)
    def calc_func(path):
        cap = cv2.VideoCapture(path)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if frame_count == 0:
            cap.release()
            return None
        indices = np.linspace(0, frame_count-1, sample_frames, dtype=int)
        encodings = []
        for idx in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if not ret:
                continue
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            if face_recognition:
                faces = face_recognition.face_encodings(rgb)
                if faces:
                    encodings.append(faces[0])
        cap.release()
        if encodings:
            return np.mean(encodings, axis=0)
        else:
            return None
    return get_features_with_cache(filepath, calc_func)

def get_cache_files(folder):
    # フォルダパスからハッシュ値を生成し、キャッシュファイル名・キー名を返す
    folder = os.path.abspath(folder)
    h = hashlib.sha1(folder.encode('utf-8')).hexdigest()[:12]
    cache_file = f".video_cache_{h}.enc"
    key_file = f".video_cache_{h}.key"
    return cache_file, key_file

# --- キャッシュ ---
def get_features_with_cache(filepath, calc_func, folder=None):
    filepath = normalize_path(filepath)
    import pickle
    import time
    if folder is None:
        folder = os.path.dirname(filepath)
    cache_file, key_file = get_cache_files(folder)
    from cache_util import save_cache, load_cache
    # キャッシュ読み込み（リトライ付き、truncated時はキャッシュファイル削除）
    cache = None
    for i in range(5):
        try:
            cache_bytes = load_cache(cache_file, key_file)
            if cache_bytes is not None:
                try:
                    cache = pickle.loads(cache_bytes)
                    break
                except Exception as e:
                    print(f"[キャッシュデコードエラー] {e}")
                    # pickle data was truncated などは一時的な書き込み競合の可能性が高いが、
                    # 5回目のリトライでも直らなければキャッシュファイルを削除して再生成
                    if i == 4:
                        try:
                            os.remove(cache_file)
                            print(f"[キャッシュ破損] {cache_file} を削除しました")
                        except Exception:
                            pass
                        cache = {}
                        break
                    time.sleep(0.3)
                    continue
            else:
                cache = {}
                break
        except FileNotFoundError:
            # キャッシュファイルが存在しないのは正常なので何も出力しない
            cache = {}
            break
        except Exception as e:
            print(f"[キャッシュ読み込み予期せぬエラー] {e}")
            cache = {}
            break
    if cache is None:
        cache = {}
    if filepath in cache:
        return cache[filepath]
    result = calc_func(filepath)
    if result is not None:
        cache[filepath] = result
        for _ in range(5):
            try:
                save_cache(cache_file, pickle.dumps(cache))
                break
            except Exception as e:
                print(f"[キャッシュ書き込み予期せぬエラー] {e}")
                time.sleep(0.2)
    return result

# --- グループ化 ---
def group_by_phash(file_hashes, threshold=8):
    groups = []
    used = set()
    for i, (f1, h1) in enumerate(file_hashes):
        if f1 in used or h1 is None:
            continue
        group = [f1]
        for j, (f2, h2) in enumerate(file_hashes):
            if i != j and f2 not in used and h2 is not None:
                # どちらかがlistなら動画同士のみ比較、画像同士のみ比較
                if isinstance(h1, list) and isinstance(h2, list):
                    minlen = min(len(h1), len(h2))
                    try:
                        dist = sum(h1[k] - h2[k] if hasattr(h1[k], '__sub__') else abs(int(h1[k]) - int(h2[k])) for k in range(minlen))
                        dist = abs(dist)
                    except Exception:
                        continue
                    if dist < threshold * minlen:
                        group.append(f2)
                        used.add(f2)
                elif not isinstance(h1, list) and not isinstance(h2, list):
                    try:
                        # imagehashオブジェクト同士ならabs(h1 - h2)でOK
                        if hasattr(h1, '__sub__') and hasattr(h2, '__sub__'):
                            diff = abs(h1 - h2)
                        else:
                            diff = abs(int(h1) - int(h2))
                        if diff < threshold:
                            group.append(f2)
                            used.add(f2)
                    except Exception:
                        continue
        used.add(f1)
        if len(group) > 1:
            groups.append(group)
    return groups

def group_by_face(encodings, paths, threshold=0.6):
    from sklearn.metrics.pairwise import cosine_distances
    groups = []
    used = set()
    for i, (f1, e1) in enumerate(zip(paths, encodings)):
        if f1 in used or e1 is None:
            continue
        group = [f1]
        for j, (f2, e2) in enumerate(zip(paths, encodings)):
            if i != j and f2 not in used and e2 is not None:
                dist = cosine_distances([e1], [e2])[0][0]
                if dist < threshold:
                    group.append(f2)
                    used.add(f2)
        used.add(f1)
        if len(group) > 1:
            groups.append(group)
    return groups

# --- ゴミ箱移動 ---
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

# --- GUI ---
class DuplicateFinderGUI(QWidget):
    def __init__(self):
        super().__init__()
        self.init_ui()

    def init_ui(self):
        font_css = "font-size:20px;font-weight:bold;padding:8px 0 8px 0;color:#00ffe7;text-shadow:0 0 8px #00ffe7;font-family:'Meiryo UI','Consolas','Fira Mono',monospace;"
        self.setStyleSheet(f'''
            QWidget {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0f2027, stop:0.5 #2c5364, stop:1 #232526);
                color: #00ffe7;
                font-family: "Meiryo UI", "Consolas", "Fira Mono", monospace;
                font-size: 14px;
                letter-spacing: 1px;
            }}
            QLabel {{
                color: #00ffe7;
                text-shadow: 0 0 6px #00ffe7, 0 0 2px #00ffe7;
                font-family: "Meiryo UI", "Consolas", "Fira Mono", monospace;
            }}
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #232526, stop:1 #0f2027);
                color: #00ffe7;
                border: 2px solid #00ffe7;
                border-radius: 10px;
                padding: 8px 16px;
                font-size: 15px;
                font-family: "Meiryo UI", "Consolas", "Fira Mono", monospace;
                font-weight: bold;
                text-shadow: 0 0 6px #00ffe7;
                box-shadow: 0 0 12px #00ffe733;
                transition: all 0.2s;
            }}
            QPushButton:hover {{
                background: #00ffe7;
                color: #232526;
                border: 2px solid #00ffe7;
                box-shadow: 0 0 24px #00ffe7;
            }}
            QPushButton:pressed {{
                background: #232526;
                color: #00ffe7;
                border: 2px solid #00ffe7;
            }}
            QProgressBar {{
                background: #232526;
                border: 2px solid #00ffe7;
                border-radius: 8px;
                text-align: center;
                color: #00ffe7;
                font-size: 14px;
                font-family: "Meiryo UI", "Consolas", "Fira Mono", monospace;
            }}
            QProgressBar::chunk {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00ffe7, stop:1 #00ff99);
                border-radius: 8px;
                box-shadow: 0 0 16px #00ffe7;
            }}
            QScrollArea {{
                background: transparent;
                border: none;
            }}
        ''')
        layout = QVBoxLayout()
        self.folder_label = QLabel("フォルダ未選択")
        self.folder_label.setStyleSheet(font_css)
        self.select_btn = QPushButton("[ フォルダ選択 ]")
        self.select_btn.setStyleSheet("font-size:17px;font-weight:bold;background:transparent;color:#00ffe7;border:2px solid #00ffe7;")
        self.select_btn.clicked.connect(self.select_folder)
        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress_time_label = QLabel("")
        self.progress_time_label.setStyleSheet("font-size:13px;color:#00ff99;padding:2px 0 8px 0;text-shadow:0 0 8px #00ff99;")
        # ETA（残り予測時間）表示用
        self.eta_label = QLabel("")
        self.eta_label.setStyleSheet("font-size:13px;color:#ffb300;padding:2px 0 8px 0;text-shadow:0 0 8px #ffb300;")
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout()
        self.content_widget.setLayout(self.content_layout)
        self.scroll_area.setWidget(self.content_widget)
        self.delete_btn = QPushButton("[ 選択ファイルをゴミ箱/移動 ]")
        self.delete_btn.setStyleSheet("font-size:17px;font-weight:bold;background:transparent;color:#ff00c8;border:2px solid #ff00c8;")
        self.delete_btn.clicked.connect(self.delete_selected)
        self.delete_btn.setEnabled(False)
        # --- ここから: モード切替ボタンを常時表示 ---
        self.dup_check_btn = QPushButton("重複チェック")
        self.dup_check_btn.setStyleSheet("font-size:16px;color:#00ffe7;border:2px solid #00ffe7;border-radius:8px;padding:8px;")
        self.dup_check_btn.clicked.connect(self.find_duplicates)
        self.face_group_btn = QPushButton("顔でグループ化して振り分け")
        self.face_group_btn.setStyleSheet("font-size:16px;color:#00ff99;border:2px solid #00ff99;border-radius:8px;padding:8px;")
        self.face_group_btn.clicked.connect(self.face_grouping_and_move)
        self.mp4_tool_btn = QPushButton("MP4修復/変換")
        # --- 再読み込みボタンを下部に移動 ---
        layout.addWidget(self.folder_label)
        layout.addWidget(self.select_btn)
        layout.addWidget(self.progress)
        layout.addWidget(self.progress_time_label)
        layout.addWidget(self.eta_label)
        layout.addWidget(self.scroll_area)
        layout.addWidget(self.delete_btn)
        self.reload_btn = QPushButton("再読み込み")
        self.reload_btn.setStyleSheet("font-size:14px;color:#fff;background:#222;border:1px solid #444;border-radius:6px;padding:4px 8px;")
        self.reload_btn.clicked.connect(self.reload_folder)
        layout.addWidget(self.reload_btn)
        self.setLayout(layout)
        self.selected_paths = set()
        self.auto_reload_timer = QTimer(self)
        self.auto_reload_timer.setInterval(3000)  # 3秒ごとに監視
        self.auto_reload_timer.timeout.connect(self.check_folder_update)
        self.last_folder_state = None

        # ETA（残り予測時間）表示
        def update_eta(start_time, done, total):
            if done > 0:
                elapsed = time.time() - start_time
                remain = total - done
                speed = elapsed / done
                eta = int(remain * speed)
                if eta > 0:
                    m, s = divmod(eta, 60)
                    self.eta_label.setText(f"残り目安: {m}分{s}秒")
                else:
                    self.eta_label.setText("")
            else:
                self.eta_label.setText("")

    def on_thumbnail_clicked(self):
        btn = self.sender()
        path = btn.property("filepath")
        if path in self.selected_paths:
            self.selected_paths.remove(path)
            btn.setStyleSheet("")
        else:
            self.selected_paths.add(path)
            btn.setStyleSheet("border: 3px solid red;")
        # 選択が1つ以上なら削除ボタン有効
        self.delete_btn.setEnabled(bool(self.selected_paths))

    def find_duplicates(self):
        self.last_mode = 'dup'
        self.thumb_buttons = []
        video_exts = ('.mp4', '.avi', '.mov', '.mkv', '.wmv')
        image_exts = ('.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff')
        video_files = collect_files(self.folder, video_exts)
        image_files = collect_files(self.folder, image_exts)
        file_hashes = []
        total = len(video_files) + len(image_files)
        self.progress.setMaximum(total)
        self.progress_time_label.setText("")
        start_time = time.time()
        error_files = []
        # --- キャッシュ一度だけ読み込み ---
        cache_file, key_file = get_cache_files(self.folder)
        cache_dict = load_cache(cache_file, key_file)
        # --- サムネイルキャッシュ一度だけ読み込み ---
        load_thumb_cache()
        # --- サムネイル非同期生成 ---
        thumb_q = queue.Queue()
        error_files = []
        def update_thumb(_):
            self.update()  # repaint→update
        # スレッド数: 論理CPU数 or 4
        num_threads = min(4, os.cpu_count() or 1)
        thumb_workers = [ThumbnailWorker(thumb_q, update_thumb) for _ in range(num_threads)]
        for w in thumb_workers:
            w.start()
        # 画像
        for idx, f in enumerate(image_files):
            h = get_image_phash(f, folder=self.folder, cache=cache_dict)
            file_hashes.append((f, h))
            thumb_q.put((f, (240,240), False, error_files))
            self.progress.setValue(idx+1)
            update_eta(start_time, idx+1, total)
            QApplication.processEvents()
        # 動画
        for idx, f in enumerate(video_files):
            h = get_video_phash(f, folder=self.folder, cache=cache_dict)
            file_hashes.append((f, h))
            thumb_q.put((f, (240,240), True, error_files))
            self.progress.setValue(len(image_files)+idx+1)
            update_eta(start_time, len(image_files)+idx+1, total)
            QApplication.processEvents()
        thumb_q.join()
        for _ in thumb_workers:
            thumb_q.put(None)
        # --- キャッシュ一度だけ保存 ---
        save_cache(cache_file, key_file, cache_dict)
        save_thumb_cache(self.folder)
        file_hashes = [(f, h) for f, h in file_hashes if h is not None]
        self.groups = group_by_phash(file_hashes)
        # 既存の内容をクリア
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self.thumb_buttons = []
        self.selected_paths = set()
        if not self.groups:
            label = QLabel("重複動画・画像は見つかりませんでした")
            label.setStyleSheet("font-size:20px;color:#00ff99;font-weight:bold;padding:20px;text-shadow:0 0 12px #00ff99;")
            self.content_layout.addWidget(label)
        else:
            for group in self.groups:
                if len(group) < 2:
                    continue
                group_label = QLabel("--- 重複グループ ---")
                group_label.setStyleSheet("font-size:16px;color:#ff00c8;font-weight:bold;padding:8px 0 8px 0;text-shadow:0 0 8px #ff00c8;")
                self.content_layout.addWidget(group_label)
                # --- 横並び・折り返しレイアウト ---
                grid = QGridLayout()
                grid.setHorizontalSpacing(12)
                grid.setVerticalSpacing(16)
                max_col = 4
                btns = []
                for idx, f in enumerate(group):
                    vbox = QVBoxLayout()
                    # サムネイル
                    if f.lower().endswith(video_exts):
                        thumb_img = get_video_thumbnail(f, size=(140,140), error_files=error_files)
                    else:
                        thumb_img = get_image_thumbnail(f, size=(140,140))
                    if thumb_img is not None:
                        rgb_img = thumb_img.convert("RGB")
                        w, h = rgb_img.size
                        data = rgb_img.tobytes()
                        qimg = QImage(data, w, h, w*3, QImage.Format_RGB888)
                        pixmap = QPixmap.fromImage(qimg)
                        icon = QIcon(pixmap.scaled(140, 140, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                        btn = QPushButton()
                        btn.setIcon(icon)
                        btn.setIconSize(QSize(140,140))
                        btn.setProperty("filepath", f)
                        btn.clicked.connect(self.on_thumbnail_clicked)
                        btn.setStyleSheet("background:transparent;border:2px solid #00ffe7;border-radius:10px;box-shadow:0 0 8px #00ffe7;")
                        vbox.addWidget(btn)
                        btns.append(btn)
                    else:
                        btn = QPushButton(f)
                        btn.setProperty("filepath", f)
                        btn.clicked.connect(self.on_thumbnail_clicked)
                        btn.setStyleSheet("background:transparent;border:2px solid #00ffe7;border-radius:10px;box-shadow:0 0 8px #00ffe7;")
                        vbox.addWidget(btn)
                        btns.append(btn)
                    # ファイル名（長い場合は省略）
                    fname = os.path.basename(f)
                    maxlen = 18
                    if len(fname) > maxlen:
                        fname_disp = fname[:8] + '...' + fname[-7:]
                    else:
                        fname_disp = fname
                    label_name = QLabel(fname_disp)
                    label_name.setAlignment(Qt.AlignCenter)
                    label_name.setStyleSheet("font-size:12px;color:#00ffe7;font-weight:bold;text-shadow:0 0 6px #00ffe7;")
                    vbox.addWidget(label_name)
                    # ファイルサイズ
                    try:
                        size = os.path.getsize(f)
                        size_mb = size / 1024 / 1024
                        size_str = f"{size_mb:.2f} MB"
                    except Exception:
                        size_str = "-"
                    label_size = QLabel(size_str)
                    label_size.setAlignment(Qt.AlignCenter)
                    label_size.setStyleSheet("font-size:11px;color:#00ff99;")
                    vbox.addWidget(label_size)
                    # ファイルパス（折り返し・クリックでフォルダを開く）
                    label_path = QLabel(f)
                    label_path.setAlignment(Qt.AlignCenter)
                    label_path.setWordWrap(True)
                    label_path.setStyleSheet("font-size:10px;color:#00ff99;text-shadow:0 0 6px #00ff99;max-width:140px;")
                    label_path.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextBrowserInteraction)
                    label_path.mousePressEvent = lambda e, path=f: self.open_folder_of_file(path)
                    vbox.addWidget(label_path)
                    # 個別ゴミ箱ボタン（アイコンのみ、幅最小）
                    del_btn = QPushButton("🗑")
                    del_btn.setFixedWidth(28)
                    del_btn.setStyleSheet("background:#232526;color:#ff00c8;border:2px solid #ff00c8;border-radius:8px;font-size:16px;font-weight:bold;padding:0 2px;")
                    del_btn.clicked.connect(lambda _, path=f: self.delete_single_file(path))
                    vbox.addWidget(del_btn)
                    vbox.addStretch()
                    wgt = QWidget()
                    wgt.setLayout(vbox)
                    row = idx // max_col
                    col = idx % max_col
                    grid.addWidget(wgt, row, col)
                self.thumb_buttons.append(btns)
                group_widget = QWidget()
                group_widget.setLayout(grid)
                self.content_layout.addWidget(group_widget)
        # コーデック未対応ファイル警告
        if error_files:
            # ファイル名のみ抽出して表示（パスは長い場合が多いため）
            filelist = '\n'.join([os.path.basename(f) for f in error_files])
            QMessageBox.warning(self, "動画コーデック未対応", f"一部動画ファイルはコーデック未対応のためサムネイル・重複判定できません:\n{filelist}")
        self.content_layout.addStretch()
        self.delete_btn.setEnabled(False)
        self.progress_time_label.setText("")

        # エラーがあれば警告ダイアログ
        if error_files:
            QMessageBox.warning(self, "警告", "一部の動画ファイルはコーデックに対応していないためスキップされました:\n" + "\n".join(error_files))

    def delete_selected(self):
        if not self.selected_paths:
            QMessageBox.information(self, "削除", "ファイルを選択してください")
            return
        # 削除方法選択ダイアログ
        msg = QMessageBox(self)
        msg.setWindowTitle("ファイル移動/削除方法選択")
        msg.setText("選択ファイルをどうしますか？")
        trash_btn = msg.addButton("ゴミ箱に移動", QMessageBox.AcceptRole)
        move_btn = msg.addButton("別フォルダに移動", QMessageBox.ActionRole)
        cancel_btn = msg.addButton("キャンセル", QMessageBox.RejectRole)
        msg.setDefaultButton(trash_btn)
        msg.exec_()
        if msg.clickedButton() == cancel_btn:
            return
        failed = []
        for path in list(self.selected_paths):
            try:
                if os.path.isdir(path):
                    move_to_trash(path)
                elif os.path.isfile(path):
                    move_to_trash(path)
                else:
                    failed.append(path)
            except Exception as e:
                failed.append(f"{path} : {e}")
        # エラー集約表示
        if failed:
            self.progress_time_label.setText(f"削除エラー: {len(failed)}件")
        else:
            self.progress_time_label.setText("選択ファイルをゴミ箱に移動しました")
        self.find_duplicates()

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "フォルダ選択")
        if folder:
            self.folder = folder
            self.folder_label.setText(folder)
            # --- モード選択ダイアログ ---
            dlg = QDialog(self)
            dlg.setWindowTitle("モード選択")
            vbox = QVBoxLayout()
            label = QLabel("どちらのモードで開始しますか？\n(あとからいつでも切り替え可能)")
            label.setStyleSheet("font-size:15px;color:#00ffe7;padding:8px;")
            vbox.addWidget(label)
            btn_dup = QPushButton("重複チェック")
            btn_dup.setStyleSheet("font-size:16px;color:#00ffe7;border:2px solid #00ffe7;border-radius:8px;padding:8px;")
            btn_face = QPushButton("顔でグループ化して振り分け")
            btn_face.setStyleSheet("font-size:16px;color:#00ff99;border:2px solid #00ff99;border-radius:8px;padding:8px;")
            vbox.addWidget(btn_dup)
            vbox.addWidget(btn_face)
            dlg.setLayout(vbox)
            selected = {'mode': None}
            def choose_dup():
                selected['mode'] = 'dup'
                dlg.accept()
            def choose_face():
                selected['mode'] = 'face'
                dlg.accept()
            btn_dup.clicked.connect(choose_dup)
            btn_face.clicked.connect(choose_face)
            dlg.exec_()
            self.last_folder_state = self.get_folder_state()
            self.auto_reload_timer.start()
            if selected['mode'] == 'face':
                self.face_grouping_and_move()
            else:
                self.find_duplicates()

    def get_folder_state(self):
        # フォルダ内のファイル名＋サイズ＋更新時刻のリストで状態を表現
        state = []
        for root, dirs, files in os.walk(self.folder):
            for f in files:
                try:
                    path = os.path.join(root, f)
                    stat = os.stat(path)
                    state.append((path, stat.st_size, stat.st_mtime))
                except Exception:
                    continue
        return sorted(state)

    def check_folder_update(self):
        if not self.folder:
            return
        new_state = self.get_folder_state()
        if self.last_folder_state != new_state:
            self.last_folder_state = new_state
            self.find_duplicates()

    def open_folder_of_file(self, path):
        # ファイルのあるフォルダをエクスプローラーで開く
        folder = os.path.dirname(path)
        if os.path.exists(folder):
            import subprocess
            subprocess.Popen(f'explorer "{folder}"')

    def delete_single_file(self, path):
        if not path or not os.path.isfile(path):
            QMessageBox.warning(self, "削除エラー", f"ファイルが存在しません: {path}")
            return
        try:
            move_to_trash(path)
            QMessageBox.information(self, "削除", f"{os.path.basename(path)} をゴミ箱に移動しました")
            self.find_duplicates()
        except Exception as e:
            QMessageBox.warning(self, "削除エラー", f"{path} : {e}")

    def move_group_popup(self, group):
        dialog = QDialog(self)
        dialog.setWindowTitle("重複グループ内ファイルの情報")
        layout = QVBoxLayout()
        info = QLabel("ファイルパスをクリックでフォルダを開けます。ドラッグ＆ドロップはエクスプローラーで行ってください。")
        info.setStyleSheet("color:#00ffe7;font-size:13px;padding:4px;")
        layout.addWidget(info)
        grid = QGridLayout()
        max_col = 2
        group_checkboxes = []
        for idx, f in enumerate(group):
            vbox = QVBoxLayout()
            fname = os.path.basename(f)
            maxlen = 18
            if len(fname) > maxlen:
                fname_disp = fname[:8] + '...' + fname[-7:]
            else:
                fname_disp = fname
            # サムネイル
            if f.lower().endswith(('.mp4', '.avi', '.mov', '.mkv', '.wmv')):
                thumb_img = get_video_thumbnail(f, size=(80,80))
            else:
                thumb_img = get_image_thumbnail(f, size=(80,80))
            if thumb_img is not None:
                rgb_img = thumb_img.convert("RGB")
                w, h = rgb_img.size
                data = rgb_img.tobytes()
                qimg = QImage(data, w, h, w*3, QImage.Format_RGB888)
                pixmap = QPixmap.fromImage(qimg)
                icon = QIcon(pixmap.scaled(80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                thumb = QLabel()
                thumb.setPixmap(icon.pixmap(80,80))
                vbox.addWidget(thumb)
            else:
                vbox.addWidget(QLabel(os.path.basename(f)))
            cb = QCheckBox(os.path.basename(f))
            cb.setToolTip(f)
            cb.setChecked(True)
            vbox.addWidget(cb)
            file_checkboxes = [(cb, f)]
            hbox = QHBoxLayout()
            hbox.addLayout(vbox)
            group_checkboxes.append(file_checkboxes)
            layout.addLayout(hbox)
        move_btn = QPushButton("選択したファイルをフォルダに移動")
        move_btn.setStyleSheet("font-size:13px;color:#00ff99;border:2px solid #00ff99;border-radius:8px;")
        move_btn.clicked.connect(lambda _, cbs=group_checkboxes: self.move_selected_files_to_folder(cbs))
        layout.addWidget(move_btn)
        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(dialog.reject)
        layout.addWidget(btns)
        dialog.setLayout(layout)
        dialog.exec_()

    def face_grouping_and_move(self):
        self.last_mode = 'face'
        from PyQt5.QtWidgets import QFileDialog, QCheckBox
        if not self.folder:
            QMessageBox.warning(self, "エラー", "先にフォルダを選択してください")
            return
        image_exts = ('.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff')
        video_exts = ('.mp4', '.avi', '.mov', '.mkv', '.wmv')
        image_files = collect_files(self.folder, image_exts)
        video_files = collect_files(self.folder, video_exts)
        all_files = image_files + video_files
        if not all_files:
            QMessageBox.information(self, "顔グループ化", "画像・動画ファイルがありません")
            return
        # 顔特徴量抽出
        encodings = []
        paths = []
        dlg = QDialog(self)
        dlg.setWindowTitle("顔特徴量抽出中...")
        vbox = QVBoxLayout()
        label = QLabel("顔特徴量を抽出しています。しばらくお待ちください...")
        vbox.addWidget(label)
        prog = QProgressBar()
        prog.setMaximum(len(all_files))
        vbox.addWidget(prog)
        dlg.setLayout(vbox)
        dlg.show()
        QApplication.processEvents()
        error_files = []
        load_thumb_cache()
        thumb_q = queue.Queue()
        def update_thumb(_):
            self.update()  # repaint→update
        # スレッド数: 論理CPU数 or 4
        num_threads = min(4, os.cpu_count() or 1)
        thumb_workers = [ThumbnailWorker(thumb_q, update_thumb) for _ in range(num_threads)]
        for w in thumb_workers:
            w.start()
        for idx, f in enumerate(all_files):
            if f.lower().endswith(video_exts):
                thumb_q.put((f, (80,80), True, error_files))
                enc = get_video_face_encoding(f)
            else:
                thumb_q.put((f, (80,80), False, error_files))
                enc = get_face_encoding(f)
            encodings.append(enc)
            paths.append(f)
            prog.setValue(idx+1)
            QApplication.processEvents()
        thumb_q.join()
        for _ in thumb_workers:
            thumb_q.put(None)
        save_thumb_cache()
        dlg.close()
        if error_files:
            QMessageBox.warning(self, "動画コーデック未対応", "一部動画ファイルはコーデック未対応のため顔グループ化できません:\n" + "\n".join(error_files))
        groups = group_by_face(encodings, paths)
        if not groups:
            QMessageBox.information(self, "顔グループ化", "顔でグループ化できるファイルがありませんでした")
            return
        # エラーが出たファイルは一番下にリストアップ
        if error_files:
            groups.append([f for f in error_files])
        # グループごとにUI表示（個別選択）
        dlg2 = QDialog(self)
        dlg2.setWindowTitle("顔グループごとに個別振り分け")
        vbox2 = QVBoxLayout()
        group_checkboxes = []
        for group in groups:
            group_label = QLabel("--- 顔グループ ---")
            group_label.setStyleSheet("font-size:15px;color:#ff00c8;font-weight:bold;padding:8px 0 8px 0;text-shadow:0 0 8px #ff00c8;")
            vbox2.addWidget(group_label)
            hbox = QHBoxLayout()
            file_checkboxes = []
            for f in group:
                vbox = QVBoxLayout()
                thumb = get_video_thumbnail(f, size=(80,80)) if f.lower().endswith(video_exts) else get_image_thumbnail(f, size=(80,80))
                if thumb is not None:
                    rgb_img = thumb.convert("RGB")
                    w, h = rgb_img.size
                    data = rgb_img.tobytes()
                    qimg = QImage(data, w, h, w*3, QImage.Format_RGB888)
                    pixmap = QPixmap.fromImage(qimg)
                    icon = QIcon(pixmap.scaled(80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                    thumb_label = QLabel()
                    thumb_label.setPixmap(icon.pixmap(80,80))
                    vbox.addWidget(thumb_label)
                else:
                    vbox.addWidget(QLabel(os.path.basename(f)))
                cb = QCheckBox(os.path.basename(f))
                cb.setToolTip(f)
                cb.setChecked(True)
                vbox.addWidget(cb)
                file_checkboxes.append((cb, f))
                hbox.addLayout(vbox)
            group_checkboxes.append(file_checkboxes)
            vbox2.addLayout(hbox)
            # 移動先選択ボタン
            move_btn = QPushButton("選択したファイルをフォルダに移動")
            move_btn.setStyleSheet("font-size:13px;color:#00ff99;border:2px solid #00ff99;border-radius:8px;")
            move_btn.clicked.connect(lambda _, cbs=file_checkboxes: self.move_selected_files_to_folder(cbs))
            vbox2.addWidget(move_btn)
        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(dlg2.reject)
        vbox2.addWidget(btns)
        dlg2.setLayout(vbox2)
        dlg2.exec_()

    def move_selected_files_to_folder(self, checkboxes):
        from PyQt5.QtWidgets import QFileDialog
        target_dir = QFileDialog.getExistingDirectory(self, "移動先フォルダを選択（新規作成可）")
        if not target_dir:
            return
        failed = []
        for cb, path in checkboxes:
            if not cb.isChecked():
                continue
            if not path or not os.path.isfile(path):
                failed.append(path)
                continue
            try:
                fname = os.path.basename(path)
                dest = os.path.join(target_dir, fname)
                base, ext = os.path.splitext(fname)
                count = 1
                while os.path.exists(dest):
                    dest = os.path.join(target_dir, f"{base}_copy{count}{ext}")
                    count += 1
                shutil_move(path, dest)
            except Exception as e:
                failed.append(f"{path} : {e}")
        if failed:
            QMessageBox.warning(self, "移動エラー", "\n".join(map(str, failed)))
        else:
            QMessageBox.information(self, "移動", "選択したファイルを指定フォルダへ移動しました")
        self.find_duplicates()

    def reload_folder(self):
        # 現在のモードに応じて再読み込み
        # 直近のモードを記憶するための属性を追加しておく
        if hasattr(self, 'last_mode') and self.last_mode == 'face':
            self.face_grouping_and_move()
        else:
            self.find_duplicates()

    def show_mp4_tool_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("MP4修復/変換ツール")
        vbox = QVBoxLayout()
        label = QLabel("MP4ファイルの修復または変換を選択してください。\n(処理後は保存先を指定できます)")
        label.setStyleSheet("font-size:15px;color:#ffb300;padding:8px;")
        vbox.addWidget(label)
        btn_repair = QPushButton("MP4修復")
        btn_repair.setStyleSheet("font-size:16px;color:#00ffe7;border:2px solid #00ffe7;border-radius:8px;padding:8px;")
        btn_convert = QPushButton("MP4変換")
        btn_convert.setStyleSheet("font-size:16px;color:#00ff99;border:2px solid #00ff99;border-radius:8px;padding:8px;")
        btn_digital = QPushButton("デジタル修復（高度）")
        btn_digital.setStyleSheet("font-size:16px;color:#ff44ff;border:2px solid #ff44ff;border-radius:8px;padding:8px;")
        vbox.addWidget(btn_repair)
        vbox.addWidget(btn_convert)
        vbox.addWidget(btn_digital)
        dlg.setLayout(vbox)
        def do_repair():
            dlg.accept()
            self.run_mp4_repair()
        def do_convert():
            dlg.accept()
            self.run_mp4_convert()
        def do_digital():
            dlg.accept()
            self.run_mp4_digital_repair()
        btn_repair.clicked.connect(do_repair)
        btn_convert.clicked.connect(do_convert)
        btn_digital.clicked.connect(do_digital)
        dlg.exec_()

    def run_mp4_repair(self, file_path=None):
        # MP4修復処理
        if not file_path:
            file_path, _ = QFileDialog.getOpenFileName(self, "修復したい動画ファイルを選択", "", "動画ファイル (*.mp4 *.avi *.mov *.mkv *.wmv *.flv *.webm *.mpg *.mpeg *.3gp)")
            if not file_path:
                return
        save_path, _ = QFileDialog.getSaveFileName(self, "修復後の保存先を指定", os.path.splitext(file_path)[0] + "_repaired" + os.path.splitext(file_path)[1], "動画ファイル (*.*)")
        if not save_path:
            return
        try:
            # ストリームコピー（無劣化）
            cmd = ["ffmpeg", "-y", "-i", file_path, "-c", "copy", save_path]
            dlg = QProgressDialog("動画修復中...", None, 0, 0, self)
            dlg.setWindowTitle("動画修復")
            dlg.setWindowModality(Qt.WindowModal)
            dlg.show()
            QApplication.processEvents()
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            dlg.close()
            if result.returncode == 0:
                QMessageBox.information(self, "完了", f"修復が完了しました:\n{save_path}")
            else:
                QMessageBox.warning(self, "エラー", f"修復に失敗しました:\n{result.stderr}")
        except Exception as e:
            QMessageBox.warning(self, "エラー", f"修復処理中にエラー:\n{e}")

    def run_mp4_convert(self, file_path=None):
        # 動画変換処理（無劣化: ストリームコピー、ただし拡張子変換やコンテナ変更のみ）
        if not file_path:
            file_path, _ = QFileDialog.getOpenFileName(self, "変換したい動画ファイルを選択", "", "動画ファイル (*.mp4 *.avi *.mov *.mkv *.wmv *.flv *.webm *.mpg *.mpeg *.3gp)")
            if not file_path:
                return
        # 拡張子選択肢をユーザーに提示
        ext_map = {'.mp4': 'MP4ファイル (*.mp4)', '.mkv': 'MKVファイル (*.mkv)', '.mov': 'MOVファイル (*.mov)', '.avi': 'AVIファイル (*.avi)', '.wmv': 'WMVファイル (*.wmv)', '.flv': 'FLVファイル (*.flv)', '.webm': 'WEBMファイル (*.webm)', '.mpg': 'MPGファイル (*.mpg)', '.mpeg': 'MPEGファイル (*.mpeg)', '.3gp': '3GPファイル (*.3gp)'}
        orig_ext = os.path.splitext(file_path)[1].lower()
        filter_str = ext_map.get(orig_ext, 'MP4ファイル (*.mp4);;MKVファイル (*.mkv);;MOVファイル (*.mov);;AVIファイル (*.avi);;WMVファイル (*.wmv);;FLVファイル (*.flv);;WEBMファイル (*.webm);;MPGファイル (*.mpg);;MPEGファイル (*.mpeg);;3GPファイル (*.3gp)')
        save_path, _ = QFileDialog.getSaveFileName(self, "変換後の保存先を指定", os.path.splitext(file_path)[0] + "_converted" + orig_ext, filter_str)
        if not save_path:
            return
        try:
            # ストリームコピー（無劣化）
            cmd = ["ffmpeg", "-y", "-i", file_path, "-c", "copy", save_path]
            dlg = QProgressDialog("動画変換中...", None, 0, 0, self)
            dlg.setWindowTitle("動画変換")
            dlg.setWindowModality(Qt.WindowModal)
            dlg.show()
            QApplication.processEvents()
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            dlg.close()
            if result.returncode == 0:
                QMessageBox.information(self, "完了", f"変換が完了しました:\n{save_path}")
            else:
                QMessageBox.warning(self, "エラー", f"変換に失敗しました:\n{result.stderr}")
        except Exception as e:
            QMessageBox.warning(self, "エラー", f"変換処理中にエラー:\n{e}")

    def run_mp4_digital_repair(self, file_path=None):
        # Real-ESRGAN+GFPGANによる高画質化＋顔復元
        import shutil, glob, json
        try:
            from gfpgan import GFPGANer
            import face_recognition
        except ImportError:
            QMessageBox.warning(self, "エラー", "GFPGAN, face_recognitionがインストールされていません")
            return
        if not file_path:
            file_path, _ = QFileDialog.getOpenFileName(self, "AI超解像＋顔復元したい動画ファイルを選択", "", "動画ファイル (*.mp4 *.avi *.mov *.mkv *.wmv *.flv *.webm *.mpg *.mpeg *.3gp)")
            if not file_path:
                return
        save_path, _ = QFileDialog.getSaveFileName(self, "高画質化後の保存先を指定", os.path.splitext(file_path)[0] + "_aiup_gfpgan" + os.path.splitext(file_path)[1], "動画ファイル (*.*)")
        if not save_path:
            return
        # --- パス・モデル ---
        frames_dir = "frames_raw"
        esrgan_out_dir = "frames_esrgan"
        gfpgan_out_dir = "frames_final_output"
        audio_only_path = "input_audio_temp.aac"
        realesrgan_exe = os.path.abspath("realesrgan-ncnn-vulkan.exe")
        gfpgan_model_path = os.path.abspath("GFPGANv1.4.pth")
        temp_dirs = [frames_dir, esrgan_out_dir, gfpgan_out_dir]
        try:
            for d in temp_dirs:
                os.makedirs(d, exist_ok=True)
            # フレームレート取得
            def get_video_framerate(video_path):
                cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate", "-of", "json", video_path]
                result = subprocess.run(cmd, capture_output=True, text=True)
                info = json.loads(result.stdout)
                rate_str = info['streams'][0]['avg_frame_rate']
                num, den = map(int, rate_str.split('/'))
                return float(num) / den if den != 0 else 0
            video_framerate = get_video_framerate(file_path)
            # 音声抽出
            subprocess.run(["ffmpeg", "-y", "-i", file_path, "-vn", "-c:a", "copy", audio_only_path], check=True)
            # フレーム分解
            subprocess.run(["ffmpeg", "-y", "-i", file_path, "-qscale:v", "2", os.path.join(frames_dir, "frame_%06d.png")], check=True)
            # Real-ESRGAN
            subprocess.run([realesrgan_exe, "-i", frames_dir, "-o", esrgan_out_dir], check=True)
            # GFPGAN
            gfpgan_enhancer = GFPGANer(model_path=gfpgan_model_path, upscale=1, arch='clean', channel_multiplier=2, bg_upsampler=None)
            esrgan_frames = sorted([f for f in os.listdir(esrgan_out_dir) if f.endswith('.png')])
            for i, fname in enumerate(esrgan_frames):
                img_path = os.path.join(esrgan_out_dir, fname)
                img = cv2.imread(img_path)
                if img is None:
                    continue
                rgb_frame = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                face_locations = face_recognition.face_locations(rgb_frame, model="hog")
                final_frame = img.copy()
                if face_locations:
                    for (top, right, bottom, left) in face_locations:
                        padding = 50
                        p_top = max(0, top - padding)
                        p_bottom = min(img.shape[0], bottom + padding)
                        p_left = max(0, left - padding)
                        p_right = min(img.shape[1], right + padding)
                        cropped_face = img[p_top:p_bottom, p_left:p_right]
                        _, restored_face_image = gfpgan_enhancer.enhance(
                            cropped_face, has_aligned=False, only_center_face=False, paste_back=True)
                        final_frame[p_top:p_bottom, p_left:p_right] = restored_face_image
                cv2.imwrite(os.path.join(gfpgan_out_dir, fname), final_frame)
            # フレーム→動画再結合
            subprocess.run([
                "ffmpeg", "-y", "-framerate", str(video_framerate),
                "-i", os.path.join(gfpgan_out_dir, "frame_%06d.png"),
                "-i", audio_only_path,
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                "-map", "0:v:0", "-map", "1:a:0?", "-shortest", save_path
            ], check=True)
            QMessageBox.information(self, "完了", f"修復が完了しました:\n{save_path}")
        except Exception as e:
            QMessageBox.warning(self, "エラー", f"修復処理中にエラー:\n{e}")
        finally:
            import shutil
            for d in temp_dirs:
                if os.path.exists(d):
                    shutil.rmtree(d, ignore_errors=True)
            if os.path.exists(audio_only_path):
                try:
                    os.remove(audio_only_path)
                except Exception:
                    pass

    def show_broken_video_dialog(self):
        # フォルダ選択
        folder = QFileDialog.getExistingDirectory(self, "壊れ検出したいフォルダを選択")
        if not folder:
            return
        # 対象動画拡張子
        video_exts = ('.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm', '.mpg', '.mpeg', '.3gp')
        video_files = collect_files(folder, video_exts)
        if not video_files:
            QMessageBox.information(self, "動画壊れ検出", "動画ファイルが見つかりませんでした")
            return
        # 進捗ダイアログ
        prog = QProgressDialog("動画解析中...", None, 0, len(video_files), self)
        prog.setWindowTitle("動画壊れ検出")
        prog.setWindowModality(Qt.WindowModal)
        prog.show()
        QApplication.processEvents()
        # ffprobeでmoov atomの有無・位置を判定
        import subprocess
        broken_list = []
        for idx, f in enumerate(video_files):
            status = self.check_moov_atom(f)
            broken_list.append((f, status))
            prog.setValue(idx+1)
            QApplication.processEvents()
        prog.close()
        # グループ化: 状態ごとにまとめる
        groups = {}
        for f, status in broken_list:
            groups.setdefault(status, []).append(f)
        # UI表示（重複グループと同じレイアウト）
        self.show_broken_video_groups(groups)

    def check_moov_atom(self, filepath):
        # ffprobeでmoov atomの有無・位置を判定
        import subprocess
        try:
            cmd = ["ffprobe", "-v", "error", "-show_entries", "format_tags=major_brand", "-show_format", "-show_streams", "-print_format", "json", filepath]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, encoding="utf-8", errors="replace")
            if result.returncode != 0:
                return "ファイルが壊れている/解析不可"
            # moov atomの位置判定
            # moov atomが先頭ならfaststart、末尾ならnot faststart、なければ壊れ
            # ここではffprobeの出力からmoovの位置は直接取れないため、ffmpeg -i でエラー文言を利用
            cmd2 = ["ffmpeg", "-v", "error", "-i", filepath, "-f", "null", "-"]
            result2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=10, encoding="utf-8", errors="replace")
            err = result2.stderr.lower()
            if "moov atom not found" in err:
                return "moov atomがない/壊れ"
            if "moov atom not found" not in err and "error" in err:
                return "ファイルが壊れている/解析不可"
            if "moov atom is not found at the beginning" in err or "moov atom not found at the beginning" in err:
                return "moov atomが後ろにある"
            return "正常/faststart"
        except Exception as e:
            return f"解析エラー: {e}"

    def show_broken_video_groups(self, groups):
        # 既存の内容をクリア
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self.thumb_buttons = []
        self.selected_paths = set()
        load_thumb_cache()
        thumb_q = queue.Queue()
        def update_thumb(_):
            self.update()  # repaint→update
        thumb_worker = ThumbnailWorker(thumb_q, update_thumb)
        thumb_worker.start()
        error_files = []
        for status, files in groups.items():
            group_label = QLabel(f"--- {status} ---")
            group_label.setStyleSheet("font-size:16px;color:#ff4444;font-weight:bold;padding:8px 0 8px 0;text-shadow:0 0 8px #ff4444;")
            self.content_layout.addWidget(group_label)
            grid = QGridLayout()
            grid.setHorizontalSpacing(12)
            grid.setVerticalSpacing(16)
            max_col = 4
            btns = []
            for idx, f in enumerate(files):
                vbox = QVBoxLayout()
                thumb_q.put((f, (140,140), True, error_files))
                thumb_img = get_video_thumbnail(f, size=(140,140), error_files=error_files)
                if thumb_img is not None:
                    rgb_img = thumb_img.convert("RGB")
                    w, h = rgb_img.size
                    data = rgb_img.tobytes()
                    qimg = QImage(data, w, h, w*3, QImage.Format_RGB888)
                    pixmap = QPixmap.fromImage(qimg)
                    icon = QIcon(pixmap.scaled(140, 140, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                    btn = QPushButton()
                    btn.setIcon(icon)
                    btn.setIconSize(QSize(140,140))
                    btn.setProperty("filepath", f)
                    btn.clicked.connect(self.on_thumbnail_clicked)
                    btn.setStyleSheet("background:transparent;border:2px solid #ff4444;border-radius:10px;box-shadow:0 0 8px #ff4444;")
                    vbox.addWidget(btn)
                    btns.append(btn)
                else:
                    btn = QPushButton(f)
                    btn.setProperty("filepath", f)
                    btn.clicked.connect(self.on_thumbnail_clicked)
                    btn.setStyleSheet("background:transparent;border:2px solid #ff4444;border-radius:10px;box-shadow:0 0 8px #ff4444;")
                    vbox.addWidget(btn)
                    btns.append(btn)
                # ファイル名（長い場合は省略）
                fname = os.path.basename(f)
                maxlen = 18
                if len(fname) > maxlen:
                    fname_disp = fname[:8] + '...' + fname[-7:]
                else:
                    fname_disp = fname
                label_name = QLabel(fname_disp)
                label_name.setAlignment(Qt.AlignCenter)
                label_name.setStyleSheet("font-size:12px;color:#ff4444;font-weight:bold;text-shadow:0 0 6px #ff4444;")
                vbox.addWidget(label_name)
                # ファイルサイズ
                try:
                    size = os.path.getsize(f)
                    size_mb = size / 1024 / 1024
                    size_str = f"{size_mb:.2f} MB"
                except Exception:
                    size_str = "-"
                label_size = QLabel(size_str)
                label_size.setAlignment(Qt.AlignCenter)
                label_size.setStyleSheet("font-size:11px;color:#00ff99;")
                vbox.addWidget(label_size)
                # ファイルパス（折り返し・クリックでフォルダを開く）
                label_path = QLabel(f)
                label_path.setAlignment(Qt.AlignCenter)
                label_path.setWordWrap(True)
                label_path.setStyleSheet("font-size:10px;color:#00ff99;text-shadow:0 0 6px #00ff99;max-width:140px;")
                label_path.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextBrowserInteraction)
                label_path.mousePressEvent = lambda e, path=f: self.open_folder_of_file(path)
                vbox.addWidget(label_path)
                # 修復/変換/デジタル修復ボタン
                hbtn = QHBoxLayout()
                repair_btn = QPushButton("修復(再mux)")
                repair_btn.setStyleSheet("font-size:11px;color:#00ffe7;border:2px solid #00ffe7;border-radius:8px;")
                repair_btn.clicked.connect(lambda _, path=f: self.run_mp4_repair(path))
                convert_btn = QPushButton("変換(H.264/AAC)")
                convert_btn.setStyleSheet("font-size:11px;color:#00ff99;border:2px solid #00ff99;border-radius:8px;")
                convert_btn.clicked.connect(lambda _, path=f: self.run_mp4_convert(path))
                digital_btn = QPushButton("デジタル修復")
                digital_btn.setStyleSheet("font-size:11px;color:#ff44ff;border:2px solid #ff44ff;border-radius:8px;")
                digital_btn.clicked.connect(lambda _, path=f: self.run_mp4_digital_repair(path))
                hbtn.addWidget(repair_btn)
                hbtn.addWidget(convert_btn)
                hbtn.addWidget(digital_btn)
                vbox.addLayout(hbtn)
                vbox.addStretch()
                wgt = QWidget()
                wgt.setLayout(vbox)
                row = idx // max_col
                col = idx % max_col
                grid.addWidget(wgt, row, col)
            self.thumb_buttons.append(btns)
            group_widget = QWidget()
            group_widget.setLayout(grid)
            self.content_layout.addWidget(group_widget)
        thumb_q.join()
        thumb_worker.q.put(None)
        save_thumb_cache()
        # エラーが出たファイルは一番下にリストアップ
        if error_files:
            group_label = QLabel("--- サムネイル生成エラー ---")
            self.content_layout.addWidget(group_label)
            for f in error_files:
                self.content_layout.addWidget(QLabel(f))
        self.content_layout.addStretch()
        self.delete_btn.setEnabled(False)
        self.progress_time_label.setText("")

    # ドラッグ＆ドロップ用ラベル
    from PyQt5.QtWidgets import QLabel
    class DropLabel(QLabel):
        def __init__(self, target_path, parent=None):
            super().__init__("ここに他のファイルをドロップで移動", parent)
            self.setAcceptDrops(True)
            self.target_path = target_path
            self.setStyleSheet("background:#232526;color:#00ff99;border:1px dashed #00ff99;border-radius:6px;font-size:10px;padding:4px;")
        def dragEnterEvent(self, event):
            if event.mimeData().hasUrls():
                event.acceptProposedAction()
        def dropEvent(self, event):
            for url in event.mimeData().urls():
                src = url.toLocalFile()
                if os.path.isfile(src):
                    try:
                        fname = os.path.basename(src)
                        dest_folder = os.path.dirname(self.target_path)
                        dest = os.path.join(dest_folder, fname)
                        base, ext = os.path.splitext(fname)
                        count = 1
                        while os.path.exists(dest):
                            dest = os.path.join(dest_folder, f"{base}_copy{count}{ext}")
                            count += 1
                        shutil_move(src, dest)
                        self.setText("移動完了: " + os.path.basename(dest))
                    except Exception as e:
                        self.setText(f"移動失敗: {e}")

    def show_digital_repair_dialog(self):
        # 独立したデジタル修復ダイアログ
        dlg = QDialog(self)
        dlg.setWindowTitle("デジタル修復・高画質化ツール")
        vbox = QVBoxLayout()
        label = QLabel("デジタル修復・高画質化したい動画ファイルを選択し、保存先を指定してください。\n(エラー隠蔽＋フレーム補間/ノイズ除去/シャープ化/色補正/アップスケーリング/AI超解像)")
        label.setStyleSheet("font-size:15px;color:#ff44ff;padding:8px;")
        vbox.addWidget(label)
        select_btn = QPushButton("ファイル選択")
        select_btn.setStyleSheet("font-size:15px;color:#00ffe7;border:2px solid #00ffe7;border-radius:8px;padding:8px;")
        vbox.addWidget(select_btn)
        self.selected_digital_file = None
        self.selected_digital_save = None
        file_label = QLabel("")
        vbox.addWidget(file_label)
        save_label = QLabel("")
        vbox.addWidget(save_label)
        # プリセット選択
        preset_label = QLabel("画質向上プリセット:")
        vbox.addWidget(preset_label)
        from PyQt5.QtWidgets import QComboBox
        preset_box = QComboBox()
        preset_box.addItem("標準デジタル修復 (フレーム補間)")
        preset_box.addItem("高画質化 (ノイズ除去+シャープ+色補正)")
        preset_box.addItem("高画質化+アップスケール2倍")
        preset_box.addItem("AI超解像(Real-ESRGAN)")
        vbox.addWidget(preset_box)
        def select_file():
            file_path, _ = QFileDialog.getOpenFileName(self, "デジタル修復・高画質化したい動画ファイルを選択", "", "動画ファイル (*.mp4 *.avi *.mov *.mkv *.wmv *.flv *.webm *.mpg *.mpeg *.3gp)")
            if file_path:
                self.selected_digital_file = file_path
                file_label.setText(f"選択ファイル: {os.path.basename(file_path)}")
                # 保存先も選択
                save_path, _ = QFileDialog.getSaveFileName(self, "修復後の保存先を指定", os.path.splitext(file_path)[0] + "_digital_fixed" + os.path.splitext(file_path)[1], "動画ファイル (*.*)")
                if save_path:
                    self.selected_digital_save = save_path
                    save_label.setText(f"保存先: {save_path}")
        select_btn.clicked.connect(select_file)
        run_btn = QPushButton("実行")
        run_btn.setStyleSheet("font-size:15px;color:#ff44ff;border:2px solid #ff44ff;border-radius:8px;padding:8px;")
        vbox.addWidget(run_btn)
        def run_repair():
            if not self.selected_digital_file or not self.selected_digital_save:
                QMessageBox.warning(self, "エラー", "ファイルと保存先を選択してください")
                return
            preset = preset_box.currentIndex()
            if preset == 0:
                # 標準デジタル修復（フレーム補間）
                vf = "minterpolate=fps=30:mi_mode=mci:mc_mode=aobmc:vsbmc=1"
                cmd = [
                    "ffmpeg", "-y", "-err_detect", "ignore_err", "-i", self.selected_digital_file,
                    "-vf", vf,
                    "-c:v", "libx264", "-c:a", "aac", self.selected_digital_save
                ]
                self._run_ffmpeg_cmd(cmd, "デジタル修復・高画質化中...", "デジタル修復・高画質化")
                QMessageBox.information(self, "完了", f"デジタル修復が完了しました:\n{self.selected_digital_save}")
                # 比較再生ボタン
                compare_btn = QMessageBox.question(self, "比較再生", "修復前後の動画を比較再生しますか？", QMessageBox.Yes | QMessageBox.No)
                if compare_btn == QMessageBox.Yes:
                    self.show_compare_dialog(self.selected_digital_file, self.selected_digital_save)
            elif preset == 1:
                # 高画質化（ノイズ除去＋シャープ＋色補正）
                vf = "hqdn3d,unsharp=5:5:1.0:5:5:0.0,eq=contrast=1.2:brightness=0.02:saturation=1.3"
                cmd = [
                    "ffmpeg", "-y", "-err_detect", "ignore_err", "-i", self.selected_digital_file,
                    "-vf", vf,
                    "-c:v", "libx264", "-c:a", "aac", self.selected_digital_save
                ]
                self._run_ffmpeg_cmd(cmd, "デジタル修復・高画質化中...", "デジタル修復・高画質化")

                QMessageBox.information(self, "完了", f"デジタル修復が完了しました:\n{self.selected_digital_save}")
                compare_btn = QMessageBox.question(self, "比較再生", "修復前後の動画を比較再生しますか？", QMessageBox.Yes | QMessageBox.No)
                if compare_btn == QMessageBox.Yes:
                    self.show_compare_dialog(self.selected_digital_file, self.selected_digital_save)
            elif preset == 2:
                # 高画質化＋アップスケール2倍
                vf = "hqdn3d,unsharp=5:5:1.0:5:5:0.0,eq=contrast=1.2:brightness=0.02:saturation=1.3,scale=iw*2:ih*2:flags=lanczos"
                cmd = [
                    "ffmpeg", "-y", "-err_detect", "ignore_err", "-i", self.selected_digital_file,
                    "-vf", vf,
                    "-c:v", "libx264", "-c:a", "aac", self.selected_digital_save
                ]
                self._run_ffmpeg_cmd(cmd, "デジタル修復・高画質化中...", "デジタル修復・高画質化")
                QMessageBox.information(self, "完了", f"デジタル修復が完了しました:\n{self.selected_digital_save}")
                compare_btn = QMessageBox.question(self, "比較再生", "修復前後の動画を比較再生しますか？", QMessageBox.Yes | QMessageBox.No)
                if compare_btn == QMessageBox.Yes:
                    self.show_compare_dialog(self.selected_digital_file, self.selected_digital_save)
            else:
                # AI超解像(Real-ESRGAN)
                # 1. 一時ディレクトリ作成
                temp_dir = tempfile.mkdtemp()
                frames_dir = os.path.join(temp_dir, "frames")
                out_dir = os.path.join(temp_dir, "out")
                os.makedirs(frames_dir, exist_ok=True)
                os.makedirs(out_dir, exist_ok=True)
                # 2. ffmpegでフレーム抽出
                extract_cmd = [
                    "ffmpeg", "-y", "-i", self.selected_digital_file, "-qscale:v", "2", os.path.join(frames_dir, "frame_%06d.png")
                ]
                self._run_ffmpeg_cmd(extract_cmd, "フレーム抽出中...", "AI超解像処理")
                # 3. Real-ESRGANで画像高解像度化
                #   (realesrgan-ncnn-vulkan.exeが同じフォルダにある前提)
                exe_path = os.path.join(os.getcwd(), "realesrgan-ncnn-vulkan.exe")
                if not os.path.exists(exe_path):
                    QMessageBox.warning(self, "エラー", "realesrgan-ncnn-vulkan.exe が見つかりません。\nhttps://github.com/xinntao/Real-ESRGAN/releases からダウンロードし、同じフォルダに置いてください。")
                    shutil.rmtree(temp_dir)
                    return
                # 画像一括変換
                # Windows: realesrgan-ncnn-vulkan.exe -i input_dir -o output_dir
                # モデル自動判定: models/Real-ESRGAN-General-x4v3.param があれば -n Real-ESRGAN-General-x4 を指定
                model_name = None
                model_path = os.path.join(os.getcwd(), "models", "Real-ESRGAN-General-x4v3.param")
                if os.path.exists(model_path):
                    model_name = "Real-ESRGAN-General-x4v3"
                # animeモデルがあればそちらも対応
                anime_model_path = os.path.join(os.getcwd(), "models", "realesr-animevideov3-x4.param")
                if os.path.exists(anime_model_path):
                    model_name = "realesr-animevideov3-x4"
                upscale_cmd = [exe_path, "-i", frames_dir, "-o", out_dir]
                if model_name:
                    upscale_cmd += ["-n", model_name]
                # --- ここからデバッグ用コード追加 ---
                import glob
                import logging
                logging.basicConfig(filename="realesrgan_debug.log", level=logging.INFO, format="%(asctime)s %(message)s")
                # アップスケール前のPNGサイズ取得
                before_files = glob.glob(os.path.join(frames_dir, "*.png"))
                before_sizes = [os.path.getsize(f) for f in before_files]
                before_mean = np.mean(before_sizes) if before_sizes else 0
                # コマンド内容をprint/log
                print("[Real-ESRGANコマンド]", " ".join(upscale_cmd))
                logging.info(f"[Real-ESRGANコマンド] {' '.join(upscale_cmd)}")
                dlg_prog = QProgressDialog("AI超解像(Real-ESRGAN)実行中...", None, 0, 0, self)
                dlg_prog.setWindowTitle("AI超解像(Real-ESRGAN)")
                dlg_prog.setWindowModality(Qt.WindowModal)
                dlg_prog.show()
                QApplication.processEvents()
                result = subprocess.run(upscale_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
                dlg_prog.close()
                # 標準出力・標準エラーをprint/log
                print("[Real-ESRGAN stdout]", result.stdout)
                print("[Real-ESRGAN stderr]", result.stderr)
                logging.info(f"[Real-ESRGAN stdout] {result.stdout}")
                logging.info(f"[Real-ESRGAN stderr] {result.stderr}")
                if result.returncode != 0:
                    QMessageBox.warning(self, "エラー", f"Real-ESRGAN実行に失敗:\n{result.stderr}")
                    shutil.rmtree(temp_dir)
                    return
                # アップスケール後のPNGサイズ取得
                after_files = glob.glob(os.path.join(out_dir, "*.png"))
                after_sizes = [os.path.getsize(f) for f in after_files]
                after_mean = np.mean(after_sizes) if after_sizes else 0
                # サイズ変化がなければ警告
                if before_mean > 0 and after_mean > 0:
                    ratio = after_mean / before_mean

                    print(f"[Real-ESRGAN] 平均PNGサイズ: 前={before_mean:.1f} bytes, 後={after_mean:.1f} bytes, ratio={ratio:.2f}")
                    logging.info(f"[Real-ESRGAN] 平均PNGサイズ: 前={before_mean:.1f} bytes, 後={after_mean:.1f} bytes, ratio={ratio:.2f}")
                    if ratio < 1.2:
                        QMessageBox.warning(self, "AI超解像 警告", f"AI超解像の効果が小さい可能性があります。\n平均PNGサイズ比: {ratio:.2f}\nモデル・コマンド・ログを確認してください。\nrealesrgan_debug.log に詳細を記録しました。")
                # 4. ffmpegで動画再結合
                import cv2
                cap = cv2.VideoCapture(self.selected_digital_file)
                fps = cap.get(cv2.CAP_PROP_FPS)
                cap.release()
                if not fps or fps < 1:
                    fps = 30  # デフォルト
                out_pattern = os.path.join(out_dir, "frame_%06d.png")
                merge_cmd = [
                    "ffmpeg", "-y",
                    "-framerate", str(int(fps)),
                    "-i", out_pattern,
                    "-i", self.selected_digital_file,
                    "-c:v", "libx264",
                    "-pix_fmt", "yuv420p",
                    "-profile:v", "high",
                    "-level", "4.0",
                    "-c:a", "aac",
                    "-map", "0:v:0",
                    "-map", "1:a:0?",
                    "-shortest",
                    "-movflags", "+faststart",
                    self.selected_digital_save
                ]
                self._run_ffmpeg_cmd(merge_cmd, "動画再結合中...", "AI超解像処理")
                # 5. 一時ディレクトリ削除
                shutil.rmtree(temp_dir)
                QMessageBox.information(self, "完了", f"AI超解像が完了しました:\n{self.selected_digital_save}")
                compare_btn = QMessageBox.question(self, "比較再生", "修復前後の動画を比較再生しますか？", QMessageBox.Yes | QMessageBox.No)

                if compare_btn == QMessageBox.Yes:
                    self.show_compare_dialog(self.selected_digital_file, self.selected_digital_save)
        run_btn.clicked.connect(run_repair)
        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(dlg.reject)

        vbox.addWidget(btns)
        dlg.setLayout(vbox)
        dlg.exec_()

    def _run_ffmpeg_cmd(self, cmd, msg, title):
        dlg_prog = QProgressDialog(msg, None, 0, 0, self)
        dlg_prog.setWindowTitle(title)
        dlg_prog.setWindowModality(Qt.WindowModal)
        dlg_prog.show()
        QApplication.processEvents()
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        dlg_prog.close()
        return result

    def show_compare_dialog(self, original_path, repaired_path):
        # 元動画と修復後動画を2画面で同時再生する比較ダイアログ
        from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent
        from PyQt5.QtMultimediaWidgets import QVideoWidget
        from PyQt5.QtCore import QUrl
        dlg = QDialog(self)
        dlg.setWindowTitle("修復前後の比較再生")

        layout = QHBoxLayout()
        # 左: 元動画
        left_widget = QVideoWidget()
        left_player = QMediaPlayer(None, QMediaPlayer.VideoSurface)
        left_player.setVideoOutput(left_widget)
        left_player.setMedia(QMediaContent(QUrl.fromLocalFile(original_path)))
        # 右: 修復後動画
        right_widget = QVideoWidget()
        right_player = QMediaPlayer(None, QMediaPlayer.VideoSurface)
        right_player.setVideoOutput(right_widget)
        right_player.setMedia(QMediaContent(QUrl.fromLocalFile(repaired_path)))
        # 再生ボタン
        play_btn = QPushButton("▶ 同時再生")
        def play_both():
            left_player.setPosition(0)
            right_player.setPosition(0)
            left_player.play()
            right_player.play()
        play_btn.clicked.connect(play_both)
        # レイアウト
        vbox_left = QVBoxLayout()
        vbox_left.addWidget(QLabel("元動画"))
        vbox_left.addWidget(left_widget)
        vbox_right = QVBoxLayout()
        vbox_right.addWidget(QLabel("修復後動画"))
        vbox_right.addWidget(right_widget)
        layout.addLayout(vbox_left)
        layout.addLayout(vbox_right)
        vbox_main = QVBoxLayout()
        vbox_main.addLayout(layout)
        vbox_main.addWidget(play_btn)
        dlg.setLayout(vbox_main)
        dlg.resize(900, 400)
        dlg.exec_()
