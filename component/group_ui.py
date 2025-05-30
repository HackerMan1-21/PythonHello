# component/group_ui.py
# グループUI部品生成（重複グループ・顔グループ・壊れ動画グループなど）
from PyQt5.QtWidgets import QGroupBox, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QWidget, QCheckBox, QDialog, QDialogButtonBox, QMessageBox, QFileDialog
import os
import shutil

def create_duplicate_group_ui(group, get_thumbnail_for_file, detail_cb, delete_cb, compare_cb):
    group_box = QGroupBox("重複グループ")
    group_layout = QHBoxLayout()
    for f in group:
        thumb = get_thumbnail_for_file(f)
        thumb_label = QLabel()
        if thumb:
            thumb_label.setPixmap(thumb.scaled(120, 90))
        else:
            thumb_label.setText("No Thumbnail")
        thumb_label.setFixedSize(120, 90)
        name_label = QLabel(os.path.basename(f))
        name_label.setStyleSheet("font-size:13px;color:#00ffe7;")
        detail_btn = QPushButton("詳細")
        detail_btn.setStyleSheet("font-size:12px;color:#00ff99;")
        detail_btn.clicked.connect(lambda _, path=f: detail_cb(path))
        del_btn = QPushButton("削除")
        del_btn.setStyleSheet("font-size:12px;color:#ff00c8;")
        del_btn.clicked.connect(lambda _, path=f: delete_cb(path))
        compare_btn = QPushButton("比較")
        compare_btn.setStyleSheet("font-size:12px;color:#ffb300;")
        compare_btn.clicked.connect(lambda _, path=f, group=group: compare_cb(path, [x for x in group if x != path][0] if len(group)>1 else None))
        vbox = QVBoxLayout()
        vbox.addWidget(thumb_label)
        vbox.addWidget(name_label)
        vbox.addWidget(detail_btn)
        vbox.addWidget(del_btn)
        vbox.addWidget(compare_btn)
        file_widget = QWidget()
        file_widget.setLayout(vbox)
        group_layout.addWidget(file_widget)
    group_box.setLayout(group_layout)
    return group_box

def show_face_grouping_dialog(parent, groups, move_selected_files_to_folder_func):
    if not groups:
        QMessageBox.information(parent, "顔グループ化", "顔グループは見つかりませんでした")
        return
    dlg = QDialog(parent)
    dlg.setWindowTitle("顔グループごとに個別振り分け")
    vbox = QVBoxLayout()
    group_checkboxes = []
    for group in groups:
        group_box = QGroupBox("顔グループ")
        group_layout = QHBoxLayout()
        for f in group:
            cb = QCheckBox(os.path.basename(f))
            group_layout.addWidget(cb)
            group_checkboxes.append((cb, f))
        group_box.setLayout(group_layout)
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
