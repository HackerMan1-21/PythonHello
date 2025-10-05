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
            padding: 0px;
        }
    """)

    # ヘッダー
    header_hbox = QHBoxLayout()
    header_label = QLabel(f"重複グループ（{len(group)}ファイル）")
    header_label.setStyleSheet("font-size:18px;color:#fff;font-weight:bold;margin-bottom:10px;")
    header_hbox.addWidget(header_label)

    dismiss_btn = QPushButton("非表示")
    dismiss_btn.setFixedSize(100, 50)
    dismiss_btn.setStyleSheet("""
        QPushButton {
            font-size: 12px;
            color: #fff;
            background-color: #555;
            border: 1px solid #777;
            border-radius: 4px;
            padding: 4px;
        }
        QPushButton:hover {
            background-color: #666;
        }
    """)

    def dismiss_group_cb():
        group_box.hide()

    dismiss_btn.clicked.connect(dismiss_group_cb)
    header_hbox.addWidget(dismiss_btn)
    header_hbox.addStretch()

    # グリッド
    grid = QGridLayout()
    grid.setHorizontalSpacing(0)
    grid.setVerticalSpacing(0)
    grid.setContentsMargins(0, 0, 0, 0)

    max_col = 4
    visible_cards = []

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
        card_hbox.setSpacing(0)
        card_hbox.setContentsMargins(0, 0, 0, 0)

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
                font-size: 12px;
                font-weight: bold;
                color: #ffffff;
                background-color: #34495e;
                padding: 3px;
                border-radius: 3px;
                line-height: 1.3;
            }
        """)
        name_label.setFixedHeight(200)
        name_label.setMinimumHeight(60)
        name_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        name_label.setWordWrap(True)
        name_label.setToolTip(f)

        # 動画情報（時間と解像度を同時取得）
        duration_label = None
        video_resolution_text = None
        if f.lower().endswith(('.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm')):
            duration_text = "⏱ 不明"
            cap = None
            try:
                import cv2
                cap = cv2.VideoCapture(f)
                if cap.isOpened():
                    # 時間取得
                    fps = cap.get(cv2.CAP_PROP_FPS)
                    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
                    if fps > 0 and frame_count > 0:
                        duration_seconds = int(frame_count / fps)
                        hours = duration_seconds // 3600
                        minutes = (duration_seconds % 3600) // 60
                        seconds = duration_seconds % 60
                        duration_text = f"⏱ {hours:02d}:{minutes:02d}:{seconds:02d}"

                    # 解像度取得
                    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    if width > 0 and height > 0:
                        video_resolution_text = f"📐 {width}×{height}"
            except Exception as e:
                print(f"Video info error: {e}")
            finally:
                if cap is not None:
                    cap.release()

            duration_label = QLabel(duration_text)
            duration_label.setStyleSheet("""
                QLabel {
                    font-size: 12px;
                    font-weight: bold;
                    color: #ffffff;
                    background-color: #34495e;
                    padding: 3px;
                    border-radius: 3px;
                    text-align: center;
                }
            """)
            duration_label.setFixedHeight(70)
            duration_label.setMinimumHeight(70)
            duration_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # ファイルサイズ（見えるサイズ）
        try:
            size_mb = os.path.getsize(f) / (1024 * 1024)
            size_text = f"📁 {size_mb:.1f} MB"
        except OSError as e:
            size_text = "📁 不明"
            print(f"File size error: {e}")

        size_label = QLabel(size_text)
        size_label.setStyleSheet("""
            QLabel {
                font-size: 12px;
                font-weight: bold;
                color: #ffffff;
                background-color: #34495e;
                padding: 3px;
                border-radius: 3px;
                text-align: center;
            }
        """)
        size_label.setFixedHeight(70)
        size_label.setMinimumHeight(70)
        size_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # フレーム解像度（動画・画像のみ表示）
        resolution_label = None
        resolution_text = "📐 不明"

        # 画像ファイルの解像度取得
        if f.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff')):
            try:
                from PIL import Image
                with Image.open(f) as img:
                    width, height = img.size
                    resolution_text = f"📐 {width}×{height}"
            except Exception as e:
                print(f"Image resolution error: {e}")

        # 動画の場合は上で取得済み
        elif f.lower().endswith(('.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm')):
            if video_resolution_text:
                resolution_text = video_resolution_text

        if resolution_text != "📐 不明" or video_resolution_text:

            resolution_label = QLabel(resolution_text)
            resolution_label.setStyleSheet("""
                QLabel {
                    font-size: 12px;
                    font-weight: bold;
                    color: #ffffff;
                    background-color: #34495e;
                    padding: 3px;
                    border-radius: 3px;
                    text-align: center;
                }
            """)
            resolution_label.setFixedHeight(70)
            resolution_label.setMinimumHeight(70)
            resolution_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # ボタン（大きく押しやすく）
        btn_hbox = QHBoxLayout()
        btn_hbox.setSpacing(0)
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
                import subprocess, sys, shlex
                try:
                    folder = os.path.dirname(os.path.abspath(file_path))
                    if sys.platform.startswith('win'):
                        subprocess.Popen(['explorer', '/select,', os.path.normpath(file_path)])
                    elif sys.platform.startswith('darwin'):
                        subprocess.Popen(['open', folder])
                    else:
                        subprocess.Popen(['xdg-open', folder])
                except (OSError, subprocess.SubprocessError) as e:
                    print(f"Error opening folder: {e}")
            return open_folder

        def make_delete_cb(file_path, card_widget, cards_list, group_widget):
            def delete_file():
                delete_cb(file_path)
                card_widget.hide()
                visible_count = sum(1 for c in cards_list if c.isVisible())
                if visible_count <= 1:
                    group_widget.hide()
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
        del_btn.clicked.connect(make_delete_cb(f, file_card, visible_cards, group_box))

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
        info_vbox.setContentsMargins(0, 0, 0, 0)
        # info_vbox.setAlignment(Qt.AlignTop)  # Qt定数エラー回避

        # from PyQt5.QtWidgets import QSpacerItem  # 使用していないのでコメントアウト

        info_vbox.addWidget(name_label)
        if duration_label:
            info_vbox.addWidget(duration_label)
        info_vbox.addWidget(size_label)
        if resolution_label:
            info_vbox.addWidget(resolution_label)

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
        visible_cards.append(file_card)

        # マップに登録（サイズ更新）
        if thumb_widget_map is not None:
            thumb_widget_map[norm_path] = thumb_btn

    # メインレイアウト
    layout = QVBoxLayout()
    layout.setSpacing(10)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addLayout(header_hbox)
    layout.addLayout(grid)
    group_box.setLayout(layout)

    return group_box
