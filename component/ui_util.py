# component/ui_util.py
# UI部品生成・グループUI構築・比較再生・詳細ダイアログなど
from PyQt5.QtWidgets import QLabel, QVBoxLayout, QWidget, QDialog, QHBoxLayout, QFileDialog, QMessageBox
from PyQt5.QtGui import QPixmap
from PyQt5.QtCore import Qt
import os
import shutil
from component.thumbnail_util import get_thumbnail_for_file
from component.file_util import move_to_trash

def show_detail_dialog(parent, file_path):
    info = f"パス: {file_path}\n"
    try:
        size = os.path.getsize(file_path)
        info += f"サイズ: {size/1024/1024:.2f} MB\n"
    except Exception:
        info += "サイズ: 不明\n"
    QMessageBox.information(parent, "ファイル詳細", info)

def show_compare_dialog(parent, file1, file2, get_thumbnail_for_file):
    if not file2:
        QMessageBox.information(parent, "比較再生", "比較対象がありません")
        return
    dlg = QDialog(parent)
    dlg.setWindowTitle("比較再生")
    layout = QHBoxLayout()
    for f in [file1, file2]:
        label = QLabel(os.path.basename(f))
        thumb = None
        try:
            thumb = get_thumbnail_for_file(f)
        except Exception:
            pass
        thumb_label = QLabel()
        if thumb:
            thumb_label.setPixmap(QPixmap.fromImage(thumb).scaled(180, 120))
        else:
            thumb_label.setText("No Thumbnail")
        vbox = QVBoxLayout()
        vbox.addWidget(label)
        vbox.addWidget(thumb_label)
        w = QWidget()
        w.setLayout(vbox)
        layout.addWidget(w)
    dlg.setLayout(layout)
    dlg.exec_()

def add_thumbnail_widget(parent, content_layout, file_path, toggle_select, selected_paths, delete_btn):
    thumb = get_thumbnail_for_file(file_path)
    thumb_label = QLabel()
    if thumb:
        thumb_label.setPixmap(QPixmap.fromImage(thumb).scaled(120, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    else:
        thumb_label.setText("No Thumbnail")
    thumb_label.setFixedSize(120, 90)
    name_label = QLabel(os.path.basename(file_path))
    name_label.setStyleSheet("font-size:13px;color:#00ffe7;")
    hbox = QHBoxLayout()
    hbox.addWidget(thumb_label)
    hbox.addWidget(name_label)
    widget = QWidget()
    widget.setLayout(hbox)
    widget.setStyleSheet("background:rgba(0,0,0,0.3);border-radius:8px;margin:4px 0;padding:4px 8px;")
    widget.mousePressEvent = lambda e, p=file_path: toggle_select(widget, p, selected_paths, delete_btn)
    content_layout.addWidget(widget)

def get_save_file_path(parent, title, default_path, filter_str):
    return QFileDialog.getSaveFileName(parent, title, default_path, filter_str)

def show_info_dialog(parent, title, message):
    QMessageBox.information(parent, title, message)

def show_warning_dialog(parent, title, message):
    QMessageBox.warning(parent, title, message)

def show_question_dialog(parent, title, message):
    return QMessageBox.question(parent, title, message, QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

def update_progress(progress_bar, value, progress_time_label=None, eta_label=None, elapsed=None):
    progress_bar.setValue(value)
    if progress_time_label is not None and elapsed is not None:
        progress_time_label.setText(f"経過: {elapsed:.1f}秒")
    if eta_label is not None and elapsed is not None:
        eta_label.setText(f"完了: {elapsed:.1f}秒")

def drag_enter_event(event):
    if event.mimeData().hasUrls():
        event.acceptProposedAction()
    else:
        event.ignore()

def drop_event(event, process_files_func):
    files = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
    if not files:
        return
    process_files_func(files)

def delete_selected_dialog(parent, selected_paths, reload_folder_func):
    if not selected_paths:
        QMessageBox.information(parent, "削除", "削除するファイルを選択してください")
        return
    msg = QMessageBox(parent)
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
        for path in list(selected_paths):
            try:
                move_to_trash(path)
            except Exception:
                failed.append(path)
    elif msg.clickedButton() == move_btn:
        target_dir = QFileDialog.getExistingDirectory(parent, "移動先フォルダを選択（新規作成可）")
        if not target_dir:
            return
        for path in list(selected_paths):
            try:
                shutil.move(path, target_dir)
            except Exception:
                failed.append(path)
    if failed:
        QMessageBox.warning(parent, "失敗", f"一部のファイルの移動/削除に失敗しました:\n" + '\n'.join(failed))
    else:
        QMessageBox.information(parent, "削除/移動", f"{len(selected_paths)}件のファイルを処理しました")
    reload_folder_func()
    selected_paths.clear()
