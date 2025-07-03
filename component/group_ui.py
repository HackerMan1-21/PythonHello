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
from PyQt5.QtCore import QSize, Qt
from PyQt5.QtWidgets import QMenu
from PyQt5 import QtCore
from PyQt5.QtGui import QPixmap, QIcon
from PIL import Image, ImageDraw
import os
import shutil
from component.thumbnail.thumbnail_util import get_thumbnail_for_file, pil_image_to_qpixmap
from PyQt5.QtCore import QTimer, Qt, QThread, pyqtSignal, QObject
from typing import Optional

from component.thumbnail.thumbnail_util import ThumbnailCache
from typing import Any

def create_duplicate_group_ui(
    group: list,
    get_thumbnail_for_file,
    detail_cb,
    delete_cb,
    compare_cb,
    thumb_cache: Optional[ThumbnailCache] = None,
    defer_queue=None,
    thumb_widget_map=None,
    parent=None,
    elapsed_time=None,
    eta_time=None,
    remain_count=None
) -> QGroupBox:
    group_box = QGroupBox(f"重複グループ（残り: {len(group)}ファイル）")
    grid = QGridLayout()
    grid.setHorizontalSpacing(12)
    grid.setVerticalSpacing(16)
    max_col = 4
    if len(group) < max_col:
        max_col = len(group)
    if isinstance(thumb_cache, ThumbnailCache):
        cache_dict = thumb_cache.cache
    elif isinstance(thumb_cache, dict):
        cache_dict = thumb_cache
    else:
        cache_dict = {}
    video_info_cache = {}
    thumb_btn_map = {}
    size_label_map = {}
    threads = []  # QThread参照保持用
    # cache_dictは上で定義済み
    for idx, f in enumerate(group):
        # cache_dictは関数先頭で定義済み
        thumb_btn = QPushButton()
        thumb_btn.setStyleSheet("background:transparent;border:0;padding:0;")
        thumb_btn.setFixedSize(140, 140)
        from component.thumbnail.thumbnail_util import get_no_thumbnail_image, pil_image_to_qpixmap
        thumb_btn.setIcon(QIcon(pil_image_to_qpixmap(get_no_thumbnail_image((140, 140)))))
        thumb_btn.setIconSize(QSize(140, 140))
        thumb_btn.setStyleSheet("background:transparent;border:2px solid #00ffe7;border-radius:10px;")
        thumb_btn.clicked.connect(lambda _, path=f: detail_cb(parent, path))
        info_vbox = QVBoxLayout()
        info_vbox.setSpacing(4)
        info_vbox.setContentsMargins(0, 0, 0, 0)
        fname = os.path.basename(f)
        name_label = QLabel(fname)
        name_label.setStyleSheet("font-size:12px;color:#00ffe7;font-weight:bold;max-width:140px;")
        name_label.setMaximumWidth(140)
        size_label = QLabel("取得中...")
        size_label.setStyleSheet("font-size:11px;color:#00ff99;max-width:140px;")
        size_label.setMaximumWidth(140)
        size_label.setWordWrap(True)
        folder_path = os.path.dirname(f)
        folder_parts = folder_path.replace("\\", "/").rstrip("/").split("/")
        if len(folder_parts) >= 2:
            last2 = "/".join(folder_parts[-2:])
        elif len(folder_parts) == 1:
            last2 = folder_parts[0]
        else:
            last2 = folder_path
        path_label = QLabel(last2)
        path_label.setStyleSheet("font-size:10px;color:#00ff99;max-width:140px;")
        path_label.setMaximumWidth(140)
        path_label.setWordWrap(True)
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        open_folder_btn = QPushButton("フォルダを開く")
        open_folder_btn.setStyleSheet("font-size:11px;color:#00ff99;border:1px solid #00ff99;border-radius:6px;")
        def open_folder(path: str) -> None:
            import os
            import subprocess
            import sys
            folder = os.path.dirname(path)
            if os.path.exists(folder):
                if sys.platform.startswith('win'):
                    os.startfile(folder)
                elif sys.platform.startswith('darwin'):
                    subprocess.Popen(['open', folder])
                else:
                    subprocess.Popen(['xdg-open', folder])
        open_folder_btn.clicked.connect(lambda _, path=f: open_folder(path))
        del_btn = QPushButton("削除")
        del_btn.setStyleSheet("font-size:12px;color:#ff00c8;max-width:140px;")
        del_btn.setFixedWidth(140)
        del_btn.clicked.connect(lambda _, path=f: delete_cb(path))
        btn_hbox = QHBoxLayout()
        btn_hbox.setSpacing(8)
        btn_hbox.setContentsMargins(0, 0, 0, 0)
        open_folder_btn.setFixedWidth(80)
        del_btn.setFixedWidth(80)
        btn_hbox.addWidget(open_folder_btn)
        btn_hbox.addWidget(del_btn)
        info_vbox.addWidget(name_label)
        info_vbox.addWidget(size_label)
        info_vbox.addWidget(path_label)
        info_vbox.addLayout(btn_hbox)
        info_widget = QWidget()
        info_widget.setLayout(info_vbox)
        info_widget.setFixedWidth(140)
        hbox = QHBoxLayout()
        hbox.setSpacing(0)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.addWidget(thumb_btn)
        hbox.addWidget(info_widget)
        file_widget = QWidget()
        file_widget.setLayout(hbox)
        row = idx // max_col
        col = idx % max_col
        grid.addWidget(file_widget, row, col)
        thumb_btn_map[f] = thumb_btn
        size_label_map[f] = size_label
        if (f not in cache_dict) or (f not in video_info_cache):
            thread = QThread()
            worker = ThumbInfoWorker(f, cache_dict, video_info_cache, (140, 140), get_thumbnail_for_file)
            worker.moveToThread(thread)
            def on_finished(path, pil_thumb, info_tuple, btn=thumb_btn, label=size_label, thread=thread, worker=worker):
                if pil_thumb is not None:
                    btn.setIcon(QIcon(pil_image_to_qpixmap(pil_thumb)))
                label.setText(f"{info_tuple[0]}{info_tuple[1]}")
                thread.quit()
                thread.wait()
                thread.deleteLater()
                worker.deleteLater()
            worker.finished.connect(on_finished)
            thread.started.connect(worker.run)
            thread.start()
            threads.append(thread)
        else:
            pil_thumb = cache_dict[f]
            btn = thumb_btn_map[f]
            btn.setIcon(QIcon(pil_image_to_qpixmap(pil_thumb)))
            size_str, duration_str = video_info_cache[f]
            size_label_map[f].setText(f"サイズ: {size_str}{duration_str}")
    n_items = len(group)
    if n_items % max_col != 0:
        last_row = n_items // max_col
        for col in range(n_items % max_col, max_col):
            spacer = QWidget()
            spacer.setFixedWidth(140)
            grid.addWidget(spacer, last_row, col)
    group_box.setLayout(grid)
    parent_dialog = parent
    if parent_dialog is not None and hasattr(parent_dialog, 'finished'):
        def _cleanup_threads():
            for t in threads:
                if t.isRunning():
                    t.quit()
                    t.wait()
        parent_dialog.finished.connect(_cleanup_threads)
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
    if thumb_cache is None:
        thumb_cache = {}
    video_info_cache = {}
    thumb_btn_map = {}
    size_label_map = {}
    threads = []  # QThread参照保持用
    # cache_dictを必ず定義
    if isinstance(thumb_cache, ThumbnailCache):
        cache_dict = thumb_cache.cache
    elif isinstance(thumb_cache, dict):
        cache_dict = thumb_cache
    else:
        cache_dict = {}
    for group in groups:
        group_box = QGroupBox(f"顔グループ（残り: {len(group)}ファイル）")
        grid = QGridLayout()
        for idx, f in enumerate(group):
            thumb_btn = QPushButton()
            thumb_btn.setStyleSheet("background:transparent;border:0;padding:0;")
            thumb_btn.setFixedSize(140, 140)
            from component.thumbnail.thumbnail_util import get_no_thumbnail_image, pil_image_to_qpixmap
            thumb_btn.setIcon(QIcon(pil_image_to_qpixmap(get_no_thumbnail_image((140, 140)))))
            thumb_btn.setIconSize(QSize(140, 140))
            thumb_btn.setStyleSheet("background:transparent;border:2px solid #00ff99;border-radius:10px;")
            fname = os.path.basename(f)
            maxlen = 18
            fname_disp = fname[:8] + '...' + fname[-7:] if len(fname) > maxlen else fname
            name_label = QLabel(fname_disp)
            name_label.setStyleSheet("font-size:12px;color:#00ff99;font-weight:bold;")
            size_label = QLabel("取得中...")
            size_label.setStyleSheet("font-size:11px;color:#00ff99;")
            path_label = QLabel(f)
            path_label.setStyleSheet("font-size:10px;color:#00ff99;max-width:140px;")
            path_label.setWordWrap(True)
            path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
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
            thumb_btn_map[f] = thumb_btn
            size_label_map[f] = size_label
            if (f not in cache_dict) or (f not in video_info_cache):
                thread = QThread()
                worker = ThumbInfoWorker(f, thumb_cache, video_info_cache, (140, 140), get_thumbnail_for_file)
                worker.moveToThread(thread)
                def on_finished(path, pil_thumb, info_tuple, btn=thumb_btn, label=size_label, thread=thread, worker=worker):
                    if pil_thumb is not None:
                        btn.setIcon(QIcon(pil_image_to_qpixmap(pil_thumb)))
                    label.setText(f"{info_tuple[0]}{info_tuple[1]}")
                    thread.quit()
                    thread.wait()
                    thread.deleteLater()
                    worker.deleteLater()
                worker.finished.connect(on_finished)
                thread.started.connect(worker.run)
                thread.start()
                threads.append(thread)
            else:
                pil_thumb = cache_dict[f]
                btn = thumb_btn_map[f]
                btn.setIcon(QIcon(pil_image_to_qpixmap(pil_thumb)))
                size_str, duration_str = video_info_cache[f]
                size_label_map[f].setText(f"{size_str}{duration_str}")
        group_box.setLayout(grid)
        vbox.addWidget(group_box)
        remain_label = QLabel(f"残り: {len(group)}ファイル")
        remain_label.setStyleSheet("font-size:12px;color:#00ff99;font-weight:bold;margin-top:4px;")
        vbox.addWidget(remain_label)
    move_btn = QPushButton("選択したファイルをフォルダに移動")
    move_btn.clicked.connect(lambda: move_selected_files_to_folder_func(group_checkboxes, dlg))
    vbox.addWidget(move_btn)
    btns = QDialogButtonBox(QDialogButtonBox.Close)
    btns.rejected.connect(dlg.reject)
    vbox.addWidget(btns)
    dlg.setLayout(vbox)
    def _cleanup_threads():
        for t in threads:
            if t.isRunning():
                t.quit()
                t.wait()
    dlg.finished.connect(_cleanup_threads)
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
    page_size = 8
    current_page = [0]
    total_pages = (len(broken_groups) + page_size - 1) // page_size
    if thumb_cache is None:
        thumb_cache = {}
    video_info_cache = {}
    thumb_btn_map = {}
    size_label_map = {}
    threads = []  # QThread参照保持用
    def update_page(groups, group_checkboxes, delete_cb):
        # cache_dictをupdate_page内でも参照できるように
        if isinstance(thumb_cache, ThumbnailCache):
            cache_dict = thumb_cache.cache
        elif isinstance(thumb_cache, dict):
            cache_dict = thumb_cache
        else:
            cache_dict = {}
        while vbox.count() > 0:
            item = vbox.takeAt(0)
            if item is not None:
                w = item.widget() if hasattr(item, 'widget') else None
                if w is not None:
                    w.deleteLater()
        start = current_page[0] * page_size
        end = min(start + page_size, len(groups))
        for group in groups[start:end]:
            group_box = QGroupBox(f"壊れ動画グループ（残り: {len(group)}ファイル）")
            grid = QGridLayout()
            max_col = 4
            for idx, f in enumerate(group):
                thumb_btn = QPushButton()
                thumb_btn.setStyleSheet("background:transparent;border:0;padding:0;")
                thumb_btn.setFixedSize(140, 140)
                from component.thumbnail.thumbnail_util import get_no_thumbnail_image, pil_image_to_qpixmap
                thumb_btn.setIcon(QIcon(pil_image_to_qpixmap(get_no_thumbnail_image((140, 140)))))
                thumb_btn.setIconSize(QSize(140, 140))
                thumb_btn.setStyleSheet("background:transparent;border:2px solid #ff4444;border-radius:10px;")
                fname = os.path.basename(f)
                maxlen = 18
                fname_disp = fname[:8] + '...' + fname[-7:] if len(fname) > maxlen else fname
                name_label = QLabel(fname_disp)
                name_label.setStyleSheet("font-size:12px;color:#ff4444;font-weight:bold;")
                size_label = QLabel("取得中...")
                size_label.setStyleSheet("font-size:11px;color:#00ff99;")
                path_label = QLabel(f)
                path_label.setStyleSheet("font-size:10px;color:#00ff99;max-width:140px;")
                path_label.setWordWrap(True)
                path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
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
                thumb_btn_map[f] = thumb_btn
                size_label_map[f] = size_label
                if (f not in cache_dict) or (f not in video_info_cache):
                    thread = QThread()
                    worker = ThumbInfoWorker(f, cache_dict, video_info_cache, (140, 140), get_thumbnail_for_file)
                    worker.moveToThread(thread)
                    def on_finished(path, pil_thumb, info_tuple, btn=thumb_btn, label=size_label, thread=thread, worker=worker):
                        if pil_thumb is not None:
                            btn.setIcon(QIcon(pil_image_to_qpixmap(pil_thumb)))
                        label.setText(f"{info_tuple[0]}{info_tuple[1]}")
                        thread.quit()
                        thread.wait()
                        thread.deleteLater()
                        worker.deleteLater()
                    worker.finished.connect(on_finished)
                    thread.started.connect(worker.run)
                    thread.start()
                    threads.append(thread)
                else:
                    pil_thumb = cache_dict[f]
                    btn = thumb_btn_map[f]
                    btn.setIcon(QIcon(pil_image_to_qpixmap(pil_thumb)))
                    size_str, duration_str = video_info_cache[f]
                    size_label_map[f].setText(f"{size_str}{duration_str}")
            n_items = len(group)
            if n_items % max_col != 0:
                last_row = n_items // max_col
                for col in range(n_items % max_col, max_col):
                    spacer = QWidget()
                    spacer.setFixedWidth(140)
                    grid.addWidget(spacer, last_row, col)
            group_box.setLayout(grid)
            vbox.addWidget(group_box)
        # ページ送りボタン
        nav_hbox = QHBoxLayout()
        prev_btn = QPushButton("前のページ")
        next_btn = QPushButton("次のページ")
        page_label = QLabel(f"{current_page[0]+1} / {total_pages}")
        prev_btn.setEnabled(current_page[0] > 0)
        next_btn.setEnabled(current_page[0] < total_pages-1)
        def goto_prev():
            if current_page[0] > 0:
                current_page[0] -= 1
                update_page(groups, group_checkboxes, delete_cb)
        def goto_next():
            if current_page[0] < total_pages-1:
                current_page[0] += 1
                update_page(groups, group_checkboxes, delete_cb)
        prev_btn.clicked.connect(goto_prev)
        next_btn.clicked.connect(goto_next)
        nav_hbox.addWidget(prev_btn)
        nav_hbox.addWidget(page_label)
        nav_hbox.addWidget(next_btn)
        vbox.addLayout(nav_hbox)
        # 閉じるボタン
        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(dlg.reject)
        vbox.addWidget(btns)
        dlg.setLayout(vbox)
    # --- 不要な重複・誤ったスコープのコードを削除 ---
    # ダイアログ終了時に全スレッドを安全に停止
    def _cleanup_threads():
        for t in threads:
            if t.isRunning():
                t.quit()
                t.wait()
    dlg.finished.connect(_cleanup_threads)
    # ページング用のチェックボックスリストとdelete_cbを初期化
    group_checkboxes = []
    delete_cb = None  # 必要に応じて外部から渡す場合は引数で受け取る
    update_page(broken_groups, group_checkboxes, delete_cb)
    dlg.exec_()

def create_error_group_ui(error_files, get_thumbnail_for_file, detail_cb, delete_cb, thumb_cache=None, defer_queue=None, thumb_widget_map=None):
    group_box = QGroupBox("サムネイル生成エラー/壊れファイル")
    grid = QGridLayout()
    grid.setHorizontalSpacing(12)
    grid.setVerticalSpacing(16)
    max_col = 4
    if isinstance(thumb_cache, ThumbnailCache):
        cache_dict = thumb_cache.cache
    elif isinstance(thumb_cache, dict):
        cache_dict = thumb_cache
    else:
        cache_dict = {}
    video_info_cache = {}
    thumb_btn_map = {}
    size_label_map = {}
    threads = []  # QThread参照保持用
    # cache_dictは上で定義済み
    for idx, f in enumerate(error_files):
        # cache_dictは関数先頭で定義済み
        thumb_btn = QPushButton()
        thumb_btn.setStyleSheet("background:transparent;border:0;padding:0;")
        thumb_btn.setFixedSize(140, 140)
        thumb_btn.setIconSize(QSize(140, 140))
        from component.thumbnail.thumbnail_util import get_no_thumbnail_image, pil_image_to_qpixmap
        thumb_btn.setIcon(QIcon(pil_image_to_qpixmap(get_no_thumbnail_image((140, 140)))))
        thumb_btn.setStyleSheet("background:transparent;border:2px solid #ff4444;color:#ff4444;font-size:15px;border-radius:10px;")
        thumb_btn.clicked.connect(lambda _, path=f: detail_cb(path))
        if thumb_widget_map is not None:
            import os
            norm_path = os.path.abspath(os.path.normpath(f))
            thumb_widget_map[norm_path] = thumb_btn
        fname = os.path.basename(f)
        name_label = QLabel(fname)
        name_label.setStyleSheet("font-size:12px;color:#ff4444;font-weight:bold;max-width:140px;")
        name_label.setWordWrap(True)
        folder_path = os.path.dirname(f)
        folder_parts = folder_path.replace("\\", "/").rstrip("/").split("/")
        if len(folder_parts) >= 2:
            last2 = "/".join(folder_parts[-2:])
        elif len(folder_parts) == 1:
            last2 = folder_parts[0]
        else:
            last2 = folder_path
        path_label = QLabel(last2)
        path_label.setStyleSheet("font-size:10px;color:#ffb300;max-width:140px;")
        path_label.setMaximumWidth(140)
        path_label.setWordWrap(True)
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        open_folder_btn = QPushButton("フォルダを開く")
        open_folder_btn.setStyleSheet("font-size:11px;color:#ffb300;border:1px solid #ffb300;border-radius:6px;")
        def open_folder(path):
            import os, subprocess, sys
            folder = os.path.dirname(path)
            if os.path.exists(folder):
                if sys.platform.startswith('win'):
                    os.startfile(folder)
                elif sys.platform.startswith('darwin'):
                    subprocess.Popen(['open', folder])
                else:
                    subprocess.Popen(['xdg-open', folder])
        open_folder_btn.clicked.connect(lambda _, path=f: open_folder(path))
        del_btn = QPushButton("削除")
        del_btn.setStyleSheet("font-size:12px;color:#ff00c8;max-width:140px;")
        del_btn.setFixedWidth(80)
        del_btn.clicked.connect(lambda _, path=f: delete_cb(path))
        btn_hbox = QHBoxLayout()
        btn_hbox.setSpacing(8)
        btn_hbox.setContentsMargins(0, 0, 0, 0)
        open_folder_btn.setFixedWidth(80)
        btn_hbox.addWidget(open_folder_btn)
        btn_hbox.addWidget(del_btn)
        info_vbox = QVBoxLayout()
        info_vbox.setSpacing(4)
        info_vbox.setContentsMargins(0, 0, 0, 0)
        info_vbox.addWidget(name_label)
        info_vbox.addWidget(path_label)
        info_vbox.addLayout(btn_hbox)
        info_widget = QWidget()
        info_widget.setLayout(info_vbox)
        info_widget.setFixedWidth(140)
        hbox = QHBoxLayout()
        hbox.setSpacing(0)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.addWidget(thumb_btn)
        hbox.addWidget(info_widget)
        file_widget = QWidget()
        file_widget.setLayout(hbox)
        row = idx // max_col
        col = idx % max_col
        grid.addWidget(file_widget, row, col)
        thumb_btn_map[f] = thumb_btn
        size_label_map[f] = name_label  # name_labelをsize_label_mapに登録（エラー用）
        if (f not in cache_dict) or (f not in video_info_cache):
            thread = QThread()
            worker = ThumbInfoWorker(f, cache_dict, video_info_cache, (140, 140), get_thumbnail_for_file)
            worker.moveToThread(thread)
            def on_finished(path, pil_thumb, info_tuple, btn=thumb_btn, label=name_label, thread=thread, worker=worker):
                if pil_thumb is not None:
                    btn.setIcon(QIcon(pil_image_to_qpixmap(pil_thumb)))
                label.setText(f"{info_tuple[0]}{info_tuple[1]}")
                thread.quit()
                thread.wait()
                thread.deleteLater()
                worker.deleteLater()
            worker.finished.connect(on_finished)
            thread.started.connect(worker.run)
            thread.start()
            threads.append(thread)
        else:
            pil_thumb = cache_dict[f]
            btn = thumb_btn_map[f]
            btn.setIcon(QIcon(pil_image_to_qpixmap(pil_thumb)))
            size_str, duration_str = video_info_cache[f]
            name_label.setText(f"{size_str}{duration_str}")
    group_box.setLayout(grid)
    # group_boxの親がQDialogなら、閉じるときにスレッドを止める
    return group_box

class ThumbInfoWorker(QObject):
    finished = pyqtSignal(str, object, object)  # path, pil_thumb, (size_str, duration_str)
    def __init__(self, path, thumb_cache, video_info_cache, thumb_size, get_thumbnail_for_file):
        super().__init__()
        self.path = path
        self.thumb_cache = thumb_cache
        self.video_info_cache = video_info_cache
        self.thumb_size = thumb_size
        self.get_thumbnail_for_file = get_thumbnail_for_file
    def run(self):
        if isinstance(self.thumb_cache, ThumbnailCache):
            cache_dict = self.thumb_cache.cache
        elif isinstance(self.thumb_cache, dict):
            cache_dict = self.thumb_cache
        else:
            cache_dict = {}
        pil_thumb = cache_dict.get(self.path)
        if pil_thumb is None:
            pil_thumb = self.get_thumbnail_for_file(self.path, self.thumb_size, cache=self.thumb_cache)
            cache_dict[self.path] = pil_thumb
        if self.path in self.video_info_cache:
            size_str, duration_str = self.video_info_cache[self.path]
        else:
            try:
                size = os.path.getsize(self.path)
                size_mb = size / 1024 / 1024
                size_str = f"{size_mb:.2f} MB"
            except Exception:
                size_str = "-"
            ext = os.path.splitext(self.path)[1].lower()
            video_exts = ('.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm', '.mpg', '.mpeg', '.3gp')
            duration_str = ""
            if ext in video_exts:
                try:
                    import cv2
                    cap = cv2.VideoCapture(self.path)
                    if cap.isOpened():
                        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
                        fps = cap.get(cv2.CAP_PROP_FPS)
                        if fps > 0:
                            seconds = int(frames / fps)
                            m, s = divmod(seconds, 60)
                            h, m = divmod(m, 60)
                            if h > 0:
                                duration_str = f" / {h}:{m:02d}:{s:02d}"
                            else:
                                duration_str = f" / {m}:{s:02d}"
                        else:
                            duration_str = ""
                    cap.release()
                except Exception:
                    duration_str = ""
            self.video_info_cache[self.path] = (size_str, duration_str)
        self.finished.emit(self.path, pil_thumb, (size_str, duration_str))
