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
from PyQt5.QtWidgets import (QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QFileDialog, QMessageBox, QScrollArea, QProgressBar, QDialog, QGridLayout, QDialogButtonBox, QCheckBox, QProgressDialog, QGroupBox)
from PyQt5.QtGui import QPixmap, QImage, QIcon
from PyQt5.QtCore import Qt, QSize, QTimer

# --- ここにDuplicateFinderGUIクラス本体を移植 ---

class DuplicateFinderGUI(QWidget):
    def __init__(self, parent=None):
        super(DuplicateFinderGUI, self).__init__(parent)
        self.init_ui()
        self.worker = None  # スレッド初期化

    def init_ui(self):
        font_css = "font-size:20px;font-weight:bold;padding:8px 0 8px 0;color:#00ffe7;text-shadow:0 0 8px #00ffe7;font-family:'Meiryo UI','Consolas','Fira Mono',monospace;"
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
                text-shadow: 0 0 6px #00ffe7, 0 0 2px #00ffe7;
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
                font-family: "Meiryo UI", "Consolas", "Fira Mono", monospace;
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
        self.progress_time_label.setStyleSheet("font-size:13px;color:#00ff99;padding:2px 0 8px 0;text-shadow:0 0 8px #00ff99;")
        layout.addWidget(self.progress_time_label)
        self.eta_label = QLabel("")
        self.eta_label.setStyleSheet("font-size:13px;color:#ffb300;padding:2px 0 8px 0;text-shadow:0 0 8px #ffb300;")
        layout.addWidget(self.eta_label)
        # --- サムネイル/グループ表示用スクロールエリア ---
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout()
        self.content_widget.setLayout(self.content_layout)
        self.scroll_area.setWidget(self.content_widget)
        layout.addWidget(self.scroll_area)
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
        self.setLayout(layout)
        # --- 状態管理 ---
        self.selected_paths = set()
        self.auto_reload_timer = QTimer(self)
        self.auto_reload_timer.setInterval(3000)  # 3秒ごとに監視
        self.auto_reload_timer.timeout.connect(self.check_folder_update)
        self.last_folder_state = None

    def selectFiles(self):
        # ファイル選択ダイアログ
        options = QFileDialog.Options()
        files, _ = QFileDialog.getOpenFileNames(self, "Select Video/Image Files", "", "All Files (*);;Image Files (*.png;*.jpg;*.jpeg);;Video Files (*.mp4;*.avi)", options=options)
        if files:
            self.processFiles(files)

    def processFiles(self, files):
        # ファイル処理ロジック
        self.fileQueue = queue.Queue()
        for file in files:
            self.fileQueue.put(file)

        # スレッド開始
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

    def dragEnterEvent(self, event):
        # ドラッグ＆ドロップでファイル追加・移動
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        # ドロップされたファイルを検出対象に追加 or 移動
        files = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if not files:
            return
        # ドロップ先がグループUIなら移動、そうでなければ追加
        # （ここでは単純に追加とする。グループUIへの移動は拡張可）
        self.processFiles(files)

    def find_duplicates(self):
        # 重複チェック処理（グループごとのUI・個別削除ボタン・詳細ダイアログ対応）
        folder = self.folder_label.text()
        if not folder or folder == "フォルダ未選択":
            QMessageBox.warning(self, "警告", "先にフォルダを選択してください")
            return
        from component.duplicate_finder import find_duplicates_in_folder
        from component.thumbnail_util import get_thumbnail_for_file
        self.progress.setValue(0)
        self.progress_time_label.setText("")
        self.eta_label.setText("")
        start_time = time.time()
        duplicates, progress_iter = find_duplicates_in_folder(folder, self.progress)
        self.clear_content()
        if not duplicates:
            self.content_layout.addWidget(QLabel("重複ファイルは見つかりませんでした"))
            return
        self.group_widgets = []
        for group in duplicates:
            group_box = QGroupBox("重複グループ")
            group_layout = QHBoxLayout()
            for f in group:
                thumb = get_thumbnail_for_file(f)
                thumb_label = QLabel()
                if thumb:
                    thumb_label.setPixmap(QPixmap.fromImage(thumb).scaled(120, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                else:
                    thumb_label.setText("No Thumbnail")
                thumb_label.setFixedSize(120, 90)
                name_label = QLabel(os.path.basename(f))
                name_label.setStyleSheet("font-size:13px;color:#00ffe7;")
                detail_btn = QPushButton("詳細")
                detail_btn.setStyleSheet("font-size:12px;color:#00ff99;")
                detail_btn.clicked.connect(lambda _, path=f: self.show_detail_dialog(path))
                del_btn = QPushButton("削除")
                del_btn.setStyleSheet("font-size:12px;color:#ff00c8;")
                del_btn.clicked.connect(lambda _, path=f: self.delete_single_file(path))
                compare_btn = QPushButton("比較")
                compare_btn.setStyleSheet("font-size:12px;color:#ffb300;")
                compare_btn.clicked.connect(lambda _, path=f, group=group: self.show_compare_dialog(path, [x for x in group if x != path][0] if len(group)>1 else None))
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
            self.content_layout.addWidget(group_box)
            self.group_widgets.append(group_box)
        self.delete_btn.setEnabled(False)
        elapsed = time.time() - start_time
        self.eta_label.setText(f"完了: {elapsed:.1f}秒")

    def show_detail_dialog(self, file_path):
        # ファイルの詳細情報を表示
        info = f"パス: {file_path}\n"
        try:
            size = os.path.getsize(file_path)
            info += f"サイズ: {size/1024/1024:.2f} MB\n"
        except Exception:
            info += "サイズ: 不明\n"
        QMessageBox.information(self, "ファイル詳細", info)

    def show_compare_dialog(self, file1, file2):
        # 2つの動画/画像を並べて比較再生（簡易UI）
        if not file2:
            QMessageBox.information(self, "比較再生", "比較対象がありません")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("比較再生")
        layout = QHBoxLayout()
        for f in [file1, file2]:
            label = QLabel(os.path.basename(f))
            thumb = None
            try:
                from component.thumbnail_util import get_thumbnail_for_file
                thumb = get_thumbnail_for_file(f)
            except Exception:
                pass
            thumb_label = QLabel()
            if thumb:
                thumb_label.setPixmap(QPixmap.fromImage(thumb).scaled(180, 120, Qt.KeepAspectRatio, Qt.SmoothTransformation))
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

    def delete_single_file(self, file_path):
        # 個別削除
        from component.file_util import move_to_trash
        move_to_trash(file_path)
        QMessageBox.information(self, "削除", f"{os.path.basename(file_path)} をゴミ箱に移動しました")
        self.reload_folder()

    def reload_folder(self):
        # 再読み込み処理
        folder = self.folder_label.text()
        if not folder or folder == "フォルダ未選択":
            QMessageBox.warning(self, "警告", "先にフォルダを選択してください")
            return
        self.process_folder(folder)

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "重複チェックしたいフォルダを選択")
        if not folder:
            return
        self.folder_label.setText(folder)
        self.process_folder(folder)

    def process_folder(self, folder):
        # フォルダ内のファイル一覧を取得し、サムネイル表示・重複検出準備
        from component.duplicate_finder import get_image_and_video_files
        self.clear_content()
        self.selected_paths = set()
        files = get_image_and_video_files(folder)
        if not files:
            self.content_layout.addWidget(QLabel("ファイルが見つかりませんでした"))
            return
        for f in files:
            self.add_thumbnail_widget(f)
        self.delete_btn.setEnabled(False)

    def clear_content(self):
        # サムネイル/グループ表示エリアをクリア
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def add_thumbnail_widget(self, file_path):
        # サムネイル付きファイル表示ウィジェットを追加
        from component.thumbnail_util import get_thumbnail_for_file
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
        widget.mousePressEvent = lambda e, p=file_path: self.toggle_select(widget, p)
        self.content_layout.addWidget(widget)

    def toggle_select(self, widget, file_path):
        # 選択状態の切り替え
        if file_path in self.selected_paths:
            self.selected_paths.remove(file_path)
            widget.setStyleSheet("background:rgba(0,0,0,0.3);border-radius:8px;margin:4px 0;padding:4px 8px;")
        else:
            self.selected_paths.add(file_path)
            widget.setStyleSheet("background:rgba(0,255,231,0.25);border:2px solid #00ffe7;border-radius:8px;margin:4px 0;padding:4px 8px;")
        self.delete_btn.setEnabled(len(self.selected_paths) > 0)

    def delete_selected(self):
        # 選択ファイルの削除処理（ゴミ箱/別フォルダ/キャンセル選択ダイアログ付き）
        if not self.selected_paths:
            QMessageBox.information(self, "削除", "削除するファイルを選択してください")
            return
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
            from component.file_util import move_to_trash
            for path in list(self.selected_paths):
                try:
                    move_to_trash(path)
                except Exception:
                    failed.append(path)
        elif msg.clickedButton() == move_btn:
            target_dir = QFileDialog.getExistingDirectory(self, "移動先フォルダを選択（新規作成可）")
            if not target_dir:
                return
            for path in list(self.selected_paths):
                try:
                    shutil_move(path, target_dir)
                except Exception:
                    failed.append(path)
        if failed:
            QMessageBox.warning(self, "失敗", f"一部のファイルの移動/削除に失敗しました:\n" + '\n'.join(failed))
        else:
            QMessageBox.information(self, "削除/移動", f"{len(self.selected_paths)}件のファイルを処理しました")
        self.reload_folder()
        self.selected_paths.clear()
        self.delete_btn.setEnabled(False)

    def face_grouping_and_move(self):
        # 顔グループ化処理
        folder = self.folder_label.text()
        if not folder or folder == "フォルダ未選択":
            QMessageBox.warning(self, "警告", "先にフォルダを選択してください")
            return
        from component.face_grouping import group_by_face_and_move
        group_by_face_and_move(folder)
        QMessageBox.information(self, "顔グループ化", "顔グループ化・振り分けが完了しました")

    def show_mp4_tool_dialog(self):
        # MP4修復/変換/デジタル修復ダイアログ
        from component.ffmpeg_util import show_mp4_tool_dialog
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
        from component.file_util import get_folder_state
        state = get_folder_state(folder)
        if self.last_folder_state is not None and state != self.last_folder_state:
            self.reload_folder()
        self.last_folder_state = state

    def show_broken_video_dialog(self):
        # 壊れ動画検出・修復ダイアログ
        folder = QFileDialog.getExistingDirectory(self, "壊れ検出したいフォルダを選択")
        if not folder:
            return
        from component.broken_checker import check_broken_videos
        video_exts = ('.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm', '.mpg', '.mpeg', '.3gp')
        broken_groups = check_broken_videos(folder, video_exts)
        if not broken_groups:
            QMessageBox.information(self, "壊れ動画検出", "壊れた動画は見つかりませんでした")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("壊れ動画グループ")
        vbox = QVBoxLayout()
        for group in broken_groups:
            group_box = QGroupBox("壊れ動画グループ")
            group_layout = QHBoxLayout()
            for f in group:
                name_label = QLabel(os.path.basename(f))
                repair_btn = QPushButton("修復")
                repair_btn.clicked.connect(lambda _, path=f: self.run_mp4_repair(path))
                convert_btn = QPushButton("変換")
                convert_btn.clicked.connect(lambda _, path=f: self.run_mp4_convert(path))
                digital_btn = QPushButton("デジタル修復")
                digital_btn.clicked.connect(lambda _, path=f: self.run_mp4_digital_repair(path))
                vbox2 = QVBoxLayout()
                vbox2.addWidget(name_label)
                vbox2.addWidget(repair_btn)
                vbox2.addWidget(convert_btn)
                vbox2.addWidget(digital_btn)
                file_widget = QWidget()
                file_widget.setLayout(vbox2)
                group_layout.addWidget(file_widget)
            group_box.setLayout(group_layout)
            vbox.addWidget(group_box)
        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(dlg.reject)
        vbox.addWidget(btns)
        dlg.setLayout(vbox)
        dlg.exec_()

    def run_mp4_repair(self, file_path=None):
        # MP4修復処理（簡易）
        if not file_path:
            return
        save_path, _ = QFileDialog.getSaveFileName(self, "修復後の保存先を指定", os.path.splitext(file_path)[0] + "_repaired" + os.path.splitext(file_path)[1], "動画ファイル (*.*)")
        if not save_path:
            return
        try:
            from component.ffmpeg_util import repair_mp4
            repair_mp4(file_path, save_path)
            QMessageBox.information(self, "修復完了", f"{os.path.basename(file_path)} の修復が完了しました")
        except Exception as e:
            QMessageBox.warning(self, "修復失敗", str(e))

    def run_mp4_convert(self, file_path=None):
        # MP4変換処理（簡易）
        if not file_path:
            return
        save_path, _ = QFileDialog.getSaveFileName(self, "変換後の保存先を指定", os.path.splitext(file_path)[0] + "_converted" + os.path.splitext(file_path)[1], "動画ファイル (*.*)")
        if not save_path:
            return
        try:
            from component.ffmpeg_util import convert_mp4
            convert_mp4(file_path, save_path)
            QMessageBox.information(self, "変換完了", f"{os.path.basename(file_path)} の変換が完了しました")
        except Exception as e:
            QMessageBox.warning(self, "変換失敗", str(e))

    def run_mp4_digital_repair(self, file_path=None):
        # デジタル修復・AI超解像（簡易）
        if not file_path:
            return
        save_path, _ = QFileDialog.getSaveFileName(self, "高画質化後の保存先を指定", os.path.splitext(file_path)[0] + "_aiup_gfpgan" + os.path.splitext(file_path)[1], "動画ファイル (*.*)")
        if not save_path:
            return
        try:
            from component.ai_tools import digital_repair
            digital_repair(file_path, save_path)
            QMessageBox.information(self, "AI超解像完了", f"{os.path.basename(file_path)} のAI超解像が完了しました")
        except Exception as e:
            QMessageBox.warning(self, "AI超解像失敗", str(e))
