# -*- coding: utf-8 -*-
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
import os
import queue
import threading
import logging
from PyQt5.QtWidgets import (QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QFileDialog, QMessageBox, QScrollArea, QProgressBar, QDialog, QGridLayout, QDialogButtonBox, QCheckBox, QProgressDialog, QGroupBox, QListView, QAbstractItemView, QStyledItemDelegate, QApplication, QStackedWidget, QSizePolicy, QComboBox)
from PyQt5.QtGui import QPixmap, QImage, QIcon
from PyQt5.QtCore import Qt, QSize, QTimer, QAbstractListModel, QModelIndex, QVariant, pyqtSignal
from queue import Queue

from component.duplicate_finder import find_duplicates_in_folder, get_image_and_video_files
from component.thumbnail.thumbnail_util import (
    start_thumbnail_workers, pil_image_to_qpixmap, load_thumb_cache, save_thumb_cache
)
from component.utils.file_util import move_to_trash, get_folder_state
from component.face_grouping import group_by_face_and_move, get_face_groups
from component.broken_checker import check_broken_videos
from component.ffmpeg_util import show_mp4_tool_dialog, repair_mp4, convert_mp4
from component.ai.ai_tools import digital_repair
from component.ui_util import show_detail_dialog, show_compare_dialog, add_thumbnail_widget, update_progress, drag_enter_event, drop_event, delete_selected_dialog, get_save_file_path, show_info_dialog, show_warning_dialog, show_question_dialog
from component.group_ui import create_duplicate_group_ui, show_face_grouping_dialog, move_selected_files_to_folder, show_broken_video_dialog
from component.thumbnail.thumbnail_util import ThumbnailCache, get_thumbnail_for_file

# オプションインポート（エラー回避）
try:
    from .gui_thumbnail import ThumbnailListModel
    from .gui_dialogs import show_progress_dialog
    from .gui_utils import ThumbnailDelegate
    from .mode_selection_dialog import ModeSelectionDialog
except ImportError:
    ThumbnailListModel = None  # type: ignore
    show_progress_dialog = None  # type: ignore
    ThumbnailDelegate = None  # type: ignore
    ModeSelectionDialog = None  # type: ignore

print("DEBUG: gui_main.py loaded from", __file__)

# --- ここにDuplicateFinderGUIクラス本体を移植 ---

class DuplicateFinderGUI(QWidget):
    def update_thumbnail_ui(self, file_path: str, pil_image):
        """
        サムネイルワーカーからのUI更新要求を受けて、該当ファイルのサムネイルボタン等のUI部品を更新する。
        file_path: サムネイル対象ファイルの絶対パス
        pil_image: PIL.Image.Image オブジェクト（Noneの場合は更新しない）
        """
        import os
        from PyQt5.QtCore import QTimer
        norm_path = os.path.abspath(os.path.normpath(file_path))
        btn = self.thumb_widget_map.get(norm_path)
        if btn is None:
            print(f"[update_thumbnail_ui] No widget for {norm_path}")
            return
        if pil_image is None:
            print(f"[update_thumbnail_ui] No image for {norm_path}")
            return
        def do_update():
            try:
                from component.thumbnail.thumbnail_util import pil_image_to_qpixmap
                pix = pil_image_to_qpixmap(pil_image)
                btn.setIcon(QIcon(pix))
                btn.setIconSize(QSize(240, 135))
                btn.setText("")
                btn.repaint()
            except Exception as e:
                print(f"[update_thumbnail_ui] Exception: {e}")
        QTimer.singleShot(0, do_update)
    update_ui_signal = pyqtSignal(object, object, object, object, object)

    def __init__(self, parent=None):
        super(DuplicateFinderGUI, self).__init__(parent)
        self.current_page = 0
        self.groups_per_page = 50
        self.duplicate_groups = []
        self.group_widgets = []
        self.thumb_queue = Queue()
        self.thumb_cache = load_thumb_cache()
        self.thumb_widget_map = {}
        self.thumb_manager = None
        self.worker = None
        self.cancel_requested = False
        self.selected_paths = set()
        self.last_folder_state = None
        self.current_view_mode = 0
        self.last_use_advanced = False
        self.last_file_list = set()
        self.update_ui_signal.connect(self.update_ui)
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("Duplicate Finder")
        self.resize(900, 700)
        layout = QVBoxLayout(self)
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
        # --- 上部: 機能ボタン横並び ---
        btn_hbox = QHBoxLayout()
        self.mp4_tool_btn = QPushButton("MP4修復")
        self.mp4_tool_btn.setStyleSheet("font-size:16px;color:#ffb300;border:2px solid #ffb300;border-radius:8px;padding:8px;")
        self.mp4_tool_btn.clicked.connect(self.show_mp4_tool_dialog)
        btn_hbox.addWidget(self.mp4_tool_btn)
        self.clear_thumb_cache_btn = QPushButton("サムネキャッシュ削除")
        self.clear_thumb_cache_btn.setStyleSheet("font-size:14px;color:#fff;background:#444;border:1px solid #00ffe7;border-radius:6px;padding:4px 8px;")
        self.clear_thumb_cache_btn.clicked.connect(self.clear_thumb_cache)
        btn_hbox.addWidget(self.clear_thumb_cache_btn)
        self.reload_btn = QPushButton("再読み込み")
        self.reload_btn.setStyleSheet("font-size:14px;color:#fff;background:#222;border:1px solid #444;border-radius:6px;padding:4px 8px;")
        self.reload_btn.clicked.connect(self.reload_folder)
        btn_hbox.addWidget(self.reload_btn)
        self.cancel_btn = QPushButton("キャンセル")
        self.cancel_btn.setStyleSheet("font-size:14px;color:#fff;background:#c00;border:1px solid #f00;border-radius:6px;padding:4px 8px;")
        self.cancel_btn.clicked.connect(self.request_cancel)
        self.cancel_btn.setEnabled(False)
        btn_hbox.addWidget(self.cancel_btn)
        self.result_fullscreen_btn = QPushButton("結果のみ表示")
        self.result_fullscreen_btn.setStyleSheet("font-size:14px;color:#fff;background:#222;border:1px solid #00ffe7;border-radius:6px;padding:4px 8px;")
        self.result_fullscreen_btn.setCheckable(True)
        self.result_fullscreen_btn.toggled.connect(self.toggle_result_fullscreen)
        btn_hbox.addWidget(self.result_fullscreen_btn)
        layout.addLayout(btn_hbox)
        # --- フォルダラベル・選択ボタン ---
        self.folder_label = QLabel("フォルダ未選択")
        self.folder_label.setStyleSheet(font_css)
        layout.addWidget(self.folder_label)
        self.select_btn = QPushButton("[ フォルダ選択 ]")
        self.select_btn.setStyleSheet("font-size:17px;font-weight:bold;background:transparent;color:#00ffe7;border:2px solid #00ffe7;")
        self.select_btn.clicked.connect(self.selectFiles)
        layout.addWidget(self.select_btn)
        # --- サムネイル/グループ表示用スクロールエリア ---
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout()
        self.content_widget.setLayout(self.content_layout)
        self.scroll_area.setWidget(self.content_widget)
        layout.addWidget(self.scroll_area)
        # --- ページングUI ---
        self.page_hbox = QHBoxLayout()
        self.prev_page_btn = QPushButton("前へ")
        self.next_page_btn = QPushButton("次へ")
        self.page_label = QLabel("")
        self.prev_page_btn.clicked.connect(self.prev_page)
        self.next_page_btn.clicked.connect(self.next_page)
        self.groups_per_page_combo = QComboBox()
        self.groups_per_page_combo.addItems(["20", "50", "100"])
        self.groups_per_page_combo.setCurrentText(str(self.groups_per_page))
        self.groups_per_page_combo.setFixedWidth(60)
        self.groups_per_page_combo.currentTextChanged.connect(self.on_groups_per_page_changed)
        self.page_hbox.addWidget(self.prev_page_btn)
        self.page_hbox.addWidget(self.page_label)
        self.page_hbox.addWidget(self.next_page_btn)
        self.page_hbox.addWidget(QLabel("/ ページあたり"))
        self.page_hbox.addWidget(self.groups_per_page_combo)
        self.page_hbox.addWidget(QLabel("件"))
        layout.addLayout(self.page_hbox)
        self.setLayout(layout)
        self.selected_paths = set()
        # --- 初期表示で重複チェックを呼ばない（フォルダ選択後のみ呼ぶ） ---

    def on_thumb_update(self, path, pil_image):
        import os
        from PyQt5.QtCore import QTimer
        norm_path = os.path.abspath(os.path.normpath(path))

        def update_ui():
            btn = self.thumb_widget_map.get(norm_path)
            if btn and pil_image:
                try:
                    from component.thumbnail.thumbnail_util import pil_image_to_qpixmap
                    pix = pil_image_to_qpixmap(pil_image)
                    btn.setIcon(QIcon(pix))
                    btn.setIconSize(QSize(180, 180))
                    btn.setText("")
                    btn.repaint()
                    print(f"[on_thumb_update] Updated: {norm_path}")
                except Exception as e:
                    print(f"[on_thumb_update] Error: {e}")

        QTimer.singleShot(0, update_ui)

    def selectFiles(self):
        folder = QFileDialog.getExistingDirectory(self, "フォルダを選択")
        if folder:
            self.folder_label.setText(f"選択フォルダ: {folder}")
            self.load_thumb_cache(folder)
            self._show_action_dialog()
    
    def _show_action_dialog(self):
        """重複検査か顔グループ化を選択"""
        dialog = QDialog(self)
        dialog.setWindowTitle("処理選択")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("実行する処理を選択してください:"))
        
        def on_dup():
            dialog.accept()
            self.find_duplicates()
        
        def on_face():
            dialog.accept()
            self.face_grouping_and_move()
        
        dup_btn = QPushButton("重複検査")
        dup_btn.clicked.connect(on_dup)
        layout.addWidget(dup_btn)
        
        face_btn = QPushButton("顔グループ化")
        face_btn.clicked.connect(on_face)
        layout.addWidget(face_btn)
        
        cancel_btn = QPushButton("キャンセル")
        cancel_btn.clicked.connect(dialog.reject)
        layout.addWidget(cancel_btn)
        
        dialog.exec_()
    
    def load_thumb_cache(self, folder=None):
        self.thumb_cache = load_thumb_cache(folder)

    def save_thumb_cache(self, folder=None):
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
        while True:
            try:
                file = self.fileQueue.get_nowait()
            except queue.Empty:
                break
            print(f"[DEBUG] detectDuplicates: processing {file}")
            # ...ファイル処理コード...
            self.fileQueue.task_done()
        self.fileQueue.join()  # 全てのtask_done()完了まで待つ
        print("[DEBUG] detectDuplicates: end")

    def runDetection(self):
        # 検出実行
        self.progressBar.setValue(0)
        # ...検出実行コード...

    def closeEvent(self, a0):  # type: ignore[override]
        if self.worker and hasattr(self.worker, 'isRunning') and callable(getattr(self.worker, 'isRunning', None)) and self.worker.isRunning():  # type: ignore[attr-defined]
            reply = QMessageBox.question(self, 'Message', 'Detection is still running. Do you really want to exit?', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                if a0 is not None:
                    a0.accept()  # type: ignore[union-attr]
            else:
                if a0 is not None:
                    a0.ignore()  # type: ignore[union-attr]
        else:
            if a0 is not None:
                a0.accept()  # type: ignore[union-attr]

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

    def dragEnterEvent(self, a0):  # type: ignore[override]
        # ドラッグ＆ドロップでファイル追加・移動（UIユーティリティに移譲）
        drag_enter_event(a0)

    def dropEvent(self, a0):  # type: ignore[override]
        # ドロップされたファイルを検出対象に追加 or 移動（UIユーティリティに移譲）
        drop_event(a0, self.processFiles)

    def request_cancel(self):
        if self.worker and hasattr(self.worker, 'isRunning') and callable(getattr(self.worker, 'isRunning', None)) and self.worker.isRunning():  # type: ignore[attr-defined]
            if hasattr(self.worker, 'quit'):
                self.worker.quit()  # type: ignore[attr-defined]
            if hasattr(self.worker, 'wait') and callable(getattr(self.worker, 'wait', None)):
                if not self.worker.wait(3000):  # type: ignore[attr-defined]
                    if hasattr(self.worker, 'terminate'):
                        self.worker.terminate()  # type: ignore[attr-defined]
                        self.worker.wait(1000)  # type: ignore[attr-defined]
            self.cancel_btn.setEnabled(False)
            QMessageBox.information(self, "キャンセル", "処理をキャンセルしました")

    def find_duplicates(self):
        """改善された重複検出処理"""
        folder = self._get_selected_folder()
        if not folder:
            QMessageBox.warning(self, "警告", "フォルダを選択してください")
            return
        
        # モード選択ダイアログ
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QRadioButton, QDialogButtonBox, QLabel, QGroupBox
        
        dialog = QDialog(self)
        dialog.setWindowTitle("重複検出設定")
        layout = QVBoxLayout(dialog)
        
        # 検出精度選択
        mode_group = QGroupBox("検出精度")
        mode_layout = QVBoxLayout()
        fast_mode = QRadioButton("高速モード (従来方式)")
        fast_mode.setChecked(True)
        mode_layout.addWidget(fast_mode)
        advanced_mode = QRadioButton("高精度モード (動画内容解析)")
        mode_layout.addWidget(advanced_mode)
        mode_group.setLayout(mode_layout)
        layout.addWidget(mode_group)
        
        # ファイル種類選択
        type_group = QGroupBox("ファイル種類")
        type_layout = QVBoxLayout()
        both_type = QRadioButton("動画と画像の両方")
        both_type.setChecked(True)
        type_layout.addWidget(both_type)
        video_only = QRadioButton("動画のみ")
        type_layout.addWidget(video_only)
        image_only = QRadioButton("画像のみ")
        type_layout.addWidget(image_only)
        type_group.setLayout(type_layout)
        layout.addWidget(type_group)
        
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        
        if dialog.exec_() != QDialog.Accepted:
            return
        
        use_advanced = advanced_mode.isChecked()
        self.last_use_advanced = use_advanced
        
        file_type = 'both'
        if video_only.isChecked():
            file_type = 'video'
        elif image_only.isChecked():
            file_type = 'image'
        
        self._execute_duplicate_finding(folder, use_advanced, file_type)
        
    def _execute_duplicate_finding(self, folder, use_advanced=False, file_type='both'):
        """重複検出の実際の実行処理"""
        from PyQt5.QtCore import QThread, pyqtSignal
        import time
        
        class DuplicateWorker(QThread):
            progress = pyqtSignal(int, int)
            finished = pyqtSignal(list)
            error = pyqtSignal(str)
            
            def __init__(self, folder_path, use_advanced):
                super().__init__()
                self.folder_path = folder_path
                self.use_advanced = use_advanced
            
            def run(self):
                try:
                    from component.duplicate_finder import find_duplicates_in_folder
                    print(f"[DEBUG] Worker starting: use_advanced={self.use_advanced}")
                    groups, _ = find_duplicates_in_folder(self.folder_path, progress_callback=self.progress.emit, use_advanced=self.use_advanced)
                    print(f"[DEBUG] Worker finished: {len(groups)} groups")
                    self.finished.emit(groups)
                except Exception as e:
                    print(f"[ERROR] Worker error: {e}")
                    import traceback
                    traceback.print_exc()
                    self.error.emit(str(e))
        
        progress_dialog = QDialog(self)
        progress_dialog.setWindowTitle("重複検出中")
        progress_dialog.setMinimumSize(500, 200)
        layout = QVBoxLayout(progress_dialog)
        
        status_label = QLabel("処理中...")
        status_label.setStyleSheet("font-size:16px;font-weight:bold;")
        layout.addWidget(status_label)
        
        progress_bar = QProgressBar()
        progress_bar.setRange(0, 100)
        layout.addWidget(progress_bar)
        
        elapsed_label = QLabel("経過時間: 0秒")
        layout.addWidget(elapsed_label)
        
        eta_label = QLabel("予測終了時間: 計算中...")
        layout.addWidget(eta_label)
        
        remain_label = QLabel("残り: 計算中...")
        layout.addWidget(remain_label)
        
        start_time = time.time()
        
        def update_progress(current, total):
            progress_bar.setValue(int(current/total*100))
            elapsed = time.time() - start_time
            elapsed_label.setText(f"経過時間: {int(elapsed)}秒")
            
            if current > 0:
                eta = (elapsed / current) * (total - current)
                eta_label.setText(f"予測終了時間: {int(eta)}秒")
                remain_label.setText(f"残り: {total - current}件")
        
        progress_dialog.show()
        
        self.worker = DuplicateWorker(folder, use_advanced)
        self.worker.progress.connect(update_progress)
        self.worker.finished.connect(lambda groups: self._on_duplicates_found(groups, file_type))
        
        def on_error(err):
            QMessageBox.critical(self, "エラー", f"重複検出エラー: {err}")
        
        self.worker.error.connect(on_error)  # type: ignore[arg-type]
        self.worker.finished.connect(lambda: progress_dialog.close())  # type: ignore[arg-type]
        self.worker.error.connect(lambda: progress_dialog.close())  # type: ignore[arg-type]
        self.worker.finished.connect(lambda: self.cancel_btn.setEnabled(False))  # type: ignore[arg-type]
        self.worker.error.connect(lambda: self.cancel_btn.setEnabled(False))  # type: ignore[arg-type]
        self.cancel_btn.setEnabled(True)
        self.worker.start()
    
    def _on_duplicates_found(self, groups, file_type='both'):
        """重複検出完了時の処理"""
        video_exts = (".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm", ".mpg", ".mpeg", ".3gp")
        image_exts = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff")
        
        video_groups = []
        image_groups = []
        error_groups = []
        
        for group in groups:
            if not group:
                continue
            first_ext = os.path.splitext(group[0])[1].lower()
            if first_ext in video_exts:
                video_groups.append(group)
            elif first_ext in image_exts:
                image_groups.append(group)
            else:
                error_groups.append(group)
        
        if file_type == 'video':
            self.duplicate_groups = video_groups
        elif file_type == 'image':
            self.duplicate_groups = image_groups
        else:
            self.duplicate_groups = video_groups + image_groups + error_groups
        
        # ファイルリストを保存
        self.last_file_list = set()
        for group in self.duplicate_groups:
            self.last_file_list.update(group)
        
        self.current_page = 0
        self.show_current_page()
    
    def _execute_incremental_finding(self, folder, new_files):
        """差分検出：新規ファイルのみ処理"""
        from PyQt5.QtCore import QThread, pyqtSignal
        import time
        
        class IncrementalWorker(QThread):
            progress = pyqtSignal(int, int)
            finished = pyqtSignal(list)
            error = pyqtSignal(str)
            
            def __init__(self, folder_path, new_files, existing_groups, use_advanced):
                super().__init__()
                self.folder_path = folder_path
                self.new_files = new_files
                self.existing_groups = existing_groups
                self.use_advanced = use_advanced
            
            def run(self):
                try:
                    from component.duplicate_finder import get_image_phash, get_video_semantic_hash
                    from component.thumbnail.thumbnail_util import FastCache
                    import imagehash
                    
                    cache = FastCache()
                    new_hashes = []
                    
                    # 新規ファイルのハッシュ計算
                    for idx, f in enumerate(self.new_files):
                        ext = os.path.splitext(f)[1].lower()
                        if ext in (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff"):
                            h = get_image_phash(f, cache=cache)
                        else:
                            h = get_video_semantic_hash(f, cache)
                        new_hashes.append((f, h))
                        self.progress.emit(idx+1, len(self.new_files))
                    
                    # 既存グループとマッチング
                    merged_groups = list(self.existing_groups)
                    unmatched = []
                    
                    for new_file, new_hash in new_hashes:
                        if new_hash is None:
                            continue
                        
                        matched = False
                        for group in merged_groups:
                            if not group:
                                continue
                            # グループの最初のファイルと比較
                            first_file = group[0]
                            first_hash_str = cache.get_phash(first_file)
                            if first_hash_str:
                                try:
                                    first_hash = imagehash.hex_to_hash(first_hash_str)
                                    if abs(new_hash - first_hash) <= 8:
                                        group.append(new_file)
                                        matched = True
                                        break
                                except:
                                    pass
                        
                        if not matched:
                            unmatched.append((new_file, new_hash))
                    
                    # 未マッチファイル同士でグループ化
                    for i, (f1, h1) in enumerate(unmatched):
                        if h1 is None:
                            continue
                        new_group = [f1]
                        for j, (f2, h2) in enumerate(unmatched):
                            if i != j and h2 is not None:
                                try:
                                    if abs(h1 - h2) <= 8:
                                        new_group.append(f2)
                                except:
                                    pass
                        if len(new_group) > 1:
                            merged_groups.append(new_group)
                    
                    self.finished.emit(merged_groups)
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    self.error.emit(str(e))
        
        progress_dialog = QDialog(self)
        progress_dialog.setWindowTitle("差分検出中")
        progress_dialog.setMinimumSize(500, 200)
        layout = QVBoxLayout(progress_dialog)
        
        status_label = QLabel(f"新規ファイル {len(new_files)}件を処理中...")
        status_label.setStyleSheet("font-size:16px;font-weight:bold;")
        layout.addWidget(status_label)
        
        progress_bar = QProgressBar()
        progress_bar.setRange(0, 100)
        layout.addWidget(progress_bar)
        
        elapsed_label = QLabel("経過時間: 0秒")
        layout.addWidget(elapsed_label)
        
        start_time = time.time()
        
        def update_progress(current, total):
            progress_bar.setValue(int(current/total*100))
            elapsed = time.time() - start_time
            elapsed_label.setText(f"経過時間: {int(elapsed)}秒")
        
        progress_dialog.show()
        
        self.worker = IncrementalWorker(folder, new_files, self.duplicate_groups, self.last_use_advanced)
        self.worker.progress.connect(update_progress)
        self.worker.finished.connect(self._on_duplicates_found)
        
        def on_inc_error(err):
            QMessageBox.critical(self, "エラー", f"差分検出エラー: {err}")
        
        self.worker.error.connect(on_inc_error)  # type: ignore[arg-type]
        self.worker.finished.connect(lambda: progress_dialog.close())  # type: ignore[arg-type]
        self.worker.error.connect(lambda: progress_dialog.close())  # type: ignore[arg-type]
        self.worker.start()

    def update_ui(self, duplicates, folder, elapsed_time=None, eta_time=None, remain_count=None):
        print("[DEBUG] update_ui: called (first line)")
        try:
            self.duplicate_groups = duplicates or []
            self.current_page = 0
            self.show_current_page(elapsed_time, eta_time, remain_count)
        except Exception as e:
            print(f"[DEBUG] update_ui: outer exception: {e}")

    def show_current_page(self, elapsed_time=None, eta_time=None, remain_count=None):
        # デバッグ情報
        print(f"[DEBUG] 総グループ数: {len(self.duplicate_groups)}")
        print(f"[DEBUG] 現在ページ: {self.current_page}, 1ページあたり: {self.groups_per_page}")
        
        # ページ切り替え時にキューをクリア
        from component.thumbnail.thumbnail_util import clear_queue
        clear_queue(self.thumb_queue)
        self.clear_content()
        
        start = self.current_page * self.groups_per_page
        end = start + self.groups_per_page
        page_groups = self.duplicate_groups[start:end]
        
        print(f"[DEBUG] 表示範囲: {start}-{end}, 実際のグループ数: {len(page_groups)}")
        if not page_groups:
            self.content_layout.addWidget(QLabel("重複ファイルは見つかりませんでした"))
            return
        self.group_widgets = []
        self.thumb_widget_map = {}
        for i, group in enumerate(page_groups):
            global_index = start + i
            is_error_group = False
            if global_index == len(self.duplicate_groups) - 1:
                if isinstance(group, list) and len(group) > 0:
                    try:
                        is_error_group = all((get_thumbnail_for_file(f, (180, 180), cache=self.thumb_cache) is None) for f in group)
                    except Exception:
                        is_error_group = False
            try:
                from component.group_ui import create_error_group_ui
                # 型安全な引数セット: 必要な引数のみ渡す
                # thumb_cacheだけは型安全に渡す（ThumbnailCache型のみ渡す）
                group_ui_kwargs = dict(
                    thumb_cache=self.thumb_cache if isinstance(self.thumb_cache, ThumbnailCache) else None,
                    defer_queue=self.thumb_queue,
                    thumb_widget_map=self.thumb_widget_map,
                )
                # 追加情報が必要な場合のみ渡す
                if elapsed_time is not None:
                    group_ui_kwargs['elapsed_time'] = elapsed_time
                if eta_time is not None:
                    group_ui_kwargs['eta_time'] = eta_time
                if remain_count is not None:
                    group_ui_kwargs['remain_count'] = remain_count

                if is_error_group:
                    group_box = create_error_group_ui(
                        group,
                        get_thumbnail_for_file,
                        show_detail_dialog,
                        self.delete_single_file,
                        **group_ui_kwargs
                    )
                    group_box.setStyleSheet("margin-bottom: 24px; border: 2px solid #ff4444; border-radius: 12px; padding: 8px;")
                else:
                    from component.group_ui_prime import create_prime_group_ui
                    group_box = create_prime_group_ui(
                        group,
                        get_thumbnail_for_file,
                        show_detail_dialog,
                        self.delete_single_file,
                        show_compare_dialog,
                        thumb_cache=self.thumb_cache if isinstance(self.thumb_cache, ThumbnailCache) else None,
                        defer_queue=None,
                        thumb_widget_map=self.thumb_widget_map,
                        parent=self,
                        elapsed_time=elapsed_time,
                        eta_time=eta_time,
                        remain_count=remain_count
                    )
                    group_box.setStyleSheet("margin-bottom: 24px; border: 2px solid #00ffe7; border-radius: 12px; padding: 8px;")
                self.content_layout.addWidget(group_box)
            except Exception as e:
                print(f"[DEBUG] show_current_page: group UI exception: {e}")

        # バッチでサムネイル要求（現在ページのみ）
        all_paths = [f for group in page_groups for f in group]
        print(f"[DEBUG] サムネイル生成対象: {len(all_paths)}ファイル")
        if self.thumb_manager is None:
            from component.thumbnail.thumbnail_util import VirtualThumbnailManager
            self.thumb_manager = VirtualThumbnailManager(self)
        
        # サムネイル生成進捗ポップアップ
        if len(all_paths) > 10:
            import time
            thumb_dialog = QDialog(self)
            thumb_dialog.setWindowTitle("サムネイル生成中")
            thumb_dialog.setMinimumSize(500, 150)
            thumb_layout = QVBoxLayout(thumb_dialog)
            
            thumb_status = QLabel(f"{len(all_paths)}件のサムネイルを生成中...")
            thumb_status.setStyleSheet("font-size:16px;font-weight:bold;")
            thumb_layout.addWidget(thumb_status)
            
            thumb_progress = QProgressBar()
            thumb_progress.setRange(0, 100)
            thumb_layout.addWidget(thumb_progress)
            
            thumb_elapsed = QLabel("経過時間: 0秒")
            thumb_layout.addWidget(thumb_elapsed)
            
            thumb_eta = QLabel("予測終了時間: 計算中...")
            thumb_layout.addWidget(thumb_eta)
            
            thumb_remain = QLabel("残り: 計算中...")
            thumb_layout.addWidget(thumb_remain)
            
            thumb_start = time.time()
            
            def update_thumb_progress(current, total):
                thumb_progress.setValue(int(current/total*100))
                elapsed = time.time() - thumb_start
                thumb_elapsed.setText(f"経過時間: {int(elapsed)}秒")
                
                if current > 0:
                    eta = (elapsed / current) * (total - current)
                    thumb_eta.setText(f"予測終了時間: {int(eta)}秒")
                    thumb_remain.setText(f"残り: {total - current}件")
                
                if current >= total:
                    QTimer.singleShot(500, lambda: thumb_dialog.close())  # type: ignore[arg-type]
            
            thumb_dialog.show()
            self.thumb_manager.load_visible_batch(all_paths, update_thumb_progress)
        else:
            self.thumb_manager.load_visible_batch(all_paths)
        # ページラベル・ボタン状態
        total_pages = max(1, (len(self.duplicate_groups) + self.groups_per_page - 1) // self.groups_per_page)
        self.page_label.setText(f"{self.current_page + 1} / {total_pages}")
        self.prev_page_btn.setEnabled(self.current_page > 0)
        self.next_page_btn.setEnabled((self.current_page + 1) * self.groups_per_page < len(self.duplicate_groups))
        self.content_widget.adjustSize()

    def prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            self.show_current_page()

    def next_page(self):
        total_pages = max(1, (len(self.duplicate_groups) + self.groups_per_page - 1) // self.groups_per_page)
        if self.current_page + 1 < total_pages:
            self.current_page += 1
            self.show_current_page()

    def _get_selected_folder(self):
        """選択されたフォルダパスを取得"""
        folder_text = self.folder_label.text()
        folder = folder_text.replace("選択フォルダ: ", "") if "選択フォルダ: " in folder_text else folder_text
        return folder if folder and folder != "フォルダ未選択" else None

    def check_folder_update(self):
        folder = self._get_selected_folder()
        if not folder:
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
        folder = self._get_selected_folder()
        if not folder:
            QMessageBox.warning(self, "警告", "フォルダを選択してください")
            return
        
        # 現在のファイルリストを取得
        current_files = set(get_image_and_video_files(folder))
        
        # 差分計算
        added_files = current_files - self.last_file_list
        deleted_files = self.last_file_list - current_files
        
        if not added_files and not deleted_files:
            QMessageBox.information(self, "情報", "変更はありません")
            return
        
        # 削除されたファイルをグループから除外
        if deleted_files:
            for group in self.duplicate_groups:
                for f in list(deleted_files):
                    if f in group:
                        group.remove(f)
            self.duplicate_groups = [g for g in self.duplicate_groups if len(g) > 1]
        
        # ファイルリストを更新
        self.last_file_list = current_files
        
        # 追加ファイルがあれば追加処理
        if added_files:
            self._execute_incremental_finding(folder, list(added_files))
            return
        
        # UI更新
        self.show_current_page()
        QMessageBox.information(self, "完了", f"削除: {len(deleted_files)}件")

    def clear_thumb_cache(self):
        reply = QMessageBox.question(self, "確認", "サムネイルキャッシュを削除しますか？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            try:
                from component.thumbnail.thumbnail_util import FastCache
                cache = FastCache()
                cache.clear()
                QMessageBox.information(self, "完了", "サムネイルキャッシュを削除しました。")
            except Exception as e:
                QMessageBox.critical(self, "エラー", f"キャッシュ削除エラー: {e}")
    
    def face_grouping_and_move(self):
        """顔グループ化処理"""
        folder = self._get_selected_folder()
        if not folder:
            QMessageBox.warning(self, "警告", "フォルダを選択してください")
            return
        
        try:
            from component.face_grouping import group_by_face_and_move
            out_dir = os.path.join(folder, "face_groups")
            group_by_face_and_move(folder, out_dir)  # type: ignore[call-arg]
            QMessageBox.information(self, "完了", "顔グループ化が完了しました")
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"顔グループ化エラー: {e}")
    
    def show_mp4_tool_dialog(self):
        """MP4修復ツール表示"""
        try:
            from component.ffmpeg_util import show_mp4_tool_dialog
            show_mp4_tool_dialog(self)
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"MP4ツールエラー: {e}")

    def dismiss_group(self, group):
        if group in self.duplicate_groups:
            self.duplicate_groups.remove(group)
    
    def delete_single_file(self, file_path):
        try:
            move_to_trash(file_path)
            for group in self.duplicate_groups:
                if file_path in group:
                    group.remove(file_path)
            self.duplicate_groups = [g for g in self.duplicate_groups if len(g) > 1]
            QMessageBox.information(self, "完了", f"ファイルをゴミ箱に移動しました:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"ファイルの削除中にエラーが発生しました:\n{str(e)}")

    def clear_content(self):
        if not hasattr(self, 'content_layout'):
            return
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item:
                widget = item.widget()
                if widget:
                    widget.setParent(None)
                    widget.deleteLater()
        QApplication.processEvents()
        self.group_widgets = []
        self.thumb_widget_map.clear()
        import gc
        gc.collect()

    def toggle_result_fullscreen(self, checked):
        widgets = [
            self.folder_label, self.select_btn
        ]
        for w in widgets:
            w.setVisible(not checked)
        self.result_fullscreen_btn.setText("元に戻す" if checked else "結果のみ表示")
        if checked:
            # 全画面表示時はグループ表示エリアを拡大
            self.scroll_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.scroll_area.setMinimumSize(800, 600)
        else:
            # 通常時のサイズポリシーに戻す
            self.scroll_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.scroll_area.setMinimumSize(0, 0)
        self.adjustSize()

    def on_groups_per_page_changed(self, text):
        try:
            self.groups_per_page = int(text)
        except Exception:
            self.groups_per_page = 50
        self.current_page = 0
        self.show_current_page()
