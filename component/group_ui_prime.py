# Prime Video風カードUI
from PyQt5.QtWidgets import QGroupBox, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QWidget, QGridLayout, QSizePolicy
from PyQt5.QtCore import QSize, Qt
from PyQt5.QtGui import QPixmap, QIcon, QFont
import os
from component.thumbnail.thumbnail_util import get_thumbnail_for_file, pil_image_to_qpixmap, get_no_thumbnail_image, ThumbnailCache
from typing import Optional

def create_prime_group_ui(
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
    
    group_box = QGroupBox()
    group_box.setStyleSheet("""
        QGroupBox {
            background: #141414;
            border: none;
            margin: 0px;
            padding: 5px;
        }
    """)
    
    # ヘッダー
    header_label = QLabel(f"重複グループ（{len(group)}ファイル）")
    header_label.setStyleSheet("font-size:18px;color:#fff;font-weight:bold;margin-bottom:10px;")
    
    # グリッド
    grid = QGridLayout()
    grid.setHorizontalSpacing(3)
    grid.setVerticalSpacing(3)
    grid.setContentsMargins(5, 5, 5, 5)
    
    max_col = 4
    
    for idx, f in enumerate(group):
        norm_path = os.path.abspath(os.path.normpath(f))
        
        # カード全体（情報部分に十分なスペース確保）
        file_card = QWidget()
        file_card.setFixedSize(355, 300)
        file_card.setContentsMargins(0, 0, 0, 0)
        file_card.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        file_card.setStyleSheet("""
            QWidget {
                background-color: #222;
                border-radius: 6px;
                border: 2px solid transparent;
            }
            QWidget:hover {
                background-color: #333;
                border: 2px solid white;
            }
        """)
        
        card_hbox = QHBoxLayout()
        card_hbox.setSpacing(8)
        card_hbox.setContentsMargins(4, 4, 4, 4)
        
        # サムネイル（16:9比率、コンパクト）
        thumb_btn = QPushButton()
        thumb_btn.setFixedSize(150, 290)
        thumb_btn.setIcon(QIcon(pil_image_to_qpixmap(get_no_thumbnail_image((150, 290)))))
        thumb_btn.setIconSize(QSize(150, 290))
        thumb_btn.setStyleSheet("""
            QPushButton {
                background-color: #333;
                border: none;
                border-radius: 0px;
                padding: 0px;
                margin: 0px;
            }
        """)
        def make_detail_cb(file_path):
            def show_detail():
                detail_cb(parent, file_path)
            return show_detail
        
        thumb_btn.clicked.connect(make_detail_cb(f))
        
        # 情報部分（適切なサイズ制約とレイアウト配分）
        info_widget = QWidget()

        
        # ファイル名（完全なパス表示）
        fname = os.path.basename(f)
        # 相対パスで表示（最後の4階層）
        path_parts = f.replace('\\', '/').split('/')
        if len(path_parts) >= 4:
            display_name = '/'.join(path_parts[-4:])
        else:
            display_name = '/'.join(path_parts)
        name_label = QLabel(display_name)
        name_label.setStyleSheet("""
            QLabel {
                font-size: 10px;
                font-weight: bold;
                color: #ffffff;
                background-color: #34495e;
                padding: 7px;
                border-radius: 3px;
                line-height: 1.3;
            }
        """)
        name_label.setFixedHeight(90)
        name_label.setMinimumHeight(60)
        name_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        name_label.setWordWrap(True)
        name_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        name_label.setToolTip(f)
        
        # 動画時間（動画のみ表示）
        duration_label = None
        if f.lower().endswith(('.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm')):
            try:
                import cv2
                cap = cv2.VideoCapture(f)
                fps = cap.get(cv2.CAP_PROP_FPS)
                frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
                if fps > 0:
                    duration_seconds = int(frame_count / fps)
                    hours = duration_seconds // 3600
                    minutes = (duration_seconds % 3600) // 60
                    seconds = duration_seconds % 60
                    duration_text = f"⏱ {hours:02d}:{minutes:02d}:{seconds:02d}"
                else:
                    duration_text = "⏱ 不明"
                cap.release()
            except:
                duration_text = "⏱ 不明"
            
            duration_label = QLabel(duration_text)
            duration_label.setStyleSheet("""
                QLabel {
                    font-size: 12px;
                    font-weight: bold;
                    color: #ffffff;
                    background-color: #34495e;
                    padding: 7px;
                    border-radius: 3px;
                }
            """)
            duration_label.setFixedHeight(60)
            duration_label.setMinimumHeight(35)
            duration_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            duration_label.setAlignment(Qt.AlignCenter)
        
        # ファイルサイズ（見えるサイズ）
        try:
            size_mb = os.path.getsize(f) / (1024 * 1024)
            size_text = f"📁 {size_mb:.1f} MB"
        except:
            size_text = "📁 不明"
        
        size_label = QLabel(size_text)
        size_label.setStyleSheet("""
            QLabel {
                font-size: 12px;
                font-weight: bold;
                color: #ffffff;
                background-color: #34495e;
                padding: 7px;
                border-radius: 3px;
            }
        """)
        size_label.setFixedHeight(60)
        size_label.setMinimumHeight(35)
        size_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        size_label.setAlignment(Qt.AlignCenter)
        
        # ボタン（大きく押しやすく）
        btn_hbox = QHBoxLayout()
        btn_hbox.setSpacing(5)
        btn_hbox.setContentsMargins(0, 0, 0, 0)
        
        open_btn = QPushButton("📂 開く")
        open_btn.setFixedHeight(65)
        open_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        open_btn.setStyleSheet("""
            QPushButton {
                font-size: 11px;
                font-weight: bold;
                color: white;
                background-color: #27ae60;
                padding: 7px;
                border-radius: 4px;
                border: none;
            }
            QPushButton:hover {
                background-color: #2ecc71;
            }
        """)
        
        def make_open_folder(file_path):
            def open_folder():
                import subprocess, sys
                folder = os.path.dirname(file_path)
                print(f"DEBUG: Opening folder for file: {file_path}")
                print(f"DEBUG: Folder path: {folder}")
                if sys.platform.startswith('win'):
                    subprocess.Popen(['explorer', '/select,', file_path.replace('/', '\\')])
                elif sys.platform.startswith('darwin'):
                    subprocess.Popen(['open', folder])
                else:
                    subprocess.Popen(['xdg-open', folder])
            return open_folder
        
        def make_delete_cb(file_path):
            def delete_file():
                delete_cb(file_path)
            return delete_file
        
        open_btn.clicked.connect(make_open_folder(f))
        
        del_btn = QPushButton("🗑 削除")
        del_btn.setFixedHeight(65)
        del_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        del_btn.setStyleSheet("""
            QPushButton {
                font-size: 11px;
                font-weight: bold;
                color: white;
                background-color: #e74c3c;
                padding: 7px;
                border-radius: 4px;
                border: none;
            }
            QPushButton:hover {
                background-color: #c0392b;
            }
        """)
        del_btn.clicked.connect(make_delete_cb(f))
        
        btn_hbox.addWidget(open_btn)
        btn_hbox.addWidget(del_btn)
        
        # 情報部分
        info_widget = QWidget()
        info_widget.setFixedSize(189, 290)
        info_widget.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        info_widget.setStyleSheet("""
            QWidget {
                background-color: #2c3e50;
                border-radius: 5px;
            }
        """)
        
        info_vbox = QVBoxLayout()
        info_vbox.setSpacing(0)
        info_vbox.setContentsMargins(15, 15, 15, 15)
        info_vbox.setAlignment(Qt.AlignTop)
        
        from PyQt5.QtWidgets import QSpacerItem
        
        info_vbox.addWidget(name_label)
        if duration_label:
            info_vbox.addWidget(duration_label)
        info_vbox.addWidget(size_label)
        
        # スペーサー削除（無駄な空間を排除）
        
        info_vbox.addLayout(btn_hbox)
        
        info_widget.setLayout(info_vbox)
        
        # レイアウト組み立て（横並び）
        card_hbox.addWidget(thumb_btn)
        card_hbox.addWidget(info_widget)
        
        file_card.setLayout(card_hbox)
        
        row = idx // max_col
        col = idx % max_col
        grid.addWidget(file_card, row, col)
        
        # マップに登録（サイズ更新）
        if thumb_widget_map is not None:
            thumb_widget_map[norm_path] = thumb_btn
    
    # メインレイアウト
    layout = QVBoxLayout()
    layout.setSpacing(10)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(header_label)
    layout.addLayout(grid)
    group_box.setLayout(layout)
    
    return group_box