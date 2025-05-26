# このツールの使い方:
# 1. このスクリプトを実行してください。
# 2. ウィンドウが開いたら「フォルダ選択」ボタンを押します。
# 3. 重複チェックしたい動画ファイルが入ったフォルダを選択します。
# 4. 重複候補がリスト表示されます。重複がなければ「重複動画は見つかりませんでした」と表示されます。
# ※ 削除や残す操作は今後追加予定です。現状は重複候補の確認のみ可能です。
#
# 【スクリプトの実行方法】
# コマンドプロンプトで下記のように入力してください:
#   python video_duplicate_finder.py
# （カレントディレクトリがこのファイルの場所になっていることを確認してください）

import os
import sys
import cv2
import numpy as np
from PIL import Image
import imagehash
import concurrent.futures
from cache_util import save_cache, load_cache
import tempfile
from shutil import move as shutil_move
import hashlib

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

from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QFileDialog, QListWidget, QMessageBox, QScrollArea, QGroupBox, QProgressBar,
    QInputDialog, QDialog, QGridLayout, QLineEdit, QDialogButtonBox, QListWidgetItem
)
from PyQt5.QtGui import QPixmap, QImage, QCursor, QIcon
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QObject, QSize

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
def get_image_phash(filepath, folder=None):
    filepath = normalize_path(filepath)
    def calc_func(path):
        try:
            img = Image.open(path).convert("RGB")
            return imagehash.phash(img)
        except Exception:
            return None
    return get_features_with_cache(filepath, calc_func, folder)

def get_video_phash(filepath, frame_count=7, folder=None):
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

# --- サムネイル生成 ---
def get_image_thumbnail(filepath, size=(240,240)):
    filepath = normalize_path(filepath)
    try:
        img = Image.open(filepath).convert("RGB")
        img.thumbnail(size)
        return img
    except Exception:
        return None

def get_video_thumbnail(filepath, size=(240,240)):
    filepath = normalize_path(filepath)
    cap = cv2.VideoCapture(filepath)
    ret, frame = cap.read()
    cap.release()
    if not ret:
        return None
    img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(img)
    pil_img.thumbnail(size)
    return pil_img

# --- ゴミ箱移動 ---
def move_to_trash(filepath):
    filepath = normalize_path(filepath)
    if SEND2TRASH_AVAILABLE:
        send2trash(filepath)
    else:
        os.remove(filepath)

# --- GUI ---
class DuplicateFinderGUI(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("動画・画像重複検出ツール")
        self.resize(900, 600)
        self.folder = None
        self.groups = []
        self.init_ui()

    def init_ui(self):
        self.setStyleSheet('''
            QWidget {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0f2027, stop:0.5 #2c5364, stop:1 #232526);
                color: #00ffe7;
                font-family: "Consolas", "Fira Mono", "Meiryo UI", monospace;
                font-size: 14px;
                letter-spacing: 1px;
            }
            QLabel {
                color: #00ffe7;
                text-shadow: 0 0 6px #00ffe7, 0 0 2px #00ffe7;
            }
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #232526, stop:1 #0f2027);
                color: #00ffe7;
                border: 2px solid #00ffe7;
                border-radius: 10px;
                padding: 8px 16px;
                font-size: 15px;
                font-family: "Consolas", monospace;
                font-weight: bold;
                text-shadow: 0 0 6px #00ffe7;
                box-shadow: 0 0 12px #00ffe733;
                transition: all 0.2s;
            }
            QPushButton:hover {
                background: #00ffe7;
                color: #232526;
                border: 2px solid #00ffe7;
                box-shadow: 0 0 24px #00ffe7;
            }
            QPushButton:pressed {
                background: #232526;
                color: #00ffe7;
                border: 2px solid #00ffe7;
            }
            QProgressBar {
                background: #232526;
                border: 2px solid #00ffe7;
                border-radius: 8px;
                text-align: center;
                color: #00ffe7;
                font-size: 14px;
                font-family: "Consolas", monospace;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00ffe7, stop:1 #00ff99);
                border-radius: 8px;
                box-shadow: 0 0 16px #00ffe7;
            }
            QScrollArea {
                background: transparent;
                border: none;
            }
        ''')
        layout = QVBoxLayout()
        self.folder_label = QLabel("フォルダ未選択")
        self.folder_label.setStyleSheet("font-size:20px;font-weight:bold;padding:8px 0 8px 0;color:#00ffe7;text-shadow:0 0 8px #00ffe7;")
        self.select_btn = QPushButton("[ フォルダ選択 ]")
        self.select_btn.setStyleSheet("font-size:17px;font-weight:bold;background:transparent;color:#00ffe7;border:2px solid #00ffe7;")
        self.select_btn.clicked.connect(self.select_folder)
        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress_time_label = QLabel("")
        self.progress_time_label.setStyleSheet("font-size:13px;color:#00ff99;padding:2px 0 8px 0;text-shadow:0 0 8px #00ff99;")
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
        layout.addWidget(self.folder_label)
        layout.addWidget(self.select_btn)
        layout.addWidget(self.progress)
        layout.addWidget(self.progress_time_label)
        layout.addWidget(self.scroll_area)
        layout.addWidget(self.delete_btn)
        self.setLayout(layout)
        self.selected_paths = set()

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
        import time
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
        # 画像
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            for idx, (f, h) in enumerate(zip(image_files, executor.map(lambda x: get_image_phash(x, self.folder), image_files))):
                file_hashes.append((f, h))
                self.progress.setValue(idx+1)
                elapsed = time.time() - start_time
                done = idx+1
                if done > 0:
                    remain = total - done
                    speed = elapsed / done
                    eta = int(remain * speed)
                    if eta > 0:
                        m, s = divmod(eta, 60)
                        self.progress_time_label.setText(f"残り目安: {m}分{s}秒")
                    else:
                        self.progress_time_label.setText("")
        # 動画
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            for idx, (f, h) in enumerate(zip(video_files, executor.map(lambda x: get_video_phash(x, 7, self.folder), video_files))):
                file_hashes.append((f, h))
                self.progress.setValue(len(image_files)+idx+1)
                elapsed = time.time() - start_time
                done = len(image_files)+idx+1
                if done > 0:
                    remain = total - done
                    speed = elapsed / done
                    eta = int(remain * speed)
                    if eta > 0:
                        m, s = divmod(eta, 60)
                        self.progress_time_label.setText(f"残り目安: {m}分{s}秒")
                    else:
                        self.progress_time_label.setText("")
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
                hbox = QHBoxLayout()
                btns = []
                for f in group:
                    vbox = QVBoxLayout()
                    # サムネイル
                    if f.lower().endswith(video_exts):
                        thumb_img = get_video_thumbnail(f, size=(240,240))
                    else:
                        thumb_img = get_image_thumbnail(f, size=(240,240))
                    if thumb_img is not None:
                        rgb_img = thumb_img.convert("RGB")
                        w, h = rgb_img.size
                        data = rgb_img.tobytes()
                        qimg = QImage(data, w, h, w*3, QImage.Format_RGB888)
                        pixmap = QPixmap.fromImage(qimg)
                        icon = QIcon(pixmap.scaled(240, 240, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                        btn = QPushButton()
                        btn.setIcon(icon)
                        btn.setIconSize(QSize(240,240))
                        btn.setProperty("filepath", f)
                        btn.clicked.connect(self.on_thumbnail_clicked)
                        btn.setStyleSheet("background:transparent;border:2px solid #00ffe7;border-radius:14px;box-shadow:0 0 16px #00ffe7;")
                        vbox.addWidget(btn)
                        btns.append(btn)
                    else:
                        btn = QPushButton(f)
                        btn.setProperty("filepath", f)
                        btn.clicked.connect(self.on_thumbnail_clicked)
                        btn.setStyleSheet("background:transparent;border:2px solid #00ffe7;border-radius:14px;box-shadow:0 0 16px #00ffe7;")
                        vbox.addWidget(btn)
                        btns.append(btn)
                    # ファイル名
                    fname = os.path.basename(f)
                    label_name = QLabel(fname)
                    label_name.setAlignment(Qt.AlignCenter)
                    label_name.setStyleSheet("font-size:14px;color:#00ffe7;font-weight:bold;text-shadow:0 0 8px #00ffe7;")
                    vbox.addWidget(label_name)
                    # ファイルパス
                    label_path = QLabel(f)
                    label_path.setAlignment(Qt.AlignCenter)
                    label_path.setStyleSheet("font-size:11px;color:#00ff99;text-shadow:0 0 8px #00ff99;")
                    vbox.addWidget(label_path)
                    vbox.addStretch()
                    wgt = QWidget()
                    wgt.setLayout(vbox)
                    hbox.addWidget(wgt)
                self.thumb_buttons.append(btns)
                hbox.addStretch()
                group_widget = QWidget()
                group_widget.setLayout(hbox)
                self.content_layout.addWidget(group_widget)
        self.content_layout.addStretch()
        self.delete_btn.setEnabled(False)
        self.progress_time_label.setText("")

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
        if msg.clickedButton() == trash_btn:
            # ゴミ箱に移動
            for path in list(self.selected_paths):
                if not path or not os.path.isfile(path):
                    failed.append(path)
                    continue
                try:
                    move_to_trash(path)
                    self.selected_paths.remove(path)
                except Exception as e:
                    failed.append(f"{path} : {e}")
            if failed:
                QMessageBox.warning(self, "削除エラー", "\n".join(map(str, failed)))
            else:
                QMessageBox.information(self, "削除", "選択ファイルをゴミ箱に移動しました")
        elif msg.clickedButton() == move_btn:
            # 別フォルダに移動
            target_dir = QFileDialog.getExistingDirectory(self, "移動先フォルダを選択")
            if not target_dir:
                return
            for path in list(self.selected_paths):
                if not path or not os.path.isfile(path):
                    failed.append(path)
                    continue
                try:
                    fname = os.path.basename(path)
                    dest = os.path.join(target_dir, fname)
                    # 既存ならリネーム
                    base, ext = os.path.splitext(fname)
                    count = 1
                    while os.path.exists(dest):
                        dest = os.path.join(target_dir, f"{base}_copy{count}{ext}")
                        count += 1
                    shutil_move(path, dest)
                    self.selected_paths.remove(path)
                except Exception as e:
                    failed.append(f"{path} : {e}")
            if failed:
                QMessageBox.warning(self, "移動エラー", "\n".join(map(str, failed)))
            else:
                QMessageBox.information(self, "移動", "選択ファイルを指定フォルダへ移動しました")
        self.find_duplicates()

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "フォルダ選択")
        if folder:
            self.folder = folder
            self.folder_label.setText(folder)
            self.find_duplicates()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = DuplicateFinderGUI()
    win.show()
    sys.exit(app.exec_())
