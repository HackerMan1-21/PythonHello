"""
gui_main.py
PyQt5ベースの重複動画・画像検出/管理アプリのメインGUIクラス。

主な機能:
- メインウィンドウ・UI部品の構築
- サムネイルキャッシュ管理・手動クリア
- 重複検出・AI修復・壊れ動画チェック等の機能呼び出し
- 進捗・エラー通知・ユーザー操作全般

依存:
- PyQt5, Pillow, OpenCV, numpy, imagehash, component配下各種
"""

# -*- coding: utf-8 -*-
"""
component/gui_main.py
PyQt5ベースの重複動画・画像検出/管理アプリのメインGUIクラス。
UI・イベント・ユーザー操作・各種機能呼び出しを担当。
"""
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
from shutil import move as shutil_move
from PyQt5.QtWidgets import (QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QFileDialog, QMessageBox, QScrollArea, QProgressBar, QDialog, QGridLayout, QDialogButtonBox, QCheckBox, QProgressDialog, QGroupBox, QListView, QAbstractItemView, QStyledItemDelegate, QApplication, QStackedWidget)
from PyQt5.QtGui import QPixmap, QImage, QIcon
from PyQt5.QtCore import Qt, QSize, QTimer, QAbstractListModel, QModelIndex, QVariant, pyqtSignal

from component.duplicate_finder import find_duplicates_in_folder, get_image_and_video_files
from component.thumbnail.thumbnail_util import get_thumbnail_for_file, load_thumb_cache, save_thumb_cache, start_thumbnail_workers
from component.utils.file_util import move_to_trash, get_folder_state
from component.face_grouping import group_by_face_and_move, get_face_groups
from component.broken_checker import check_broken_videos
from component.ffmpeg_util import show_mp4_tool_dialog, repair_mp4, convert_mp4
from component.ai.ai_tools import digital_repair
from component.ui_util import show_detail_dialog, show_compare_dialog, add_thumbnail_widget, update_progress, drag_enter_event, drop_event, delete_selected_dialog, get_save_file_path, show_info_dialog, show_warning_dialog, show_question_dialog
from component.group_ui import create_duplicate_group_ui, show_face_grouping_dialog, move_selected_files_to_folder, show_broken_video_dialog
from component.thumbnail.thumbnail_util import ThumbnailCache

from .gui_thumbnail import ThumbnailListModel
from .gui_dialogs import show_progress_dialog
from .gui_utils import ThumbnailDelegate

from component.thumbnail.thumbnail_util import pil_image_to_qpixmap

print("DEBUG: gui_main.py loaded from", __file__)

# --- ここにDuplicateFinderGUIクラス本体を移植 ---

class DuplicateFinderGUI(QWidget):
    def __init__(self, parent=None):
        super(DuplicateFinderGUI, self).__init__(parent)
        self.thumb_queue = queue.Queue()
        self.thumb_cache = None
        self.thumb_workers = start_thumbnail_workers(self.thumb_queue, self.on_thumb_update, cache=None)
        self.init_ui()
        self.worker = None  # スレッド初期化
        self.thumb_widget_map = {}  # ファイルパス→サムネイルボタン
        self.cancel_requested = False
        self.selected_paths = set()
        self.last_folder_state = None
        self.current_view_mode = 0  # 0:グリッド, 1:仮想化
        # auto_reload_timerの初期化はここだけ
        self.auto_reload_timer = QTimer(self)
        self.auto_reload_timer.setInterval(3000)
        self.auto_reload_timer.timeout.connect(self.check_folder_update)

    def on_thumb_update(self, path, pil_image):
        print(f"[DEBUG] on_thumb_update: path={path}, pil_image={'OK' if pil_image is not None else 'None'}")
        # サムネイル生成完了時のコールバック
        from PyQt5.QtCore import QTimer
        def update_ui():
            btn = self.thumb_widget_map.get(path)
            if btn is not None and pil_image is not None:
                # 既に削除されたウィジェットや非表示ウィジェットにはsetIconしない
                if not btn.isVisible():
                    return
                try:
                    from component.thumbnail.thumbnail_util import pil_image_to_qpixmap
                    btn.setIcon(QIcon(pil_image_to_qpixmap(pil_image)))
                    btn.setIconSize(QSize(180, 180))
                except RuntimeError:
                    # QWidgetが既に削除済みの場合は無視
                    pass
        QTimer.singleShot(0, update_ui)
        # else: pass  # サムネイルボタンが見つからない場合は何もしない

    def init_ui(self):
        font_css = "font-size:20px;font-weight:bold;padding:8px 0 8px 0;color:#00ffe7;font-family:'Meiryo UI','Consolas','Fira Mono',monospace;"
        self.setStyleSheet('''
            QWidget {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0f2027, stop:0.5 #2c5364, stop:1 #232526);
                color: #00ffe7;
                font-family: "Meiryo UI", "Consolas", "Fira Mono", monospace;
                font-size: 14px;
                letter-spacing: 1px;
            }
            QLabel {
                color: #00ffe7;
                font-family: "Meiryo UI", "Consolas", "Fira Mono", monospace;
            }
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #232526, stop:1 #0f2027);
                color: #00ffe7;
                border: 2px solid #00ffe7;
                border-radius: 10px;
                padding: 8px 16px;
                font-size: 15px;
                font-family: "Meiryo UI", "Consolas", "Fira Mono", monospace;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #00ffe7;
                color: #232526;
                border: 2px solid #00ffe7;
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
                font-family: "Meiryo UI", "Consolas", "Fira Mono", monospace;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00ffe7, stop:1 #00ff99);
                border-radius: 8px;
            }
            QScrollArea {
                background: transparent;
                border: none;
            }
        ''')
        layout = QVBoxLayout()
        # --- 上部: 機能ボタン横並び ---
        btn_hbox = QHBoxLayout()
        self.dup_check_btn = QPushButton("重複チェック")
        self.dup_check_btn.setStyleSheet("font-size:16px;color:#00ffe7;border:2px solid #00ffe7;border-radius:8px;padding:8px;")
        self.dup_check_btn.clicked.connect(self.find_duplicates)
        btn_hbox.addWidget(self.dup_check_btn)
        self.face_group_btn = QPushButton("顔でグループ化して振り分け")
        self.face_group_btn.setStyleSheet("font-size:16px;color:#00ff99;border:2px solid #00ff99;border-radius:8px;padding:8px;")
        self.face_group_btn.clicked.connect(lambda: self.face_grouping_and_move())
        btn_hbox.addWidget(self.face_group_btn)
        self.mp4_tool_btn = QPushButton("MP4修復/変換")
        self.mp4_tool_btn.setStyleSheet("font-size:16px;color:#ffb300;border:2px solid #ffb300;border-radius:8px;padding:8px;")
        self.mp4_tool_btn.clicked.connect(lambda: self.show_mp4_tool_dialog())
        btn_hbox.addWidget(self.mp4_tool_btn)
        # --- サムネイルキャッシュ削除ボタン ---
        self.clear_thumb_cache_btn = QPushButton("サムネイルキャッシュ削除")
        self.clear_thumb_cache_btn.setStyleSheet("font-size:14px;color:#fff;background:#444;border:1px solid #00ffe7;border-radius:6px;padding:4px 8px;")
        self.clear_thumb_cache_btn.clicked.connect(lambda: self.clear_thumb_cache())
        btn_hbox.addWidget(self.clear_thumb_cache_btn)
        layout.addLayout(btn_hbox)
        # --- フォルダラベル・選択ボタン ---
        self.folder_label = QLabel("フォルダ未選択")
        self.folder_label.setStyleSheet(font_css)
        layout.addWidget(self.folder_label)
        self.select_btn = QPushButton("[ フォルダ選択 ]")
        self.select_btn.setStyleSheet("font-size:17px;font-weight:bold;background:transparent;color:#00ffe7;border:2px solid #00ffe7;")
        self.select_btn.clicked.connect(self.selectFiles)
        layout.addWidget(self.select_btn)
        # --- 進捗バー・ETA ---
        self.progress = QProgressBar()
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.progress_time_label = QLabel("")
        self.progress_time_label.setStyleSheet("font-size:13px;color:#00ff99;padding:2px 0 8px 0;")
        layout.addWidget(self.progress_time_label)
        self.eta_label = QLabel("")
        self.eta_label.setStyleSheet("font-size:13px;color:#ffb300;padding:2px 0 8px 0;")
        layout.addWidget(self.eta_label)
        # --- サムネイル/グループ表示用スクロールエリア ---
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout()
        self.content_widget.setLayout(self.content_layout)
        self.scroll_area.setWidget(self.content_widget)
        # --- 仮想化UI用リストビュー ---
        self.list_view = QListView()
        self.list_view.setViewMode(QListView.IconMode)
        self.list_view.setResizeMode(QListView.Adjust)
        self.list_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list_view.setSpacing(8)
        self.list_view.setItemDelegate(ThumbnailDelegate())
        self.list_view.setIconSize(QSize(180, 180))  # ← 追加
        # --- スタックウィジェットでUI切替 ---
        self.stacked = QStackedWidget()
        self.stacked.addWidget(self.scroll_area)  # 0: グリッドUI
        self.stacked.addWidget(self.list_view)    # 1: 仮想化UI
        layout.addWidget(self.stacked)
        # --- 表示切替ボタン ---
        self.toggle_view_btn = QPushButton("仮想化UIに切替")
        self.toggle_view_btn.setStyleSheet("font-size:14px;color:#fff;background:#444;border:1px solid #00ffe7;border-radius:6px;padding:4px 8px;")
        self.toggle_view_btn.clicked.connect(self.toggle_view_mode)
        layout.addWidget(self.toggle_view_btn)
        # --- 削除ボタン ---
        self.delete_btn = QPushButton("[ 選択ファイルをゴミ箱/移動 ]")
        self.delete_btn.setStyleSheet("font-size:17px;font-weight:bold;background:transparent;color:#ff00c8;border:2px solid #ff00c8;")
        self.delete_btn.clicked.connect(self.delete_selected)
        self.delete_btn.setEnabled(False)
        layout.addWidget(self.delete_btn)
        # --- 再読み込みボタン ---
        self.reload_btn = QPushButton("再読み込み")
        self.reload_btn.setStyleSheet("font-size:14px;color:#fff;background:#222;border:1px solid #444;border-radius:6px;padding:4px 8px;")
        self.reload_btn.clicked.connect(self.reload_folder)
        layout.addWidget(self.reload_btn)
        # --- キャンセルボタン ---
        self.cancel_btn = QPushButton("キャンセル")
        self.cancel_btn.setStyleSheet("font-size:14px;color:#fff;background:#c00;border:1px solid #f00;border-radius:6px;padding:4px 8px;")
        self.cancel_btn.clicked.connect(self.request_cancel)
        self.cancel_btn.setEnabled(False)
        layout.addWidget(self.cancel_btn)
        self.setLayout(layout)
        self.current_view_mode = 0  # 0:グリッド, 1:仮想化
        self.selected_paths = set()
        # --- 初期表示で重複チェックを呼ばない（フォルダ選択後のみ呼ぶ） ---

    def selectFiles(self):
        # フォルダ選択ダイアログ
        options = QFileDialog.Options()
        folder = QFileDialog.getExistingDirectory(self, "フォルダを選択", "", options=options)
        if folder:
            self.folder_label.setText(folder)
            self.load_thumb_cache(folder)
            files = get_image_and_video_files(folder)
            self.clear_content()
            self.processFiles(files)
            self.find_duplicates()  # フォルダ選択時に自動で重複チェックを実行

    def load_thumb_cache(self, folder=None):
        print(f"[DEBUG] load_thumb_cache: folder={folder}")
        # サムネイルキャッシュをロード
        try:
            load_thumb_cache(folder)
        except Exception as e:
            print(f"[DEBUG] load_thumb_cache: Exception {e}")
            pass

    def save_thumb_cache(self, folder=None):
        print(f"[DEBUG] save_thumb_cache: folder={folder}")
        # サムネイルキャッシュを保存
        try:
            save_thumb_cache(folder)
        except Exception as e:
            print(f"[DEBUG] save_thumb_cache: Exception {e}")
            pass

    def processFiles(self, files):
        # ファイル処理ロジック（サムネイル非同期生成対応）
        self.fileQueue = queue.Queue()
        for file in files:
            self.fileQueue.put(file)
        self.worker = threading.Thread(target=self.detectDuplicates)
        self.worker.start()

    def detectDuplicates(self):
        print("[DEBUG] detectDuplicates: start")
        while not self.fileQueue.empty():
            file = self.fileQueue.get()
            print(f"[DEBUG] detectDuplicates: processing {file}")
            # ...ファイル処理コード...
            self.fileQueue.task_done()
        print("[DEBUG] detectDuplicates: end")

    def runDetection(self):
        # 検出実行
        self.progressBar.setValue(0)
        # ...検出実行コード...

    def closeEvent(self, event):
        # ウィンドウ閉じる処理
        if self.worker and hasattr(self.worker, 'is_alive') and self.worker.is_alive():
            reply = QMessageBox.question(self, 'Message', 'Detection is still running. Do you really want to exit?', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()

    def add_thumbnail_widget(self, file_path):
        # サムネイル付きファイル表示ウィジェットを追加（UIユーティリティに移譲）
        add_thumbnail_widget(self, self.content_layout, file_path, self.toggle_select, self.selected_paths, self.delete_btn)

    def toggle_select(self, widget, file_path, selected_paths=None, delete_btn=None):
        # 選択状態の切り替え
        if selected_paths is None:
            selected_paths = self.selected_paths
        if delete_btn is None:
            delete_btn = self.delete_btn
        if file_path in selected_paths:
            selected_paths.remove(file_path)
            widget.setStyleSheet("background:rgba(0,0,0,0.3);border-radius:8px;margin:4px 0;padding:4px 8px;")
        else:
            selected_paths.add(file_path)
            widget.setStyleSheet("background:rgba(0,255,231,0.25);border:2px solid #00ffe7;border-radius:8px;margin:4px 0;padding:4px 8px;")
        delete_btn.setEnabled(len(selected_paths) > 0)

    def dragEnterEvent(self, event):
        # ドラッグ＆ドロップでファイル追加・移動（UIユーティリティに移譲）
        drag_enter_event(event)

    def dropEvent(self, event):
        # ドロップされたファイルを検出対象に追加 or 移動（UIユーティリティに移譲）
        drop_event(event, self.processFiles)

    def toggle_view_mode(self):
        if self.current_view_mode == 0:
            # 仮想UIに切り替え
            # 既存の仮想UI用ウィジェットを削除
            if hasattr(self, 'virtual_grid_widget') and self.virtual_grid_widget:
                self.stacked.removeWidget(self.virtual_grid_widget)
                self.virtual_grid_widget.deleteLater()
            # 仮想UI用グリッドを生成
            virtual_grid_widget = QWidget()
            grid = QGridLayout()
            grid.setHorizontalSpacing(12)
            grid.setVerticalSpacing(16)
            max_col = 4
            row = 0
            col = 0
            if hasattr(self, 'group_widgets') and self.group_widgets:
                for group_box in self.group_widgets:
                    g = group_box.layout()
                    for i in range(g.count()):
                        file_widget = g.itemAt(i).widget()
                        if file_widget:
                            grid.addWidget(file_widget, row, col)
                            col += 1
                            if col >= max_col:
                                col = 0
                                row += 1
            virtual_grid_widget.setLayout(grid)
            self.virtual_grid_widget = virtual_grid_widget
            self.stacked.addWidget(virtual_grid_widget)
            self.stacked.setCurrentWidget(virtual_grid_widget)
            self.toggle_view_btn.setText("グリッドUIに切替")
            self.current_view_mode = 1
        else:
            self.stacked.setCurrentIndex(0)
            self.toggle_view_btn.setText("仮想化UIに切替")
            self.current_view_mode = 0

    def request_cancel(self):
        self.cancel_requested = True
        self.cancel_btn.setEnabled(False)

    def find_duplicates(self):
        print("[DEBUG] find_duplicates: start")
        self.progress.setValue(0)
        self.progress_time_label.setText("経過: 0.0 秒")
        self.eta_label.setText("")
        self.cancel_btn.setEnabled(True)
        self.cancel_requested = False
        start_time = time.time()
        folder = self.folder_label.text()
        def worker():
            print(f"[DEBUG] find_duplicates.worker: folder={folder}")
            duplicates, _ = find_duplicates_in_folder(folder, parallel=True)
            print(f"[DEBUG] find_duplicates.worker: duplicates found={len(duplicates)}")
            total = len(duplicates)
            last_update = time.time()
            for idx, group in enumerate(duplicates):
                if self.cancel_requested:
                    print("[DEBUG] find_duplicates.worker: cancel requested")
                    break
                elapsed = time.time() - start_time
                eta = (elapsed / (idx + 1)) * (total - (idx + 1)) if idx > 0 else 0
                if idx % 100 == 0 or time.time() - last_update > 0.1 or idx == total - 1:
                    QTimer.singleShot(0, lambda v=idx+1, e=elapsed, t=eta: update_progress(self.progress, v, self.progress_time_label, self.eta_label, e, t))
                    last_update = time.time()
            def update_ui():
                print("[DEBUG] find_duplicates.worker: update_ui called")
                try:
                    self.clear_content()
                    if not duplicates:
                        self.content_layout.addWidget(QLabel("重複ファイルは見つかりませんでした"))
                        print("DEBUG: find_duplicates end (no duplicates)")
                        return
                    self.group_widgets = []
                    self.thumb_widget_map = {}
                    self.thumb_cache = ThumbnailCache(folder)
                    self.stacked.setCurrentIndex(0)
                    print("DEBUG: stacked.setCurrentIndex(0) called")
                    for i, group in enumerate(duplicates):
                        # 最後のグループかつエラーグループ（pHash失敗ファイル群）はエラーUIで描画
                        is_error_group = False
                        if i == len(duplicates) - 1:
                            if isinstance(group, list) and len(group) > 0:
                                try:
                                    # サムネイルが全てNoneならエラーグループ
                                    is_error_group = all((get_thumbnail_for_file(f, (180, 180), cache=self.thumb_cache) is None) for f in group)
                                except Exception:
                                    is_error_group = False
                        try:
                            if is_error_group:
                                from component.group_ui import create_error_group_ui
                                group_box = create_error_group_ui(
                                    group,
                                    get_thumbnail_for_file,
                                    show_detail_dialog,
                                    self.delete_single_file,
                                    thumb_cache=self.thumb_cache,
                                    defer_queue=self.thumb_queue,
                                    thumb_widget_map=self.thumb_widget_map
                                )
                                group_box.setStyleSheet("margin-bottom: 24px; border: 2px solid #ff4444; border-radius: 12px; padding: 8px;")
                            else:
                                group_box = create_duplicate_group_ui(
                                    group,
                                    get_thumbnail_for_file,
                                    show_detail_dialog,
                                    self.delete_single_file,
                                    show_compare_dialog,
                                    thumb_cache=self.thumb_cache,
                                    defer_queue=self.thumb_queue,
                                    thumb_widget_map=self.thumb_widget_map
                                )
                                group_box.setStyleSheet("margin-bottom: 24px; border: 2px solid #00ffe7; border-radius: 12px; padding: 8px;")
                            self.content_layout.addWidget(group_box)
                        except Exception as e:
                            print(f"[DEBUG] update_ui: group UI exception: {e}")
                    self.content_widget.adjustSize()
                    print("DEBUG: find_duplicates end")
                except Exception as e:
                    print(f"[DEBUG] update_ui: outer exception: {e}")
            QTimer.singleShot(0, update_ui)
        threading.Thread(target=worker).start()

    def check_folder_update(self):
        # フォルダ内のファイル変化を監視・自動更新
        folder = self.folder_label.text()
        if not folder or folder == "フォルダ未選択":
            return
        try:
            state = get_folder_state(folder)
        except Exception as e:
            logging.warning("Failed to get folder state: %s", e)
            return
        if self.last_folder_state is None:
            self.last_folder_state = state
            return
        if state != self.last_folder_state:
            logging.info("Folder state changed, reloading...")
            self.reload_folder()
        self.last_folder_state = state

    def reload_folder(self):
        # フォルダ再読み込み
        folder = self.folder_label.text()
        if not folder or folder == "フォルダ未選択":
            return
        self.clear_content()
        self.load_thumb_cache(folder)
        self.processFiles(get_image_and_video_files(folder))
        self.cancel_btn.setEnabled(False)
        self.request_cancel()
        self.progress.setValue(0)
        self.progress_time_label.setText("経過: 0.0 秒")
        self.eta_label.setText("")
        QMessageBox.information(self, "情報", "フォルダを再読み込みました。")

    def clear_thumb_cache(self):
        print("[DEBUG] clear_thumb_cache: called")
        folder = self.folder_label.text()
        if not folder or folder == "フォルダ未選択":
            print("[DEBUG] clear_thumb_cache: no folder selected")
            return
        reply = QMessageBox.question(self, "確認", "サムネイルキャッシュを削除しますか？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            try:
                print(f"[DEBUG] clear_thumb_cache: load_thumb_cache({folder})")
                thumb_cache = load_thumb_cache(folder)
                if thumb_cache:
                    print("[DEBUG] clear_thumb_cache: cache loaded, clearing cache")
                    if hasattr(thumb_cache, 'clear') and callable(thumb_cache.clear):
                        thumb_cache.clear()
                        thumb_cache.save()
                    self.thumb_cache = thumb_cache
                    self.clear_content()
                    self.processFiles(get_image_and_video_files(folder))
                    QMessageBox.information(self, "完了", "サムネイルキャッシュを削除しました。")
                else:
                    print("[DEBUG] clear_thumb_cache: no cache to clear")
                    QMessageBox.information(self, "情報", "削除するキャッシュファイルが見つかりませんでした。")
            except Exception as e:
                print(f"[DEBUG] clear_thumb_cache: Exception {e}")
                QMessageBox.critical(self, "エラー", f"サムネイルキャッシュの削除中にエラーが発生しました:\n{str(e)}")

    def delete_selected(self):
        # 選択ファイルをゴミ箱に移動
        if not self.selected_paths or len(self.selected_paths) == 0:
            return
        reply = QMessageBox.question(self, "確認", "選択したファイルをゴミ箱に移動しますか？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            failed_files = []
            for path in self.selected_paths:
                try:
                    move_to_trash(path)
                except Exception as e:
                    logging.warning("Failed to move to trash %s: %s", path, e)
                    failed_files.append(path)
            if failed_files:
                QMessageBox.warning(self, "一部失敗", f"以下のファイルの移動に失敗しました:\n" + "\n".join(failed_files))
            self.selected_paths.clear()
            self.clear_content()
            self.processFiles(get_image_and_video_files(self.folder_label.text()))
            QMessageBox.information(self, "完了", "選択ファイルをゴミ箱に移動しました。")

    def delete_single_file(self, file_path):
        # 単一ファイルをゴミ箱に移動
        try:
            move_to_trash(file_path)
            QMessageBox.information(self, "完了", f"ファイルをゴミ箱に移動しました:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"ファイルの削除中にエラーが発生しました:\n{str(e)}")

    def clear_content(self):
        # サムネイル・グループ表示エリアをクリア
        if hasattr(self, 'content_layout'):
            while self.content_layout.count():
                item = self.content_layout.takeAt(0)
                if item is not None:
                    w = item.widget()
                    if w is not None:
                        w.setParent(None)
                        w.deleteLater()
        self.group_widgets = []
        self.thumb_widget_map = {}
