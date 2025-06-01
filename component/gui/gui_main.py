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

print("DEBUG: gui_main.py loaded from", __file__)

# --- ここにDuplicateFinderGUIクラス本体を移植 ---

class ThumbnailListModel(QAbstractListModel):
    thumb_updated = pyqtSignal(str)
    def __init__(self, file_list, thumb_cache, defer_queue, parent=None):
        super().__init__(parent)
        self.file_list = file_list
        self.thumb_cache = thumb_cache
        self.defer_queue = defer_queue
        self.thumb_updated.connect(self.on_thumb_updated)
    def rowCount(self, parent=QModelIndex()):
        return len(self.file_list)
    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self.file_list)):
            return QVariant()
        path = self.file_list[index.row()]
        if role == Qt.DecorationRole:
            from component.thumbnail.thumbnail_util import get_thumbnail_for_file, pil_image_to_qpixmap
            pil_thumb = get_thumbnail_for_file(path, (180, 180), cache=self.thumb_cache, defer_queue=self.defer_queue)
            return QIcon(pil_image_to_qpixmap(pil_thumb))
        if role == Qt.DisplayRole:
            return os.path.basename(path)
        return QVariant()
    def on_thumb_updated(self, path):
        if path in self.file_list:
            row = self.file_list.index(path)
            self.dataChanged.emit(self.index(row), self.index(row))

class ThumbnailDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        super().paint(painter, option, index)
    def sizeHint(self, option, index):
        return QSize(200, 200)

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
        # --- 状態管理 ---
        self.selected_paths = set()
        self.auto_reload_timer = QTimer(self)
        self.auto_reload_timer.setInterval(3000)  # 3秒ごとに監視
        self.auto_reload_timer.timeout.connect(self.check_folder_update)
        self.last_folder_state = None
        self.current_view_mode = 0  # 0:グリッド, 1:仮想化

    def on_thumb_update(self, path):
        # サムネイル生成完了時のコールバック
        btn = self.thumb_widget_map.get(path)
        if btn is not None:
            from component.thumbnail.thumbnail_util import get_thumbnail_for_file, pil_image_to_qpixmap
            pil_thumb = get_thumbnail_for_file(path, (180, 180), cache=self.thumb_cache)
            btn.setIcon(QIcon(pil_image_to_qpixmap(pil_thumb)))
            btn.setIconSize(QSize(180, 180))
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
        self.face_group_btn.clicked.connect(self.face_grouping_and_move)
        btn_hbox.addWidget(self.face_group_btn)
        self.mp4_tool_btn = QPushButton("MP4修復/変換")
        self.mp4_tool_btn.setStyleSheet("font-size:16px;color:#ffb300;border:2px solid #ffb300;border-radius:8px;padding:8px;")
        self.mp4_tool_btn.clicked.connect(self.show_mp4_tool_dialog)
        btn_hbox.addWidget(self.mp4_tool_btn)
        # --- サムネイルキャッシュ削除ボタン ---
        self.clear_thumb_cache_btn = QPushButton("サムネイルキャッシュ削除")
        self.clear_thumb_cache_btn.setStyleSheet("font-size:14px;color:#fff;background:#444;border:1px solid #00ffe7;border-radius:6px;padding:4px 8px;")
        self.clear_thumb_cache_btn.clicked.connect(self.clear_thumb_cache)
        btn_hbox.addWidget(self.clear_thumb_cache_btn)
        layout.addLayout(btn_hbox)
        # --- フォルダラベル・選択ボタン ---
        self.folder_label = QLabel("フォルダ未選択")
        self.folder_label.setStyleSheet(font_css)
        layout.addWidget(self.folder_label)
        self.select_btn = QPushButton("[ フォルダ選択 ]")
        self.select_btn.setStyleSheet("font-size:17px;font-weight:bold;background:transparent;color:#00ffe7;border:2px solid #00ffe7;")
        self.select_btn.clicked.connect(self.select_folder)
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
        self.auto_reload_timer = QTimer(self)
        self.auto_reload_timer.setInterval(3000)
        self.auto_reload_timer.timeout.connect(self.check_folder_update)
        self.last_folder_state = None
        # --- 初期表示で重複チェックを呼ばない（フォルダ選択後のみ呼ぶ） ---

    def selectFiles(self):
        # ファイル選択ダイアログ
        options = QFileDialog.Options()
        files, _ = QFileDialog.getOpenFileNames(self, "Select Video/Image Files", "", "All Files (*);;Image Files (*.png;*.jpg;*.jpeg);;Video Files (*.mp4;*.avi)", options=options)
        if files:
            self.processFiles(files)

    def load_thumb_cache(self, folder=None):
        # サムネイルキャッシュをロード
        try:
            load_thumb_cache(folder)
        except Exception:
            pass

    def save_thumb_cache(self, folder=None):
        # サムネイルキャッシュを保存
        try:
            save_thumb_cache(folder)
        except Exception:
            pass

    def processFiles(self, files):
        # ファイル処理ロジック（サムネイル非同期生成対応）
        self.fileQueue = queue.Queue()
        for file in files:
            self.fileQueue.put(file)
        self.worker = threading.Thread(target=self.detectDuplicates)
        self.worker.start()

    def detectDuplicates(self):
        # 重複検出ロジック
        while not self.fileQueue.empty():
            file = self.fileQueue.get()
            # ...ファイル処理コード...
            self.fileQueue.task_done()

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
        print("DEBUG: find_duplicates called")
        folder = self.folder_label.text()
        if not folder or folder == "フォルダ未選択":
            show_warning_dialog(self, "警告", "先にフォルダを選択してください")
            return
        update_progress(self.progress, 0, self.progress_time_label, self.eta_label, 0)
        start_time = time.time()
        self.load_thumb_cache(folder)
        # --- 非同期で重複検出・サムネイル生成 ---
        def run_detection():
            duplicates, progress_iter = find_duplicates_in_folder(folder, progress_callback=progress_callback)
            def update_ui():
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
                for group in duplicates:
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
                    print("DEBUG: group_box created", group_box)
                    group_box.setStyleSheet("margin-bottom: 24px; border: 2px solid #00ffe7; border-radius: 12px; padding: 8px;")
                    self.content_layout.addWidget(group_box)
                    self.group_widgets.append(group_box)
                self.delete_btn.setEnabled(False)
                elapsed = time.time() - start_time
                update_progress(self.progress, 100, self.progress_time_label, self.eta_label, elapsed, 0)
                self.save_thumb_cache(folder)
                self.cancel_btn.setEnabled(False)
                print("DEBUG: find_duplicates end")
            QTimer.singleShot(0, update_ui)
        # 進捗更新用のラッパー
        def progress_callback(value, total):
            elapsed = time.time() - start_time
            eta = (elapsed / value * (total - value)) if value > 0 else None
            update_progress(self.progress, int(value / total * 100), self.progress_time_label, self.eta_label, elapsed, eta)
        threading.Thread(target=run_detection, daemon=True).start()

    def reload_folder(self):
        # 再読み込み処理
        folder = self.folder_label.text()
        if not folder or folder == "フォルダ未選択":
            show_warning_dialog(self, "警告", "先にフォルダを選択してください")
            return
        self.find_duplicates()

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "重複チェックしたいフォルダを選択")
        if not folder:
            return
        self.folder_label.setText(folder)
        self.find_duplicates()

    def clear_content(self):
        # サムネイル/グループ表示エリアをクリア
        print("DEBUG: clear_content called, count=", self.content_layout.count())
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            widget = item.widget()
            if widget:
                print("DEBUG: delete widget", widget)
                widget.deleteLater()

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

    def delete_selected(self):
        # 選択ファイルの削除処理（UIユーティリティに移譲）
        delete_selected_dialog(self, self.selected_paths, self.reload_folder)

    def delete_single_file(self, file_path):
        # 個別削除
        move_to_trash(file_path)
        show_info_dialog(self, "削除", f"{os.path.basename(file_path)} をゴミ箱に移動しました")
        self.reload_folder()

    def face_grouping_and_move(self):
        # 顔グループ化処理（グループUIユーティリティに移譲）
        folder = self.folder_label.text()
        if not folder or folder == "フォルダ未選択":
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(self, "警告", "先にフォルダを選択してください")
            return
        groups = get_face_groups(folder)
        self.thumb_cache = ThumbnailCache(folder)
        print("DEBUG: show_face_grouping_dialog call", groups, self.thumb_cache)
        show_face_grouping_dialog(
            self,
            groups,
            lambda checkboxes, dlg: move_selected_files_to_folder(checkboxes, dlg) or self.reload_folder(),
            delete_cb=self.delete_single_file,
            thumb_cache=self.thumb_cache,
            defer_queue=self.thumb_queue
        )

    def show_mp4_tool_dialog(self):
        # MP4修復/変換/デジタル修復ダイアログ
        folder = self.folder_label.text()
        if not folder or folder == "フォルダ未選択":
            QMessageBox.warning(self, "警告", "先にフォルダを選択してください")
            return
        show_mp4_tool_dialog(self, folder)

    def check_folder_update(self):
        # フォルダの更新監視（自動リロード）
        folder = self.folder_label.text()
        if not folder or folder == "フォルダ未選択":
            return
        state = get_folder_state(folder)
        if self.last_folder_state is not None and state != self.last_folder_state:
            self.reload_folder()
        self.last_folder_state = state

    def show_broken_video_dialog(self):
        # 壊れ動画検出・修復ダイアログ
        folder = QFileDialog.getExistingDirectory(self, "壊れ検出したいフォルダを選択")
        if not folder:
            return
        video_exts = ('.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm', '.mpg', '.mpeg', '.3gp')
        broken_groups = check_broken_videos(folder, video_exts)
        self.thumb_cache = ThumbnailCache(folder)
        print("DEBUG: show_broken_video_dialog call", broken_groups, self.thumb_cache)
        show_broken_video_dialog(
            self,
            broken_groups,
            self.run_mp4_repair,
            self.run_mp4_convert,
            self.run_mp4_digital_repair,
            thumb_cache=self.thumb_cache,
            defer_queue=self.thumb_queue
        )

    def run_mp4_repair(self, file_path=None):
        if not file_path:
            return
        save_path, _ = get_save_file_path(self, "修復後の保存先を指定", os.path.splitext(file_path)[0] + "_repaired" + os.path.splitext(file_path)[1], "動画ファイル (*.*)")
        if not save_path:
            return
        try:
            repair_mp4(file_path, save_path)
            show_info_dialog(self, "修復完了", f"{os.path.basename(file_path)} の修復が完了しました")
        except Exception as e:
            show_warning_dialog(self, "修復失敗", str(e))

    def run_mp4_convert(self, file_path=None):
        if not file_path:
            return
        save_path, _ = get_save_file_path(self, "変換後の保存先を指定", os.path.splitext(file_path)[0] + "_converted" + os.path.splitext(file_path)[1], "動画ファイル (*.*)")
        if not save_path:
            return
        try:
            convert_mp4(file_path, save_path)
            show_info_dialog(self, "変換完了", f"{os.path.basename(file_path)} の変換が完了しました")
        except Exception as e:
            show_warning_dialog(self, "変換失敗", str(e))

    def run_mp4_digital_repair(self, file_path=None):
        if not file_path:
            return
        save_path, _ = get_save_file_path(self, "高画質化後の保存先を指定", os.path.splitext(file_path)[0] + "_aiup_gfpgan" + os.path.splitext(file_path)[1], "動画ファイル (*.*)")
        if not save_path:
            return
        try:
            digital_repair(file_path, save_path)
            show_info_dialog(self, "AI超解像完了", f"{os.path.basename(file_path)} のAI超解像が完了しました")
        except Exception as e:
            show_warning_dialog(self, "AI超解像失敗", str(e))

    def clear_thumb_cache(self):
        # サムネイルキャッシュ削除処理
        folder = self.folder_label.text()
        if not folder or folder == "フォルダ未選択":
            QMessageBox.information(self, "キャッシュ削除", "先にフォルダを選択してください")
            return
        cache = ThumbnailCache(folder)
        cache.clear()
        try:
            import os
            os.remove(cache.cache_file)
        except Exception:
            pass
        QMessageBox.information(self, "キャッシュ削除", "サムネイルキャッシュを削除しました")
