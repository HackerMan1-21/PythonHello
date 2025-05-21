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

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), "imagehash"))
import cv2
from PIL import Image
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QFileDialog, QListWidget, QMessageBox, QScrollArea, QGroupBox, QProgressBar,
    QInputDialog, QDialog, QGridLayout
)
from PyQt5.QtGui import QPixmap, QImage, QCursor
from PyQt5.QtCore import Qt, QUrl, QThread, pyqtSignal, QObject
from PyQt5.QtGui import QDesktopServices
import imagehash
import subprocess
import concurrent.futures

# --- ここから追加: send2trash, face_recognition の定義 ---
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
# --- 追加ここまで ---

# --- ここから追加: CPUコア数取得とワーカー数決定 ---
def get_cpu_count():
    """利用可能なCPUコア数を返す（Noneの場合は1を返す）"""
    count = os.cpu_count()
    return count if count and count > 0 else 1

def get_num_workers(max_workers=None):
    """
    並列処理用のワーカー数を返す。
    max_workersを指定した場合はその最大値を超えない。
    """
    cpu_count = get_cpu_count()
    if max_workers is not None:
        return min(cpu_count, max_workers)
    return cpu_count
# --- 追加ここまで ---

def get_video_files(folder):
    exts = ('.mp4', '.avi', '.mov', '.mkv', '.wmv')
    video_files = []
    for root, dirs, files in os.walk(folder):
        for f in files:
            if f.lower().endswith(exts):
                video_files.append(os.path.join(root, f))
    return video_files

def get_video_hash(filepath, frame_count=5):
    cap = cv2.VideoCapture(filepath)
    length = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    hashes = []
    if length == 0 or frame_count == 0:
        cap.release()
        return hashes
    for i in range(frame_count):
        frame_no = int(length * i / (frame_count - 1)) if frame_count > 1 else 0
        frame_no = min(frame_no, length - 1)
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
        ret, frame = cap.read()
        if not ret:
            continue
        img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(img)
        hash_val = imagehash.phash(pil_img)
        hashes.append(hash_val)
    cap.release()
    return hashes

def are_videos_similar(hashes1, hashes2, threshold=8):
    if len(hashes1) != len(hashes2) or not hashes1:
        return False
    dist = sum(int(h1 - h2) for h1, h2 in zip(hashes1, hashes2))
    return dist < threshold

def group_similar_videos(video_files, progress_callback=None):
    hashes = []
    total = len(video_files)
    for idx, f in enumerate(video_files):
        try:
            h = get_video_hash(f)
            hashes.append((f, h))
        except Exception:
            continue
        if progress_callback:
            progress_callback(idx + 1, total)
    groups = []
    used = set()
    for i, (f1, h1) in enumerate(hashes):
        if f1 in used:
            continue
        group = [f1]
        for j, (f2, h2) in enumerate(hashes):
            if i != j and f2 not in used and are_videos_similar(h1, h2):
                group.append(f2)
                used.add(f2)
        used.add(f1)
        if len(group) > 1:
            groups.append(group)
    return groups

def get_video_thumbnail(filepath):
    cap = cv2.VideoCapture(filepath)
    ret, frame = cap.read()
    cap.release()
    if not ret:
        return None
    img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w, ch = img.shape
    bytes_per_line = ch * w
    qimg = QImage(img.data, w, h, bytes_per_line, QImage.Format_RGB888)
    # サムネイルサイズを240x240に変更
    pixmap = QPixmap.fromImage(qimg).scaled(240, 240, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return pixmap

def get_image_files(folder):
    exts = ('.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff')
    image_files = []
    for root, dirs, files in os.walk(folder):
        for f in files:
            if f.lower().endswith(exts):
                image_files.append(os.path.join(root, f))
    return image_files

def get_image_thumbnail(filepath):
    try:
        img = Image.open(filepath)
        img = img.convert("RGB")
        # サムネイルサイズを240x240に変更
        img.thumbnail((240, 240))
        data = img.tobytes("raw", "RGB")
        qimg = QImage(data, img.width, img.height, QImage.Format_RGB888)
        pixmap = QPixmap.fromImage(qimg)
        return pixmap
    except Exception:
        return None

def get_image_hash(filepath):
    try:
        img = Image.open(filepath)
        img = img.convert("RGB")
        return imagehash.phash(img)
    except Exception:
        return None

def group_similar_images(image_files, progress_callback=None):
    hashes = []
    total = len(image_files)
    for idx, f in enumerate(image_files):
        h = get_image_hash(f)
        if h is not None:
            hashes.append((f, h))
        if progress_callback:
            progress_callback(idx + 1, total)
    groups = []
    used = set()
    for i, (f1, h1) in enumerate(hashes):
        if f1 in used:
            continue
        group = [f1]
        for j, (f2, h2) in enumerate(hashes):
            # --- ここを修正: h1/h2がNoneでないことを明示的にチェック ---
            if i != j and f2 not in used and h1 is not None and h2 is not None and abs(h1 - h2) < 8:
                group.append(f2)
                used.add(f2)
        used.add(f1)
        if len(group) > 1:
            groups.append(group)
    return groups

class FaceGroupWorker(QObject):
    finished = pyqtSignal(list)
    progress = pyqtSignal(int, int)
    error = pyqtSignal(str)

    def __init__(self, image_files):
        super().__init__()
        self.image_files = image_files

    def run(self):
        try:
            groups = self._analyze_and_group_faces(self.image_files)
            self.finished.emit(groups)
        except Exception as e:
            self.error.emit(str(e))

    def _extract_face_encoding(self, f):
        try:
            img = face_recognition.load_image_file(f)
            faces = face_recognition.face_encodings(img)
            if faces:
                return (f, faces[0])
        except Exception:
            pass
        return None

    def _analyze_and_group_faces(self, image_files):
        if face_recognition is None:
            raise RuntimeError("face_recognitionライブラリが必要です。")
        encodings = []
        paths = []
        total = len(image_files)
        results = []
        # 並列で顔特徴量抽出
        with concurrent.futures.ProcessPoolExecutor(max_workers=get_num_workers(8)) as executor:
            results = list(executor.map(self._extract_face_encoding, image_files))
        for res in results:
            if res:
                f, encoding = res
                encodings.append(encoding)
                paths.append(f)
        groups = []
        used = set()
        for i, enc1 in enumerate(encodings):
            if i in used:
                continue
            group = [paths[i]]
            for j, enc2 in enumerate(encodings):
                if i != j and j not in used:
                    match = face_recognition.compare_faces([enc1], enc2, tolerance=0.5)[0]
                    if match:
                        group.append(paths[j])
                        used.add(j)
            used.add(i)
            if len(group) > 0:
                groups.append(group)
        return groups

class VideoFaceGroupWorker(QObject):
    finished = pyqtSignal(list)
    progress = pyqtSignal(int, int)
    error = pyqtSignal(str)

    def __init__(self, video_files):
        super().__init__()
        self.video_files = video_files

    def run(self):
        try:
            groups = self._group_videos_by_face(self.video_files)
            self.finished.emit(groups)
        except Exception as e:
            import traceback
            self.error.emit(f"{e}\n{traceback.format_exc()}")

    def _extract_video_face_encoding(self, filepath, sample_frames=10):
        """
        動画からsample_frames個のフレームを等間隔でサンプリングし、
        最初に見つかった顔特徴量を返す（なければNone）。
        """
        import numpy as np
        try:
            cap = cv2.VideoCapture(filepath)
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if frame_count == 0:
                cap.release()
                return None
            indices = np.linspace(0, frame_count - 1, sample_frames, dtype=int)
            encodings = []
            for idx in indices:
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ret, frame = cap.read()
                if not ret:
                    continue
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                try:
                    faces = face_recognition.face_encodings(rgb)
                except Exception:
                    faces = []
                if faces:
                    encodings.append(faces[0])
            cap.release()
            if encodings:
                # 複数フレームの顔特徴量の平均を代表値とする
                return (filepath, np.mean(encodings, axis=0))
            else:
                return None
        except Exception:
            return None

    def _group_videos_by_face(self, video_files):
        """
        動画ファイルリストを顔特徴量でグループ化する。
        """
        import numpy as np
        from sklearn.cluster import DBSCAN

        if face_recognition is None:
            raise RuntimeError("face_recognitionライブラリが必要です。")

        encodings = []
        paths = []
        total = len(video_files)
        # 並列で顔特徴量抽出
        with concurrent.futures.ThreadPoolExecutor(max_workers=get_num_workers(4)) as executor:
            futures = [executor.submit(self._extract_video_face_encoding, f) for f in video_files]
            for idx, future in enumerate(concurrent.futures.as_completed(futures)):
                res = future.result()
                if res:
                    f, encoding = res
                    paths.append(f)
                    encodings.append(encoding)
                self.progress.emit(idx + 1, total)

        if not encodings:
            return []

        # DBSCANクラスタリング
        X = np.stack(encodings)
        clustering = DBSCAN(eps=0.6, min_samples=1, metric='euclidean').fit(X)
        labels = clustering.labels_

        groups = []
        for label in set(labels):
            group = [paths[i] for i, l in enumerate(labels) if l == label]
            if group:
                groups.append(group)
        return groups

class VideoDuplicateFinder(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("動画・画像重複検出ツール")
        self.layout = QVBoxLayout()
        self.progress_label = QLabel("進捗: 0%")
        self.layout.addWidget(self.progress_label)
        self.progress_bar = QProgressBar()
        self.layout.addWidget(self.progress_bar)
        self.folder_btn = QPushButton("フォルダ選択（動画）")
        self.folder_btn.clicked.connect(self.select_folder)
        self.layout.addWidget(self.folder_btn)
        self.img_btn = QPushButton("フォルダ選択（画像）")
        self.img_btn.clicked.connect(self.select_image_folder)
        self.layout.addWidget(self.img_btn)
        # self.face_btn = QPushButton("画像整理（顔でグループ化）")
        # self.face_btn.clicked.connect(self.group_faces)
        # self.layout.addWidget(self.face_btn)
        # self.video_face_btn = QPushButton("動画整理（顔でグループ化）")
        # self.video_face_btn.clicked.connect(self.group_video_faces)
        # self.layout.addWidget(self.video_face_btn)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout()
        self.scroll_content.setLayout(self.scroll_layout)
        self.scroll.setWidget(self.scroll_content)
        self.layout.addWidget(self.scroll)
        self.setLayout(self.layout)
        self.duplicates = []
        self.image_files = []
        self.video_files = []

        # send2trashが使えない場合は警告
        if not SEND2TRASH_AVAILABLE:
            QMessageBox.warning(self, "警告", "send2trashライブラリが見つかりません。削除機能は無効化されます。")

    def clear_scroll(self):
        while self.scroll_layout.count():
            child = self.scroll_layout.takeAt(0)
            widget = child.widget()
            if widget:
                widget.deleteLater()

    def update_progress(self, current, total):
        import time
        percent = int(current / total * 100)
        self.progress_label.setText(f"進捗: {percent}%")
        self.progress_bar.setValue(percent)
        # --- 残り時間の推定表示 ---
        if not hasattr(self, "_progress_times"):
            self._progress_times = []
        now = time.time()
        self._progress_times.append((current, now))
        # 進捗が2以上進んだら計算
        if current > 1 and len(self._progress_times) > 1:
            prev_count, prev_time = self._progress_times[-2]
            dt = now - prev_time
            dcount = current - prev_count
            if dcount > 0 and dt > 0:
                speed = dcount / dt  # 件数/秒
                remain = total - current
                if speed > 0:
                    remain_sec = remain / speed
                    if remain_sec > 1:
                        mins = int(remain_sec // 60)
                        secs = int(remain_sec % 60)
                        remain_str = f"推定残り時間: {mins}分{secs}秒"
                    else:
                        remain_str = "推定残り時間: 1秒未満"
                    self.progress_label.setText(f"進捗: {percent}%　{remain_str}")
        QApplication.processEvents()

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "動画フォルダ選択")
        if not folder:
            return
        # --- ここで処理方法を選択 ---
        method, ok = QInputDialog.getItem(
            self,
            "処理方法選択",
            "このフォルダで実行する処理を選んでください：",
            ["重複チェック", "顔でグループ化"],
            0,
            False
        )
        if not ok:
            return
        self.clear_scroll()
        self.progress_label.setText("進捗: 0%")
        self.progress_bar.setValue(0)
        video_files = get_video_files(folder)
        self.video_files = video_files
        self.image_files = []  # 画像リストをクリア
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        if method == "重複チェック":
            self.duplicates = group_similar_videos(video_files, progress_callback=self.update_progress)
            self.progress_label.setText("進捗: 100%")
            self.progress_bar.setValue(100)
            if not self.duplicates:
                QMessageBox.information(self, "結果", "重複動画は見つかりませんでした。")
                return
            self.show_video_duplicates(self.duplicates)
        elif method == "顔でグループ化":
            if not video_files:
                QMessageBox.warning(self, "エラー", "動画ファイルが見つかりません。")
                return
            self.run_video_face_group_worker(video_files)

    def select_image_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "画像フォルダ選択")
        if not folder:
            return
        self.clear_scroll()
        self.progress_label.setText("進捗: 0%")
        self.progress_bar.setValue(0)
        image_files = get_image_files(folder)
        self.image_files = image_files
        self.video_files = []  # 動画リストをクリア（動画は無視）
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.run_face_group_worker(image_files)

    def run_face_group_worker(self, image_files):
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_label.setText("顔グループ化中...")
        self.face_thread = QThread()
        self.face_worker = FaceGroupWorker(image_files)
        self.face_worker.moveToThread(self.face_thread)
        self.face_thread.started.connect(self.face_worker.run)
        self.face_worker.finished.connect(self.on_face_group_finished)
        self.face_worker.progress.connect(self.update_progress)
        self.face_worker.error.connect(self.on_face_group_error)
        self.face_worker.finished.connect(self.face_thread.quit)
        self.face_worker.finished.connect(self.face_worker.deleteLater)
        self.face_thread.finished.connect(self.face_thread.deleteLater)
        self.face_thread.start()

    def on_face_group_finished(self, groups):
        self.progress_label.setText("進捗: 100%")
        self.progress_bar.setValue(100)
        if not groups:
            QMessageBox.information(self, "結果", "顔グループは見つかりませんでした。")
            return
        # グループごとに移動UI
        for idx, group in enumerate(groups):
            folder_name, ok = QInputDialog.getText(self, "フォルダ名入力", f"グループ{idx+1}のフォルダ名を入力してください:")
            if not ok or not folder_name:
                folder_name = f"group_{idx+1}"
            base_dir = os.path.dirname(group[0])
            dest_dir = os.path.join(base_dir, folder_name)
            os.makedirs(dest_dir, exist_ok=True)
            for f in group:
                try:
                    basename = os.path.basename(f)
                    new_path = os.path.join(dest_dir, basename)
                    if os.path.abspath(f) != os.path.abspath(new_path):
                        os.rename(f, new_path)
                except Exception:
                    continue
        QMessageBox.information(self, "完了", "顔ごとに画像をフォルダ分けしました。")

    def on_face_group_error(self, msg):
        # --- ここから修正: エラー内容とコマンド例を表示 ---
        msg_detail = (
            f"{msg}\n\n"
            "【対策例】\n"
            "コマンドプロンプトで下記を実行してください:\n"
            "  python -m pip install git+https://github.com/ageitgey/face_recognition_models\n"
            "または\n"
            "  pip install git+https://github.com/ageitgey/face_recognition_models\n"
            "（実行中のPython環境でインストールしてください）"
        )
        QMessageBox.warning(self, "エラー", msg_detail)

    def run_video_face_group_worker(self, video_files):
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_label.setText("動画顔グループ化中...")
        self.video_face_thread = QThread()
        self.video_face_worker = VideoFaceGroupWorker(video_files)
        self.video_face_worker.moveToThread(self.video_face_thread)
        self.video_face_thread.started.connect(self.video_face_worker.run)
        self.video_face_worker.finished.connect(self.on_video_face_group_finished)
        self.video_face_worker.progress.connect(self.update_progress)
        self.video_face_worker.error.connect(self.on_face_group_error)
        self.video_face_worker.finished.connect(self.video_face_thread.quit)
        self.video_face_worker.finished.connect(self.video_face_worker.deleteLater)
        self.video_face_thread.finished.connect(self.video_face_thread.deleteLater)
        self.video_face_thread.start()

    def on_video_face_group_finished(self, groups):
        self.progress_label.setText("進捗: 100%")
        self.progress_bar.setValue(100)
        if not groups:
            QMessageBox.information(self, "結果", "顔グループは見つかりませんでした。")
            return
        # グループごとに確認UI
        for idx, group in enumerate(groups):
            dlg = QDialog(self)
            dlg.setWindowTitle(f"動画グループ{idx+1}の確認")
            vbox = QVBoxLayout()
            label = QLabel("このグループの動画は同じ人物ですか？")
            vbox.addWidget(label)
            thumbs_layout = QHBoxLayout()
            for f in group:
                thumb = get_video_thumbnail(f)
                thumb_label = QLabel()
                if thumb:
                    thumb_label.setPixmap(thumb)
                thumb_label.setToolTip(os.path.basename(f))
                thumbs_layout.addWidget(thumb_label)
            vbox.addLayout(thumbs_layout)
            btn_ok = QPushButton("このグループでOK")
            btn_skip = QPushButton("やり直し（このグループは移動しない）")
            btns = QHBoxLayout()
            btns.addWidget(btn_ok)
            btns.addWidget(btn_skip)
            vbox.addLayout(btns)
            dlg.setLayout(vbox)
            result = []
            def accept():
                result.append(True)
                dlg.accept()
            def reject():
                result.append(False)
                dlg.reject()
            btn_ok.clicked.connect(accept)
            btn_skip.clicked.connect(reject)
            dlg.exec_()
            if not result or not result[0]:
                continue
            folder_name, ok = QInputDialog.getText(self, "フォルダ名入力", f"動画グループ{idx+1}のフォルダ名を入力してください:")
            if not ok or not folder_name:
                folder_name = f"video_group_{idx+1}"
            base_dir = os.path.dirname(group[0])
            dest_dir = os.path.join(base_dir, folder_name)
            os.makedirs(dest_dir, exist_ok=True)
            for f in group:
                try:
                    basename = os.path.basename(f)
                    new_path = os.path.join(dest_dir, basename)
                    if os.path.abspath(f) != os.path.abspath(new_path):
                        os.rename(f, new_path)
                except Exception:
                    continue
        QMessageBox.information(self, "完了", "顔ごとに動画をフォルダ分けしました。")

    def break_long_path(self, path, maxlen=40):
        # パスをmaxlenごとに<br>で改行
        import re
        if len(path) <= maxlen:
            return path
        # ディレクトリ区切りで分割しつつ、maxlen超えたら<br>を挿入
        parts = re.split(r'([\\/])', path)
        lines = []
        line = ""
        for part in parts:
            if len(line) + len(part) > maxlen and line:
                lines.append(line)
                line = part
            else:
                line += part
        if line:
            lines.append(line)
        return "<br>".join(lines)

    def show_video_duplicates(self, groups):
        # グループごとにグリッドレイアウトで表示
        for idx, group in enumerate(groups):
            group_widget = QWidget()
            grid_layout = QGridLayout()
            # グリッドの隙間を1pxに設定（float不可のため）
            grid_layout.setHorizontalSpacing(1)
            grid_layout.setVerticalSpacing(1)
            group_widget.setLayout(grid_layout)
            thumb_widgets = []
            for i, f in enumerate(group):
                if not os.path.exists(f):
                    continue  # 削除済みファイルはスキップ
                v_layout = QVBoxLayout()
                thumb = get_video_thumbnail(f)
                thumb_label = QLabel()
                if thumb:
                    thumb_label.setPixmap(thumb)
                # サムネイルの余白を減らす
                thumb_label.setContentsMargins(2, 2, 2, 2)
                v_layout.addWidget(thumb_label)
                title_label = QLabel(f"<b>{os.path.basename(f)}</b>")
                title_label.setMaximumWidth(240)
                v_layout.addWidget(title_label)
                folder_path = os.path.dirname(f)
                # フォルダパスの最大幅を広げて全体表示しやすくする
                folder_html = self.break_long_path(folder_path, maxlen=80)
                folder_label = QLabel(f'<a href="{folder_path}">{folder_html}</a>')
                folder_label.setTextFormat(Qt.RichText)
                folder_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
                folder_label.setOpenExternalLinks(False)
                folder_label.linkActivated.connect(self.open_folder)
                folder_label.setWordWrap(True)
                folder_label.setMaximumWidth(240)
                v_layout.addWidget(folder_label)
                try:
                    size_mb = os.path.getsize(f) / (1024 * 1024)
                    size_label = QLabel(f"サイズ: {size_mb:.2f} MB")
                except Exception:
                    size_label = QLabel("サイズ: 不明")
                size_label.setMaximumWidth(240)
                v_layout.addWidget(size_label)
                cell_widget = QWidget()
                cell_widget.setLayout(v_layout)
                del_btn = QPushButton("削除")
                del_btn.setEnabled(SEND2TRASH_AVAILABLE)
                del_btn.setFixedWidth(80)
                def make_delete_func(path, cell_widget, group, group_widget, grid_layout):
                    return lambda: self.delete_video_and_widget(path, cell_widget, group, group_widget, grid_layout)
                del_btn.clicked.connect(make_delete_func(f, cell_widget, group, group_widget, grid_layout))
                v_layout.addWidget(del_btn)
                # セルの最小幅を設定
                cell_widget.setMinimumWidth(250)
                row = i // 4
                col = i % 4
                grid_layout.addWidget(cell_widget, row, col)
            self.scroll_layout.addWidget(group_widget)
            separator = QLabel("<hr>")
            separator.setTextFormat(Qt.RichText)
            self.scroll_layout.addWidget(separator)

    def delete_video_and_widget(self, path, cell_widget, group, group_widget, grid_layout):
        if not SEND2TRASH_AVAILABLE:
            QMessageBox.warning(self, "エラー", "send2trashライブラリが見つかりません。削除できません。")
            return
        reply = QMessageBox.question(self, "確認", f"{os.path.basename(path)} を削除しますか？", QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            try:
                norm_path = os.path.normpath(path)
                if norm_path.startswith(r"\\?\\"):
                    norm_path = norm_path[4:]
                send2trash(norm_path)
                QMessageBox.information(self, "削除", f"{os.path.basename(path)} をゴミ箱に移動しました。")
                # cell_widgetをグリッドから削除し破棄
                cell_widget.setParent(None)
                # groupからも削除
                if path in group:
                    group.remove(path)
                # グループが空 or 全て削除済みなら全体を消す
                if not [f for f in group if os.path.exists(f)]:
                    group_widget.setParent(None)
            except Exception as e:
                QMessageBox.warning(self, "エラー", f"削除できませんでした: {e}")

    def show_image_duplicates(self, groups):
        for group in groups:
            group_widget = QWidget()
            grid_layout = QGridLayout()
            grid_layout.setHorizontalSpacing(1)
            grid_layout.setVerticalSpacing(1)
            group_widget.setLayout(grid_layout)
            for i, f in enumerate(group):
                # --- ここを追加: ファイルが存在しない場合はスキップ ---
                if not os.path.exists(f):
                    continue
                v_layout = QVBoxLayout()
                thumb = get_image_thumbnail(f)
                thumb_label = QLabel()
                if thumb:
                    thumb_label.setPixmap(thumb)
                thumb_label.setContentsMargins(2, 2, 2, 2)
                v_layout.addWidget(thumb_label)
                title_label = QLabel(f"<b>{os.path.basename(f)}</b>")
                title_label.setMaximumWidth(240)
                v_layout.addWidget(title_label)
                folder_path = os.path.dirname(f)
                # フォルダパスの最大幅を広げて全体表示しやすくする
                folder_html = self.break_long_path(folder_path, maxlen=80)
                folder_label = QLabel(f'<a href="{folder_path}">{folder_html}</a>')
                folder_label.setTextFormat(Qt.RichText)
                folder_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
                folder_label.setOpenExternalLinks(False)
                folder_label.linkActivated.connect(self.open_folder)
                folder_label.setWordWrap(True)
                folder_label.setMaximumWidth(240)
                v_layout.addWidget(folder_label)
                try:
                    size_mb = os.path.getsize(f) / (1024 * 1024)
                    size_label = QLabel(f"サイズ: {size_mb:.2f} MB")
                except Exception:
                    size_label = QLabel("サイズ: 不明")
                size_label.setMaximumWidth(240)
                v_layout.addWidget(size_label)
                del_btn = QPushButton("削除")
                del_btn.setEnabled(SEND2TRASH_AVAILABLE)
                del_btn.setFixedWidth(80)
                cell_widget = QWidget()
                cell_widget.setLayout(v_layout)
                def make_delete_func(path, cell_widget, group, group_widget, grid_layout):
                    return lambda: self.delete_image_and_widget(path, cell_widget, group, group_widget, grid_layout)
                del_btn.clicked.connect(make_delete_func(f, cell_widget, group, group_widget, grid_layout))
                v_layout.addWidget(del_btn)
                cell_widget.setMinimumWidth(250)
                row = i // 4
                col = i % 4
                grid_layout.addWidget(cell_widget, row, col)
            self.scroll_layout.addWidget(group_widget)
            separator = QLabel("<hr>")
            separator.setTextFormat(Qt.RichText)
            self.scroll_layout.addWidget(separator)

    def delete_image_and_widget(self, path, cell_widget, group, group_widget, grid_layout):
        if not SEND2TRASH_AVAILABLE:
            QMessageBox.warning(self, "エラー", "send2trashライブラリが見つかりません。削除できません。")
            return
        reply = QMessageBox.question(self, "確認", f"{os.path.basename(path)} を削除しますか？", QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            try:
                norm_path = os.path.normpath(path)
                if norm_path.startswith(r"\\?\\"):
                    norm_path = norm_path[4:]
                send2trash(norm_path)
                QMessageBox.information(self, "削除", f"{os.path.basename(path)} をゴミ箱に移動しました。")
                # cell_widgetをグリッドから削除し破棄
                cell_widget.setParent(None)
                if path in group:
                    group.remove(path)
                if not [f for f in group if os.path.exists(f)]:
                    group_widget.setParent(None)
            except Exception as e:
                QMessageBox.warning(self, "エラー", f"削除できませんでした: {e}")

    def group_faces(self):
        if not self.image_files:
            QMessageBox.warning(self, "エラー", "先に画像フォルダを選択してください。")
            return
        # analyze_and_group_faces(self.image_files, self)
        self.run_face_group_worker(self.image_files)

    def group_video_faces(self):
        if not self.video_files:
            QMessageBox.warning(self, "エラー", "先に動画フォルダを選択してください。")
            return
        # analyze_and_group_video_faces(self.video_files, self)
        self.run_video_face_group_worker(self.video_files)

    def open_folder(self, folder_path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder_path))

# このメッセージの意味：
# face_recognitionライブラリを使うには、顔認識用の学習済みモデル（face_recognition_models）が必要です。
# それがインストールされていないため、エラーが出ています。
#
# 解決方法：
# コマンドプロンプトで下記のコマンドを実行してください。
#   pip install git+https://github.com/ageitgey/face_recognition_models
# これで必要なモデルデータがインストールされ、face_recognitionが使えるようになります。

# face_recognition_modelsをインストールしても
# 「Please install `face_recognition_models` ...」と出る場合の主な原因：

# 1. Pythonのバージョンや仮想環境が複数あり、face_recognitionとface_recognition_modelsが別の環境にインストールされている
# 2. pipでインストールした場所と、実行しているpython.exeの場所が違う
# 3. face_recognition_modelsのインストールが失敗している
# 4. 権限不足やパスの問題でPythonがmodelsを見つけられない

# 対策例：
# ・「python -m pip install ...」で、実行しているPython環境にインストールする
# ・「pip show face_recognition_models」「pip show face_recognition」でパスを確認
# ・「where python」「where pip」で実際に使われているパスを確認
# ・仮想環境を使っている場合は、必ずその環境をアクティブにしてからpip installする
# ・インストール後にPythonを再起動する

# 例:
#   python -m pip install git+https://github.com/ageitgey/face_recognition_models

if __name__ == "__main__":
    try:
        # --- 依存ライブラリのチェック ---
        missing = []
        try:
            import PyQt5
        except ImportError:
            missing.append("PyQt5")
        try:
            import imagehash
        except ImportError:
            missing.append("imagehash")
        try:
            import cv2
        except ImportError:
            missing.append("opencv-python")
        # send2trash, face_recognitionは既存のtryでOK

        if missing:
            msg = "必要なライブラリが見つかりません: " + ", ".join(missing) + "\n"
            msg += "コマンドプロンプトで下記を実行してください:\n"
            for lib in missing:
                msg += f"  pip install {lib}\n"
            print(msg)
            try:
                from PyQt5.QtWidgets import QApplication, QMessageBox
                app = QApplication(sys.argv)
                QMessageBox.critical(None, "エラー", msg)
            except Exception:
                pass
            sys.exit(1)

        app = QApplication(sys.argv)
        win = VideoDuplicateFinder()
        win.show()
        sys.exit(app.exec_())
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print("エラーが発生しました:", e)
        print(tb)
        try:
            from PyQt5.QtWidgets import QApplication, QMessageBox
            # QApplicationがまだなければ作る
            if QApplication.instance() is None:
                app = QApplication(sys.argv)
            QMessageBox.critical(None, "致命的エラー", f"{e}\n\n{tb}")
        except Exception:
            pass
        sys.exit(1)
