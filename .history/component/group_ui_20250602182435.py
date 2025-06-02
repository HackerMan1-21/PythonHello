"""
group_ui.py
重複グループ・顔グループ・壊れ動画グループなどのUI部品生成ユーティリティ。

主な機能:
- 重複グループUIの生成（サムネイル・詳細・削除・比較ボタン付き）
- 顔グループダイアログの表示
- サムネイル取得・型変換・キャッシュ利用の統一

依存:
- PyQt5, component.thumbnail.thumbnail_util
"""

print("DEBUG: group_ui.py loaded from", __file__)

# component/group_ui.py
# グループUI部品生成（重複グループ・顔グループ・壊れ動画グループなど）
from PyQt5.QtWidgets import QGroupBox, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QWidget, QCheckBox, QDialog, QDialogButtonBox, QMessageBox, QFileDialog, QGridLayout
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QPixmap, QIcon
from PIL import Image, ImageDraw
import os
import shutil
from component.thumbnail.thumbnail_util import get_thumbnail_for_file, pil_image_to_qpixmap
from PyQt5.QtCore import QTimer

def create_duplicate_group_ui(group, get_thumbnail_for_file, detail_cb, delete_cb, compare_cb, thumb_cache=None, defer_queue=None, thumb_widget_map=None):
    group_box = QGroupBox("重複グループ")
    grid = QGridLayout()
    grid.setHorizontalSpacing(12)
    grid.setVerticalSpacing(16)
    max_col = 4
    for idx, f in enumerate(group):
        thumb_btn = QPushButton()
        thumb_btn.setFixedSize(180, 180)
        def set_icon(btn=thumb_btn, path=f):
            pil_thumb = get_thumbnail_for_file(path, (180, 180), cache=thumb_cache, defer_queue=defer_queue)
            from component.thumbnail.thumbnail_util import pil_image_to_qpixmap
            btn.setIcon(QIcon(pil_image_to_qpixmap(pil_thumb)))
            btn.setIconSize(QSize(180, 180))
        QTimer.singleShot(0, set_icon)
        thumb_btn.setStyleSheet("background:transparent;border:2px solid #00ffe7;border-radius:10px;")
        # サムネイルボタンのクリックで詳細ダイアログを表示
        thumb_btn.clicked.connect(lambda _, path=f: detail_cb(parent, path))
        if thumb_widget_map is not None:
            thumb_widget_map[f] = thumb_btn
        fname = os.path.basename(f)
        name_label = QLabel(fname)
        name_label.setAlignment(Qt.AlignCenter)
        name_label.setStyleSheet("font-size:12px;color:#00ffe7;font-weight:bold;max-width:180px;")
        name_label.setWordWrap(True)
        path_label = QLabel(f)
        path_label.setAlignment(Qt.AlignCenter)
        path_label.setWordWrap(True)
        path_label.setStyleSheet("font-size:10px;color:#00ff99;max-width:180px;")
        path_label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextBrowserInteraction)
        def open_folder(event, path=f):
            import os, subprocess, sys
            folder = os.path.dirname(path)
            if os.path.exists(folder):
                try:
                    os.startfile(folder)
                except AttributeError:
                    # 非Windows環境用
                    if sys.platform == "darwin":
                        subprocess.Popen(["open", folder])
                    else:
                        subprocess.Popen(["xdg-open", folder])
        path_label.mousePressEvent = open_folder
        del_btn = QPushButton("削除")
        del_btn.setStyleSheet("font-size:12px;color:#ff00c8;max-width:180px;")
        del_btn.setFixedWidth(180)
        del_btn.clicked.connect(lambda _, path=f: delete_cb(path))
        vbox = QVBoxLayout()
        vbox.setSpacing(2)
        vbox.setContentsMargins(2, 2, 2, 2)
        vbox.addWidget(thumb_btn)
        vbox.addWidget(name_label)
        vbox.addWidget(path_label)
        vbox.addWidget(del_btn)
        file_widget = QWidget()
        file_widget.setLayout(vbox)
        row = idx // max_col
        col = idx % max_col
        grid.addWidget(file_widget, row, col)
    group_box.setLayout(grid)
    print("DEBUG: create_duplicate_group_ui returning", group_box)
    return group_box

def show_face_grouping_dialog(parent, groups, move_selected_files_to_folder_func, delete_cb=None, thumb_cache=None, defer_queue=None):
    print("DEBUG: show_face_grouping_dialog called", groups, thumb_cache, delete_cb)
    if not groups:
        QMessageBox.information(parent, "顔グループ化", "顔グループは見つかりませんでした")
        return
    dlg = QDialog(parent)
    dlg.setWindowTitle("顔グループごとに個別振り分け")
    vbox = QVBoxLayout()
    group_checkboxes = []
    max_col = 4
    for group in groups:
        group_box = QGroupBox("顔グループ")
        grid = QGridLayout()
        for idx, f in enumerate(group):
            thumb_btn = QPushButton()
            thumb_btn.setFixedSize(180, 180)
            def set_icon(btn=thumb_btn, path=f):
                pil_thumb = get_thumbnail_for_file(path, (180, 180), cache=thumb_cache, defer_queue=defer_queue)
                from component.thumbnail.thumbnail_util import pil_image_to_qpixmap
                btn.setIcon(QIcon(pil_image_to_qpixmap(pil_thumb)))
                btn.setIconSize(QSize(180, 180))
            QTimer.singleShot(0, set_icon)
            thumb_btn.setStyleSheet("background:transparent;border:2px solid #00ff99;border-radius:10px;")
            fname = os.path.basename(f)
            maxlen = 18
            fname_disp = fname[:8] + '...' + fname[-7:] if len(fname) > maxlen else fname
            name_label = QLabel(fname_disp)
            name_label.setAlignment(Qt.AlignCenter)
            name_label.setStyleSheet("font-size:12px;color:#00ff99;font-weight:bold;")
            try:
                size = os.path.getsize(f)
                size_mb = size / 1024 / 1024
                size_str = f"{size_mb:.2f} MB"
            except Exception:
                size_str = "-"
            size_label = QLabel(size_str)
            size_label.setAlignment(Qt.AlignCenter)
            size_label.setStyleSheet("font-size:11px;color:#00ff99;")
            path_label = QLabel(f)
            path_label.setAlignment(Qt.AlignCenter)
            path_label.setWordWrap(True)
            path_label.setStyleSheet("font-size:10px;color:#00ff99;max-width:140px;")
            path_label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextBrowserInteraction)
            def open_folder(event, path=f):
                import os
                import subprocess
                print(f"DEBUG: open_folder clicked: {path}")
                path = os.path.abspath(os.path.normpath(path))
                if os.path.isdir(path):
                    folder = path
                else:
                    folder = os.path.dirname(path)
                print(f"DEBUG: open_folder will open: {folder}")
                if os.path.exists(folder):
                    subprocess.Popen(f'explorer "{folder}"')
            path_label.mousePressEvent = open_folder
            cb = QCheckBox("選択")
            group_checkboxes.append((cb, f))
            del_btn = QPushButton("削除")
            del_btn.setStyleSheet("font-size:12px;color:#ff00c8;")
            if delete_cb:
                del_btn.clicked.connect(lambda _, path=f: delete_cb(path))
            else:
                del_btn.clicked.connect(lambda _, path=f: (os.remove(path) if os.path.exists(path) else None))
            vbox2 = QVBoxLayout()
            vbox2.addWidget(thumb_btn)
            vbox2.addWidget(name_label)
            vbox2.addWidget(size_label)
            vbox2.addWidget(path_label)
            vbox2.addWidget(cb)
            vbox2.addWidget(del_btn)
            file_widget = QWidget()
            file_widget.setLayout(vbox2)
            row = idx // max_col
            col = idx % max_col
            grid.addWidget(file_widget, row, col)
        group_box.setLayout(grid)
        vbox.addWidget(group_box)
    move_btn = QPushButton("選択したファイルをフォルダに移動")
    move_btn.clicked.connect(lambda: move_selected_files_to_folder_func(group_checkboxes, dlg))
    vbox.addWidget(move_btn)
    btns = QDialogButtonBox(QDialogButtonBox.Close)
    btns.rejected.connect(dlg.reject)
    vbox.addWidget(btns)
    dlg.setLayout(vbox)
    dlg.exec_()

def move_selected_files_to_folder(checkboxes, parent):
    print("DEBUG: move_selected_files_to_folder called", checkboxes)
    target_dir = QFileDialog.getExistingDirectory(parent, "移動先フォルダを選択（新規作成可）")
    if not target_dir:
        return
    failed = []
    for cb, path in checkboxes:
        if cb.isChecked():
            try:
                shutil.move(path, target_dir)
            except Exception:
                failed.append(path)
    if failed:
        QMessageBox.warning(parent, "失敗", f"一部のファイルの移動に失敗しました:\n" + '\n'.join(failed))
    else:
        QMessageBox.information(parent, "移動完了", "選択したファイルを移動しました")
    parent.accept()

def show_broken_video_dialog(parent, broken_groups, run_mp4_repair, run_mp4_convert, run_mp4_digital_repair, thumb_cache=None, defer_queue=None):
    print("DEBUG: show_broken_video_dialog called", broken_groups, thumb_cache)
    if not broken_groups:
        from component.ui_util import show_info_dialog
        show_info_dialog(parent, "壊れ動画検出", "壊れた動画は見つかりませんでした")
        return
    dlg = QDialog(parent)
    dlg.setWindowTitle("壊れ動画グループ")
    vbox = QVBoxLayout()
    max_col = 4
    for group in broken_groups:
        group_box = QGroupBox("壊れ動画グループ")
        grid = QGridLayout()
        for idx, f in enumerate(group):
            thumb_btn = QPushButton()
            thumb_btn.setFixedSize(180, 180)
            def set_icon(btn=thumb_btn, path=f):
                pil_thumb = get_thumbnail_for_file(path, (180, 180), cache=thumb_cache, defer_queue=defer_queue)
                from component.thumbnail.thumbnail_util import pil_image_to_qpixmap
                btn.setIcon(QIcon(pil_image_to_qpixmap(pil_thumb)))
                btn.setIconSize(QSize(180, 180))
            QTimer.singleShot(0, set_icon)
            thumb_btn.setStyleSheet("background:transparent;border:2px solid #ff4444;border-radius:10px;")
            fname = os.path.basename(f)
            maxlen = 18
            fname_disp = fname[:8] + '...' + fname[-7:] if len(fname) > maxlen else fname
            name_label = QLabel(fname_disp)
            name_label.setAlignment(Qt.AlignCenter)
            name_label.setStyleSheet("font-size:12px;color:#ff4444;font-weight:bold;")
            try:
                size = os.path.getsize(f)
                size_mb = size / 1024 / 1024
                size_str = f"{size_mb:.2f} MB"
            except Exception:
                size_str = "-"
            size_label = QLabel(size_str)
            size_label.setAlignment(Qt.AlignCenter)
            size_label.setStyleSheet("font-size:11px;color:#00ff99;")
            path_label = QLabel(f)
            path_label.setAlignment(Qt.AlignCenter)
            path_label.setWordWrap(True)
            path_label.setStyleSheet("font-size:10px;color:#00ff99;max-width:140px;")
            path_label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextBrowserInteraction)
            def open_folder(event, path=f):
                import os
                import subprocess
                print(f"DEBUG: open_folder clicked: {path}")
                path = os.path.abspath(os.path.normpath(path))
                if os.path.isdir(path):
                    folder = path
                else:
                    folder = os.path.dirname(path)
                print(f"DEBUG: open_folder will open: {folder}")
                if os.path.exists(folder):
                    subprocess.Popen(f'explorer "{folder}"')
            path_label.mousePressEvent = open_folder
            repair_btn = QPushButton("修復")
            repair_btn.setStyleSheet("font-size:11px;color:#00ffe7;border:2px solid #00ffe7;border-radius:8px;")
            repair_btn.clicked.connect(lambda _, path=f: run_mp4_repair(path))
            convert_btn = QPushButton("変換")
            convert_btn.setStyleSheet("font-size:11px;color:#00ff99;border:2px solid #00ff99;border-radius:8px;")
            convert_btn.clicked.connect(lambda _, path=f: run_mp4_convert(path))
            digital_btn = QPushButton("デジタル修復")
            digital_btn.setStyleSheet("font-size:11px;color:#ff44ff;border:2px solid #ff44ff;border-radius:8px;")
            digital_btn.clicked.connect(lambda _, path=f: run_mp4_digital_repair(path))
            del_btn = QPushButton("削除")
            del_btn.setStyleSheet("font-size:12px;color:#ff00c8;")
            del_btn.clicked.connect(lambda _, path=f: os.remove(path) if os.path.exists(path) else None)
            vbox2 = QVBoxLayout()
            vbox2.addWidget(thumb_btn)
            vbox2.addWidget(name_label)
            vbox2.addWidget(size_label)
            vbox2.addWidget(path_label)
            vbox2.addWidget(repair_btn)
            vbox2.addWidget(convert_btn)
            vbox2.addWidget(digital_btn)
            vbox2.addWidget(del_btn)
            file_widget = QWidget()
            file_widget.setLayout(vbox2)
            row = idx // max_col
            col = idx % max_col
            grid.addWidget(file_widget, row, col)
        group_box.setLayout(grid)
        vbox.addWidget(group_box)
    btns = QDialogButtonBox(QDialogButtonBox.Close)
    btns.rejected.connect(dlg.reject)
    vbox.addWidget(btns)
    dlg.setLayout(vbox)
    dlg.exec_()

def create_error_group_ui(error_files, get_thumbnail_for_file, detail_cb, delete_cb, thumb_cache=None, defer_queue=None, thumb_widget_map=None):
    group_box = QGroupBox("サムネイル生成エラー/壊れファイル")
    grid = QGridLayout()
    grid.setHorizontalSpacing(12)
    grid.setVerticalSpacing(16)
    max_col = 4
    for idx, f in enumerate(error_files):
        # サムネイル取得不可なのでNo Thumbnail表示
        thumb_btn = QPushButton()
        thumb_btn.setFixedSize(180, 180)
        thumb_btn.setIconSize(QSize(180, 180))
        thumb_btn.setText("No Thumbnail")
        thumb_btn.setStyleSheet("background:transparent;border:2px solid #ff4444;color:#ff4444;font-size:15px;border-radius:10px;")
        thumb_btn.clicked.connect(lambda _, path=f: detail_cb(path))
        if thumb_widget_map is not None:
            thumb_widget_map[f] = thumb_btn
        fname = os.path.basename(f)
        name_label = QLabel(fname)
        name_label.setAlignment(Qt.AlignCenter)
        name_label.setStyleSheet("font-size:12px;color:#ff4444;font-weight:bold;max-width:180px;")
        name_label.setWordWrap(True)
        path_label = QLabel(f)
        path_label.setAlignment(Qt.AlignCenter)
        path_label.setWordWrap(True)
        path_label.setStyleSheet("font-size:10px;color:#ffb300;max-width:180px;")
        path_label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextBrowserInteraction)
        def open_folder(event, path=f):
            import os, subprocess, sys
            folder = os.path.dirname(path)
            if os.path.exists(folder):
                try:
                    os.startfile(folder)
                except AttributeError:
                    if sys.platform == "darwin":
                        subprocess.Popen(["open", folder])
                    else:
                        subprocess.Popen(["xdg-open", folder])
        path_label.mousePressEvent = open_folder
        del_btn = QPushButton("削除")
        del_btn.setStyleSheet("font-size:12px;color:#ff00c8;max-width:180px;")
        del_btn.setFixedWidth(180)
        del_btn.clicked.connect(lambda _, path=f: delete_cb(path))
        vbox = QVBoxLayout()
        vbox.setSpacing(2)
        vbox.setContentsMargins(2, 2, 2, 2)
        vbox.addWidget(thumb_btn)
        vbox.addWidget(name_label)
        vbox.addWidget(path_label)
        vbox.addWidget(del_btn)
        file_widget = QWidget()
        file_widget.setLayout(vbox)
        row = idx // max_col
        col = idx % max_col
        grid.addWidget(file_widget, row, col)
    group_box.setLayout(grid)
    return group_box
