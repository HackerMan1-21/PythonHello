# --- 2. 並列処理の最適化: CPUコア数に応じてmax_workersを自動設定 ---
import concurrent.futures
import multiprocessing

def get_optimal_workers():
    try:
        return max(2, min(16, multiprocessing.cpu_count()))
    except Exception:
        return 4

def group_by_phash_parallel(files, phash_func, chunksize=32):
    max_workers = get_optimal_workers()
    # 並列化対象関数はトップレベルで定義すること
    def _phash_worker(filelist):
        return [(f, phash_func(f)) for f in filelist]
    # チャンク分割
    chunks = [files[i:i+chunksize] for i in range(0, len(files), chunksize)]
    results = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
        futs = [executor.submit(_phash_worker, chunk) for chunk in chunks]
        for fut in concurrent.futures.as_completed(futs):
            try:
                results.extend(fut.result())
            except Exception as e:
                print(f"[WARN] 並列pHash失敗: {e}")
    return results
import atexit
from PyQt5.QtWidgets import QApplication
from component.thumbnail.thumbnail_util import ThumbnailCache
def _save_thumb_cache_on_exit():
    try:
        app = QApplication.instance()
        if hasattr(app, 'thumb_cache'):
            thumb_cache = app.thumb_cache
            if isinstance(thumb_cache, ThumbnailCache):
                print(f"[DEBUG] キャッシュ保存: {thumb_cache.cache_file}")
                thumb_cache.save_to_disk()
    except Exception as e:
        print(f"[WARN] サムネイルキャッシュ保存失敗: {e}")

def _load_thumb_cache_on_start():
    try:
        app = QApplication.instance()
        if app is not None:
            if not hasattr(app, 'thumb_cache'):
                thumb_cache = ThumbnailCache()
                app.thumb_cache = thumb_cache
                print(f"[DEBUG] キャッシュロード: {thumb_cache.cache_file}")
    except Exception as e:
        print(f"[WARN] サムネイルキャッシュロード失敗: {e}")

_load_thumb_cache_on_start()
app = QApplication.instance()
if app is not None:
    app.aboutToQuit.connect(_save_thumb_cache_on_exit)
    try:
        from PyQt5.QtCore import QTimer
        def _periodic_thumb_cache_save():
            try:
                if hasattr(app, 'thumb_cache'):
                    thumb_cache = app.thumb_cache
                    if isinstance(thumb_cache, ThumbnailCache):
                        thumb_cache.save_to_disk()
            except Exception as e:
                print(f"[WARN] サムネイルキャッシュ定期保存失敗: {e}")
        timer = QTimer()
        timer.setInterval(60000)
        timer.timeout.connect(_periodic_thumb_cache_save)
        timer.start()
        app._thumb_cache_autosave_timer = timer
    except Exception as e:
        print(f"[WARN] サムネイルキャッシュ自動保存タイマー起動失敗: {e}")
atexit.register(_save_thumb_cache_on_exit)
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
from PyQt5.QtWidgets import QGroupBox, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QWidget, QCheckBox, QDialog, QDialogButtonBox, QMessageBox, QFileDialog, QGridLayout, QProgressDialog
from PyQt5.QtCore import QSize, Qt
try:
    from PyQt5.QtCore import Qt as _Qt
    _WINDOW_MODAL = _Qt.WindowModality.WindowModal
except Exception:
    _WINDOW_MODAL = None  # fallback to None if WindowModal is not available
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
    from PyQt5.QtWidgets import QScrollArea
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
    workers = []  # Worker参照も保持
    group_box.threads = threads  # QGroupBoxの属性として保持
    group_box.workers = workers
    # cache_dictは上で定義済み
    thumb_btns = []
    for idx, f in enumerate(group):
        norm_path = os.path.abspath(os.path.normpath(f))
        thumb_btn = QPushButton()
        thumb_btn.setStyleSheet("background:transparent;border:0;padding:0;")
        thumb_btn.setFixedSize(140, 140)
        from component.thumbnail.thumbnail_util import get_no_thumbnail_image, pil_image_to_qpixmap
        thumb_btn.setIcon(QIcon(pil_image_to_qpixmap(get_no_thumbnail_image((140, 140)))));
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
            from PyQt5.QtWidgets import QMessageBox
            folder = path
            if not os.path.exists(folder):
                folder = os.path.dirname(path)
            if not os.path.exists(folder):
                QMessageBox.warning(None, "フォルダを開く", f"パスが存在しません: {folder}")
                return
            if os.path.isdir(folder):
                if sys.platform.startswith('win'):
                    os.startfile(folder)
                elif sys.platform.startswith('darwin'):
                    subprocess.Popen(['open', folder])
                else:
                    subprocess.Popen(['xdg-open', folder])
            else:
                parent_folder = os.path.dirname(folder)
                if os.path.exists(parent_folder):
                    if sys.platform.startswith('win'):
                        os.startfile(parent_folder)
                    elif sys.platform.startswith('darwin'):
                        subprocess.Popen(['open', parent_folder])
                    else:
                        subprocess.Popen(['xdg-open', parent_folder])
                else:
                    QMessageBox.warning(None, "フォルダを開く", f"フォルダが存在しません: {parent_folder}")
        open_folder_btn.clicked.connect(lambda _, path=f: open_folder(path))
        del_btn = QPushButton("削除")
        del_btn.setStyleSheet("font-size:12px;color:#ff00c8;max-width:140px;")
        del_btn.setFixedWidth(140)
        def delete_and_update(path):
            try:
                delete_cb(path)
                norm_path = os.path.abspath(os.path.normpath(path))
                if norm_path in cache_dict:
                    del cache_dict[norm_path]
                if norm_path in video_info_cache:
                    del video_info_cache[norm_path]
                if path in group:
                    group.remove(path)
                # 削除時にQThread/Workerを安全に停止・破棄
                for t in getattr(group_box, 'threads', []):
                    if t.isRunning():
                        t.quit()
                for t in getattr(group_box, 'threads', []):
                    t.wait()
                    t.deleteLater()
                for w in getattr(group_box, 'workers', []):
                    w.deleteLater()
                if len(group) <= 1:
                    parent_widget = group_box.parentWidget()
                    parent_layout = getattr(parent_widget, 'layout', None)
                    layout_obj = parent_layout() if callable(parent_layout) else None
                    from PyQt5.QtWidgets import QLayout
                    if isinstance(layout_obj, QLayout):
                        layout_obj.removeWidget(group_box)
                    group_box.deleteLater()
                else:
                    from PyQt5.QtCore import QTimer
                    QTimer.singleShot(100, lambda: group_box.update())
            except Exception as e:
                print(f"[ERROR] 削除失敗: {e}")
        del_btn.clicked.connect(lambda _, path=f: delete_and_update(path))
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
        thumb_btn_map[norm_path] = thumb_btn
        size_label_map[norm_path] = size_label
        thumb_btns.append((thumb_btn, norm_path))
    n_items = len(group)
    if n_items % max_col != 0:
        last_row = n_items // max_col
        for col in range(n_items % max_col, max_col):
            spacer = QWidget()
            spacer.setFixedWidth(140)
            grid.addWidget(spacer, last_row, col)
    remain_label = QLabel(f"残り: {len(group)}ファイル")
    remain_label.setStyleSheet("font-size:12px;color:#00ffe7;font-weight:bold;margin-top:4px;")
    hide_btn = QPushButton("グループを非表示")
    hide_btn.setStyleSheet("font-size:12px;color:#888;background:#222;border-radius:8px;margin-left:12px;")
    def hide_group_box():
        # キャッシュ削除
        for f in group:
            norm_path = os.path.abspath(os.path.normpath(f))
            if norm_path in cache_dict:
                del cache_dict[norm_path]
            if norm_path in video_info_cache:
                del video_info_cache[norm_path]
        parent_widget = group_box.parentWidget()
        parent_layout = getattr(parent_widget, 'layout', None)
        layout_obj = parent_layout() if callable(parent_layout) else None
        from PyQt5.QtWidgets import QLayout
        if isinstance(layout_obj, QLayout):
            layout_obj.removeWidget(group_box)
        group_box.deleteLater()
    hide_btn.clicked.connect(hide_group_box)
    top_hbox = QHBoxLayout()
    top_hbox.addWidget(remain_label)
    top_hbox.addWidget(hide_btn)
    top_hbox.addStretch(1)
    vbox = QVBoxLayout()
    vbox.addLayout(top_hbox)
    vbox.addLayout(grid)
    grid_widget = QWidget()
    grid_widget.setLayout(vbox)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(grid_widget)
    scroll.setMinimumHeight(600)
    scroll.setMinimumWidth(600)
    # 遅延サムネイル生成用: スクロール時に表示範囲のボタンだけ生成
    def update_visible_thumbnails():
        viewport = scroll.viewport()
        rect = viewport.rect()
        for btn, norm_path in thumb_btns:
            btn_pos = btn.mapTo(viewport, btn.rect().topLeft())
            btn_rect = btn.rect()
            btn_rect.moveTopLeft(btn_pos)
            if rect.intersects(btn_rect):
                if (norm_path not in cache_dict) and (norm_path not in video_info_cache):
                    thread = QThread()
                    worker = ThumbInfoWorker(norm_path, cache_dict, video_info_cache, (140, 140), get_thumbnail_for_file)
                    worker.moveToThread(thread)
                    def on_finished(path, pil_thumb, info_tuple, btn=btn, thread=thread, worker=worker):
                        from PyQt5.QtCore import QTimer
                        def update_ui():
                            try:
                                from PyQt5 import sip
                                from component.thumbnail.thumbnail_util import pil_image_to_qpixmap, get_no_thumbnail_image
                                norm_path2 = os.path.abspath(os.path.normpath(path))
                                label = size_label_map.get(norm_path2)
                                if label is not None and (sip.isdeleted(label) or sip.isdeleted(btn)):
                                    return
                                cache_dict[norm_path2] = pil_thumb
                                video_info_cache[norm_path2] = info_tuple
                                if pil_thumb is not None:
                                    btn.setIcon(QIcon(pil_image_to_qpixmap(pil_thumb)))
                                else:
                                    btn.setIcon(QIcon(pil_image_to_qpixmap(get_no_thumbnail_image((140, 140)))))
                                if label is not None:
                                    label.setText(f"{info_tuple[0]}{info_tuple[1]}")
                            except Exception as e:
                                print(f"[WARN] on_finished UI更新失敗: {e}")
                            thread.quit()
                            worker.deleteLater()
                        QTimer.singleShot(0, update_ui)
                    worker.finished.connect(on_finished)
                    thread.started.connect(worker.run)
                    thread.start()
                    group_box.threads.append(thread)
                    group_box.workers.append(worker)
                else:
                    # 生成済みの場合はキャッシュ内容を反映
                    pil_thumb = cache_dict.get(norm_path)
                    info_tuple = video_info_cache.get(norm_path, ("", ""))
                    label = size_label_map.get(norm_path)
                    if pil_thumb is not None:
                        btn.setIcon(QIcon(pil_image_to_qpixmap(pil_thumb)))
                    else:
                        btn.setIcon(QIcon(pil_image_to_qpixmap(get_no_thumbnail_image((140, 140)))))
                    if label is not None:
                        label.setText(f"{info_tuple[0]}{info_tuple[1]}")
    scroll.verticalScrollBar().valueChanged.connect(lambda _: update_visible_thumbnails())
    scroll.horizontalScrollBar().valueChanged.connect(lambda _: update_visible_thumbnails())
    # 初回表示時にもサムネイル生成
    QTimer.singleShot(100, update_visible_thumbnails)
    # group_boxのレイアウトにscrollを追加
    layout = QVBoxLayout()
    layout.addWidget(scroll)
    group_box.setLayout(layout)
    parent_dialog = parent
    if parent_dialog is not None and hasattr(parent_dialog, 'finished'):
        def _cleanup_threads():
            for t in group_box.threads:
                if t.isRunning():
                    t.quit()
            for t in group_box.threads:
                t.wait()
                t.deleteLater()
            for w in getattr(group_box, 'workers', []):
                w.deleteLater()
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
    workers = []  # Worker参照も保持
    dlg.threads = threads  # ダイアログの属性として保持
    dlg.workers = workers
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
        # 不要な try: を削除
            group_box = QGroupBox(f"顔グループ（残り: {len(group)}ファイル）")
            grid = QGridLayout()
            def delete_and_update_face(path):
                try:
                    if delete_cb:
                        delete_cb(path)
                    else:
                        if os.path.exists(path):
                            os.remove(path)
                    if path in group:
                        group.remove(path)
                    if len(group) <= 1:
                        parent_widget = group_box.parentWidget()
                        parent_layout = getattr(parent_widget, 'layout', None)
                        layout_obj = parent_layout() if callable(parent_layout) else None
                        from PyQt5.QtWidgets import QLayout
                        if isinstance(layout_obj, QLayout):
                            layout_obj.removeWidget(group_box)
                        group_box.deleteLater()
                    else:
                        from PyQt5.QtCore import QTimer
                        QTimer.singleShot(100, lambda: group_box.update())
                except Exception as e:
                    print(f"[ERROR] 顔グループ削除失敗: {e}")
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
                del_btn.clicked.connect(lambda _, path=f: delete_and_update_face(path))
                # サムネイル取得の非同期処理（on_finishedのexceptインデント修正）
                if (f not in cache_dict) or (f not in video_info_cache):
                    thread = QThread()
                    worker = ThumbInfoWorker(f, thumb_cache, video_info_cache, (140, 140), get_thumbnail_for_file)
                    worker.moveToThread(thread)
                    def on_finished(path, pil_thumb, info_tuple, btn=thumb_btn, label=size_label):
                        try:
                            from PyQt5 import sip
                            from component.thumbnail.thumbnail_util import pil_image_to_qpixmap, get_no_thumbnail_image
                            if sip.isdeleted(label) or sip.isdeleted(btn):
                                return
                            if pil_thumb is not None:
                                btn.setIcon(QIcon(pil_image_to_qpixmap(pil_thumb)))
                            else:
                                btn.setIcon(QIcon(pil_image_to_qpixmap(get_no_thumbnail_image((140, 140)))))
                            label.setText(f"{info_tuple[0]}{info_tuple[1]}")
                        except Exception as e:
                            print(f"[WARN] on_finished UI更新失敗: {e}")
                        thread.quit()
                        worker.deleteLater()
                    worker.finished.connect(on_finished)
                    thread.started.connect(worker.run)
                    thread.start()
                    threads.append(thread)
                    workers.append(worker)
            else:
                pil_thumb = cache_dict[f]
                btn = thumb_btn_map[f]
                btn.setIcon(QIcon(pil_image_to_qpixmap(pil_thumb)))
                size_str, duration_str = video_info_cache[f]
                size_label_map[f].setText(f"{size_str}{duration_str}")
        # 顔グループUIにも「グループを非表示」ボタンを右横に追加
        remain_label = QLabel(f"残り: {len(group)}ファイル")
        remain_label.setStyleSheet("font-size:12px;color:#00ff99;font-weight:bold;margin-top:4px;")
        hide_btn = QPushButton("グループを非表示")
        hide_btn.setStyleSheet("font-size:12px;color:#888;background:#222;border-radius:8px;margin-left:12px;")
        def hide_group_box():
            parent_widget = group_box.parentWidget()
            parent_layout = getattr(parent_widget, 'layout', None)
            layout_obj = parent_layout() if callable(parent_layout) else None
            from PyQt5.QtWidgets import QLayout
            if isinstance(layout_obj, QLayout):
                layout_obj.removeWidget(group_box)
            group_box.deleteLater()
        hide_btn.clicked.connect(hide_group_box)
        top_hbox = QHBoxLayout()
        top_hbox.addWidget(remain_label)
        top_hbox.addWidget(hide_btn)
        top_hbox.addStretch(1)
        vbox.addLayout(top_hbox)
        vbox.addLayout(grid)
        group_box.setLayout(vbox)
    move_btn = QPushButton("選択したファイルをフォルダに移動")
    move_btn.clicked.connect(lambda: move_selected_files_to_folder_func(group_checkboxes, dlg))
    vbox.addWidget(move_btn)
    btns = QDialogButtonBox(QDialogButtonBox.Close)
    btns.rejected.connect(dlg.reject)
    vbox.addWidget(btns)
    dlg.setLayout(vbox)
    def _cleanup_threads():
        # すべてのスレッドにquitを投げる
        for t in dlg.threads:
            if t.isRunning():
                t.quit()
        # すべてのスレッドが終了するまでwait
        for t in dlg.threads:
            t.wait()
            t.deleteLater()
        # ワーカーもdeleteLater
        for w in getattr(dlg, 'workers', []):
            w.deleteLater()
    dlg.finished.connect(_cleanup_threads)
    dlg.exec_()
    # 念のためダイアログ終了後も全スレッドwait
    for t in dlg.threads:
        t.wait()

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
    if thumb_cache is None:
        thumb_cache = {}
    video_info_cache = {}
    thumb_btn_map = {}
    size_label_map = {}
    threads = []  # QThread参照保持用
    workers = []  # Worker参照も保持
    dlg.threads = threads  # ダイアログの属性として保持
    dlg.workers = workers
    from PyQt5.QtWidgets import QScrollArea
    thumb_btns = []
    max_col = 4
    for group in broken_groups:
        group_box = QGroupBox(f"壊れ動画グループ（残り: {len(group)}ファイル）")
        grid = QGridLayout()
        for idx, f in enumerate(group):
            thumb_btn = QPushButton()
            thumb_btn.setStyleSheet("background:transparent;border:0;padding:0;")
            thumb_btn.setFixedSize(140, 140)
            from component.thumbnail.thumbnail_util import get_no_thumbnail_image, pil_image_to_qpixmap
            thumb_btn.setIcon(QIcon(pil_image_to_qpixmap(get_no_thumbnail_image((140, 140)))));
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
            def delete_and_update_broken(path):
                try:
                    if os.path.exists(path):
                        os.remove(path)
                    norm_path = os.path.abspath(os.path.normpath(path))
                    if norm_path in cache_dict:
                        del cache_dict[norm_path]
                    if norm_path in video_info_cache:
                        del video_info_cache[norm_path]
                    if path in group:
                        group.remove(path)
                    if len(group) <= 1:
                        parent_widget = group_box.parentWidget()
                        parent_layout = getattr(parent_widget, 'layout', None)
                        layout_obj = parent_layout() if callable(parent_layout) else None
                        from PyQt5.QtWidgets import QLayout
                        if isinstance(layout_obj, QLayout):
                            layout_obj.removeWidget(group_box)
                        group_box.deleteLater()
                    else:
                        from PyQt5.QtCore import QTimer
                        QTimer.singleShot(100, lambda: group_box.update())
                except Exception as e:
                    print(f"[ERROR] 壊れ動画削除失敗: {e}")
            del_btn.clicked.connect(lambda _, path=f: delete_and_update_broken(path))
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
            thumb_btns.append((thumb_btn, f))
        n_items = len(group)
        if n_items % max_col != 0:
            last_row = n_items // max_col
            for col in range(n_items % max_col, max_col):
                spacer = QWidget()
                spacer.setFixedWidth(140)
                grid.addWidget(spacer, last_row, col)
        remain_label = QLabel(f"残り: {len(group)}ファイル")
        remain_label.setStyleSheet("font-size:12px;color:#ff4444;font-weight:bold;margin-top:4px;")
        hide_btn = QPushButton("グループを非表示")
        hide_btn.setStyleSheet("font-size:12px;color:#888;background:#222;border-radius:8px;margin-left:12px;")
        def hide_group_box():
            # キャッシュ削除
            for f in group:
                norm_path = os.path.abspath(os.path.normpath(f))
                if norm_path in cache_dict:
                    del cache_dict[norm_path]
                if norm_path in video_info_cache:
                    del video_info_cache[norm_path]
            parent_widget = group_box.parentWidget()
            parent_layout = getattr(parent_widget, 'layout', None)
            layout_obj = parent_layout() if callable(parent_layout) else None
            from PyQt5.QtWidgets import QLayout
            if isinstance(layout_obj, QLayout):
                layout_obj.removeWidget(group_box)
            group_box.deleteLater()
        hide_btn.clicked.connect(hide_group_box)
        top_hbox = QHBoxLayout()
        top_hbox.addWidget(remain_label)
        top_hbox.addWidget(hide_btn)
        top_hbox.addStretch(1)
        vbox.addLayout(top_hbox)
        vbox.addLayout(grid)
    grid_widget = QWidget()
    grid_widget.setLayout(vbox)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(grid_widget)
    scroll.setMinimumHeight(600)
    scroll.setMinimumWidth(600)
    def update_visible_thumbnails():
        viewport = scroll.viewport()
        rect = viewport.rect()
        for btn, f in thumb_btns:
            btn_pos = btn.mapTo(viewport, btn.rect().topLeft())
            btn_rect = btn.geometry()
            btn_rect.moveTopLeft(btn_pos)
            if rect.intersects(btn_rect):
                if (f not in cache_dict) or (f not in video_info_cache):
                    thread = QThread()
                    worker = ThumbInfoWorker(f, cache_dict, video_info_cache, (140, 140), get_thumbnail_for_file)
                    worker.moveToThread(thread)
                    def on_finished(path, pil_thumb, info_tuple, btn=btn, label=size_label_map.get(path, None), thread=thread, worker=worker):
                        from PyQt5.QtCore import QTimer
                        def update_ui():
                            try:
                                from PyQt5 import sip
                                from component.thumbnail.thumbnail_util import pil_image_to_qpixmap, get_no_thumbnail_image
                                norm_path2 = os.path.abspath(os.path.normpath(path))
                                if label is not None and (sip.isdeleted(label) or sip.isdeleted(btn)):
                                    return
                                cache_dict[norm_path2] = pil_thumb
                                video_info_cache[norm_path2] = info_tuple
                                if pil_thumb is not None:
                                    btn.setIcon(QIcon(pil_image_to_qpixmap(pil_thumb)))
                                else:
                                    btn.setIcon(QIcon(pil_image_to_qpixmap(get_no_thumbnail_image((140, 140)))));
                                if label is not None:
                                    label.setText(f"{info_tuple[0]}{info_tuple[1]}")
                            except Exception as e:
                                print(f"[WARN] on_finished UI更新失敗: {e}")
                            thread.quit()
                            worker.deleteLater()
                        QTimer.singleShot(0, update_ui)
                    worker.finished.connect(on_finished)
                    thread.started.connect(worker.run)
                    thread.start()
            else:
                pass
    scroll.verticalScrollBar().valueChanged.connect(lambda _: update_visible_thumbnails())
    scroll.horizontalScrollBar().valueChanged.connect(lambda _: update_visible_thumbnails())
    QTimer.singleShot(100, update_visible_thumbnails)
    layout = QVBoxLayout()
    layout.addWidget(scroll)
    dlg.setLayout(layout)
    def _cleanup_threads():
        for t in dlg.threads:
            if t.isRunning():
                t.quit()
        for t in dlg.threads:
            t.wait()
            t.deleteLater()
        for w in getattr(dlg, 'workers', []):
            w.deleteLater()
    dlg.finished.connect(_cleanup_threads)
    dlg.exec_()
    for t in dlg.threads:
        t.wait()

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
        def delete_and_update_error(path):
            try:
                delete_cb(path)
                if path in error_files:
                    error_files.remove(path)
                norm_path = os.path.abspath(os.path.normpath(path))
                # キャッシュ削除
                if norm_path in cache_dict:
                    del cache_dict[norm_path]
                if norm_path in video_info_cache:
                    del video_info_cache[norm_path]
                # グループ自体が1件になった場合のみ group_box を削除
                if len(error_files) <= 1:
                    parent_widget = group_box.parentWidget()
                    parent_layout = getattr(parent_widget, 'layout', None)
                    layout_obj = parent_layout() if callable(parent_layout) else None
                    from PyQt5.QtWidgets import QLayout
                    if isinstance(layout_obj, QLayout):
                        layout_obj.removeWidget(group_box)
                    group_box.deleteLater()
                # ページングUI未実装だが、複数削除時はQTimer.singleShot(100ms)で再描画遅延
                else:
                    from PyQt5.QtCore import QTimer
                    QTimer.singleShot(100, lambda: group_box.update())
            except Exception as e:
                print(f"[ERROR] エラーグループ削除失敗: {e}")
        del_btn.clicked.connect(lambda _, path=f: delete_and_update_error(path))
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
            def on_finished(path, pil_thumb, info_tuple, btn=thumb_btn, label=name_label):
                if pil_thumb is not None:
                    btn.setIcon(QIcon(pil_image_to_qpixmap(pil_thumb)))
                label.setText(f"{info_tuple[0]}{info_tuple[1]}")
                thread.quit()
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
    # エラーグループUIにも「グループを非表示」ボタンを右横に追加
    remain_label = QLabel(f"残り: {len(error_files)}ファイル")
    remain_label.setStyleSheet("font-size:12px;color:#ff4444;font-weight:bold;margin-top:4px;")
    hide_btn = QPushButton("グループを非表示")
    hide_btn.setStyleSheet("font-size:12px;color:#888;background:#222;border-radius:8px;margin-left:12px;")
    def hide_group_box():
        parent_widget = group_box.parentWidget()
        parent_layout = getattr(parent_widget, 'layout', None)
        layout_obj = parent_layout() if callable(parent_layout) else None
        if layout_obj:
            layout_obj.removeWidget(group_box)
        group_box.deleteLater()
    hide_btn.clicked.connect(hide_group_box)
    top_hbox = QHBoxLayout()
    top_hbox.addWidget(remain_label)
    top_hbox.addWidget(hide_btn)
    top_hbox.addStretch(1)
    vbox = QVBoxLayout()
    vbox.addLayout(top_hbox)
    vbox.addLayout(grid)
    group_box.setLayout(vbox)
    return group_box

class ThumbInfoWorker(QObject):
    finished = pyqtSignal(str, object, object)  # path, pil_thumb, (size_str, duration_str)

    def __init__(self, path, thumb_cache, video_info_cache, thumb_size, get_thumbnail_for_file):
        super().__init__()
        import os
        self.path = os.path.abspath(os.path.normpath(path))  # パスを正規化
        self.thumb_cache = thumb_cache
        self.video_info_cache = video_info_cache
        self.thumb_size = thumb_size
        self.get_thumbnail_for_file = get_thumbnail_for_file
        self._canceled = False

    def cancel(self):
        self._canceled = True

    def run(self):
        # すべての処理でキャンセルチェック
        if self._canceled:
            print(f"[DEBUG] ThumbInfoWorker: キャンセル検知 {self.path}")
            return
        # キャッシュ取得（正規化キーで統一）
        if isinstance(self.thumb_cache, ThumbnailCache):
            cache_dict = self.thumb_cache.cache
        elif isinstance(self.thumb_cache, dict):
            cache_dict = self.thumb_cache
        else:
            cache_dict = {}
        norm_path = os.path.abspath(os.path.normpath(self.path))
        pil_thumb = cache_dict.get(norm_path)
        info_tuple = ("", "")
        # サムネイル未取得なら生成
        if pil_thumb is None:
            try:
                if self._canceled:
                    print(f"[DEBUG] ThumbInfoWorker: キャンセル検知(生成前) {norm_path}")
                    return
                result = self.get_thumbnail_for_file(norm_path, self.thumb_size)
                # get_thumbnail_for_file の返り値がタプルかどうか判定
                if isinstance(result, tuple) and len(result) == 2:
                    pil_thumb, info_tuple = result
                else:
                    pil_thumb = result
                    info_tuple = ("", "")
                if self._canceled:
                    print(f"[DEBUG] ThumbInfoWorker: キャンセル検知(生成後) {norm_path}")
                    return
                # 失敗時は None
                if pil_thumb is not None:
                    cache_dict[norm_path] = pil_thumb
                # info_tuple もキャッシュ（動画情報など）
                self.video_info_cache[norm_path] = info_tuple
                print(f"[DEBUG] ThumbInfoWorker: サムネイル生成完了 {norm_path} {info_tuple}")
            except Exception as e:
                print(f"[ERROR] ThumbInfoWorker サムネイル生成例外: {norm_path} - {e}")
        else:
            # 既存キャッシュから info_tuple を取得
            info_tuple = self.video_info_cache.get(norm_path, ("", ""))
        if self._canceled:
            print(f"[DEBUG] ThumbInfoWorker: キャンセル検知(emit前) {norm_path}")
            return
        # UIスレッドで安全に finished.emit を呼ぶ
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(0, lambda: self.finished.emit(norm_path, pil_thumb, info_tuple))
