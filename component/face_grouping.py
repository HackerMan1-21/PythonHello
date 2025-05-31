"""
face_grouping.py
顔認識による画像グループ化・グループごとのファイル移動ユーティリティ。

主な機能:
- 画像リストから顔特徴量でグループ化
- 類似顔グループごとのディレクトリ移動

依存:
- face_recognition, os, shutil
"""

import os
import shutil
try:
    import face_recognition
except ImportError:
    face_recognition = None
from component.thumbnail.thumbnail_util import get_thumbnail_for_file, pil_image_to_qpixmap
from PyQt5.QtWidgets import QGroupBox, QVBoxLayout, QGridLayout, QLabel, QPushButton, QWidget, QDialog, QDialogButtonBox
from PyQt5.QtCore import Qt

def get_face_groups(file_list):
    if face_recognition is None:
        raise ImportError("face_recognitionライブラリが必要です")
    groups = []
    encodings = []
    for f in file_list:
        try:
            img = face_recognition.load_image_file(f)
            faces = face_recognition.face_encodings(img)
            if faces:
                encodings.append((f, faces[0]))
        except Exception:
            continue
    used = set()
    for i, (f1, enc1) in enumerate(encodings):
        if f1 in used:
            continue
        group = [f1]
        used.add(f1)
        for j, (f2, enc2) in enumerate(encodings):
            if i != j and f2 not in used:
                dist = face_recognition.face_distance([enc1], enc2)[0]
                if dist < 0.5:
                    group.append(f2)
                    used.add(f2)
        groups.append(group)
    return groups

def group_by_face_and_move(file_list, out_dir):
    groups = get_face_groups(file_list)
    for idx, group in enumerate(groups):
        group_dir = os.path.join(out_dir, f"face_group_{idx+1}")
        os.makedirs(group_dir, exist_ok=True)
        for f in group:
            try:
                shutil.move(f, group_dir)
            except Exception:
                continue

def show_face_grouping_dialog(parent, groups, move_selected_files_to_folder_func):
    if not groups:
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.information(parent, "顔グループ化", "顔グループは見つかりませんでした")
        return
    dlg = QDialog(parent)
    dlg.setWindowTitle("顔グループごとに個別振り分け")
    vbox = QVBoxLayout()
    group_checkboxes = []
    for group in groups:
        group_box = QGroupBox("顔グループ")
        grid = QGridLayout()
        for idx, f in enumerate(group):
            pil_thumb = get_thumbnail_for_file(f, (120, 90))
            thumb_label = QLabel()
            thumb_label.setPixmap(pil_image_to_qpixmap(pil_thumb))
            thumb_label.setFixedSize(120, 90)
            name_label = QLabel(os.path.basename(f))
            name_label.setStyleSheet("font-size:13px;color:#00ffe7;")
            path_label = QLabel(f)
            path_label.setStyleSheet("font-size:10px;color:#00ff99;max-width:140px;")
            path_label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextBrowserInteraction)
            path_label.setWordWrap(True)
            def open_folder(event, path=f):
                folder = os.path.dirname(path)
                if os.path.exists(folder):
                    import subprocess
                    subprocess.Popen(f'explorer "{folder}"')
            path_label.mousePressEvent = open_folder
            cb = QPushButton("移動")
            cb.setStyleSheet("font-size:12px;color:#ffb300;")
            cb.clicked.connect(lambda _, path=f: move_selected_files_to_folder_func([(True, path)], dlg))
            vbox2 = QVBoxLayout()
            vbox2.addWidget(thumb_label)
            vbox2.addWidget(name_label)
            vbox2.addWidget(path_label)
            vbox2.addWidget(cb)
            file_widget = QWidget()
            file_widget.setLayout(vbox2)
            row = idx // 4
            col = idx % 4
            grid.addWidget(file_widget, row, col)
        group_box.setLayout(grid)
        vbox.addWidget(group_box)
    btns = QDialogButtonBox(QDialogButtonBox.Close)
    btns.rejected.connect(dlg.reject)
    vbox.addWidget(btns)
    dlg.setLayout(vbox)
    dlg.exec_()
