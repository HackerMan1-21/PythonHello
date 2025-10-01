# カードUI版重複グループ
from PyQt5.QtWidgets import QGroupBox, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QWidget, QGridLayout, QScrollArea
from PyQt5.QtCore import QSize, Qt, QTimer, QThread, pyqtSignal, QObject
from PyQt5.QtGui import QPixmap, QIcon
from PIL import Image, ImageDraw
import os
from component.thumbnail.thumbnail_util import get_thumbnail_for_file, pil_image_to_qpixmap, get_no_thumbnail_image, ThumbnailCache
from typing import Optional

def create_card_group_ui(
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
            background: #fafafa;
            border: 1px solid #ddd;
            border-radius: 6px;
            margin: 2px;
            padding: 0px;
        }
    """)
    
    # ヘッダー固定部分
    header_widget = QWidget()
    header_widget.setStyleSheet("""
        QWidget {
            background: #f0f0f0;
            border-bottom: 1px solid #ccc;
            padding: 4px;
        }
    """)
    header_widget.setFixedHeight(30)
    
    top_hbox = QHBoxLayout()
    top_hbox.setSpacing(4)
    top_hbox.setContentsMargins(4, 2, 4, 2)
    
    remain_label = QLabel(f"重複グループ（残り: {len(group)}ファイル）")
    remain_label.setStyleSheet("font-size:12px;color:#333;font-weight:bold;margin:0;padding:0;")
    hide_btn = QPushButton("グループを非表示")
    hide_btn.setStyleSheet("font-size:11px;color:#666;background:#e0e0e0;border:1px solid #ccc;border-radius:4px;margin:0;padding:2px 6px;")
    
    top_hbox.addWidget(remain_label)
    top_hbox.addWidget(hide_btn)
    top_hbox.addStretch(1)
    header_widget.setLayout(top_hbox)
    
    # グリッド部分
    grid = QGridLayout()
    grid.setHorizontalSpacing(4)
    grid.setVerticalSpacing(4)
    grid.setContentsMargins(4, 4, 4, 4)
    
    max_col = 4
    if isinstance(thumb_cache, ThumbnailCache):
        cache_dict = thumb_cache.cache
    elif isinstance(thumb_cache, dict):
        cache_dict = thumb_cache
    else:
        cache_dict = {}
    
    video_info_cache = {}
    
    for idx, f in enumerate(group):
        norm_path = os.path.abspath(os.path.normpath(f))
        
        # サムネイルボタン
        thumb_btn = QPushButton()
        thumb_btn.setFixedSize(200, 200)
        thumb_btn.setIcon(QIcon(pil_image_to_qpixmap(get_no_thumbnail_image((200, 200)))))
        thumb_btn.setIconSize(QSize(200, 200))
        thumb_btn.setStyleSheet("background:transparent;border:2px solid #00ffe7;border-radius:10px;")
        thumb_btn.clicked.connect(lambda _, path=f: detail_cb(parent, path))
        
        # 情報部分
        info_vbox = QVBoxLayout()
        info_vbox.setSpacing(4)
        info_vbox.setContentsMargins(0, 0, 0, 0)
        
        fname = os.path.basename(f)
        name_label = QLabel(fname)
        name_label.setStyleSheet("font-size:12px;color:#333;font-weight:bold;max-width:200px;background:rgba(255,255,255,0.8);padding:4px;border-radius:4px;")
        name_label.setMaximumWidth(200)
        name_label.setFixedHeight(30)
        
        size_label = QLabel("取得中...")
        size_label.setStyleSheet("font-size:11px;color:#666;max-width:200px;background:rgba(255,255,255,0.6);padding:3px;border-radius:3px;")
        size_label.setMaximumWidth(200)
        size_label.setFixedHeight(25)
        
        # ボタン
        btn_hbox = QHBoxLayout()
        btn_hbox.setSpacing(3)
        btn_hbox.setContentsMargins(0, 0, 0, 0)
        
        open_folder_btn = QPushButton("📁 フォルダ")
        open_folder_btn.setStyleSheet("font-size:11px;color:#007acc;background:rgba(0,122,204,0.1);border:1px solid #007acc;border-radius:4px;padding:4px;")
        open_folder_btn.setFixedHeight(30)
        open_folder_btn.setFixedWidth(95)
        
        del_btn = QPushButton("🗑️ 削除")
        del_btn.setStyleSheet("font-size:11px;color:#d73a49;background:rgba(215,58,73,0.1);border:1px solid #d73a49;border-radius:4px;padding:4px;")
        del_btn.setFixedHeight(30)
        del_btn.setFixedWidth(95)
        
        btn_hbox.addWidget(open_folder_btn)
        btn_hbox.addWidget(del_btn)
        
        info_vbox.addWidget(name_label)
        info_vbox.addWidget(size_label)
        info_vbox.addLayout(btn_hbox)
        
        info_widget = QWidget()
        info_widget.setLayout(info_vbox)
        info_widget.setFixedWidth(200)
        info_widget.setFixedHeight(200)
        
        # ファイルウィジェット（縦配置）
        file_widget = QWidget()
        file_vbox = QVBoxLayout()
        file_vbox.setSpacing(2)
        file_vbox.setContentsMargins(6, 6, 6, 6)
        file_vbox.addWidget(thumb_btn)
        file_vbox.addWidget(info_widget)
        file_widget.setLayout(file_vbox)
        file_widget.setFixedHeight(220)
        file_widget.setStyleSheet("""
            QWidget {
                border: 1px solid #ddd;
                border-radius: 4px;
                background: #fff;
            }
            QWidget:hover {
                border: 1px solid #aaa;
                background: #f9f9f9;
            }
        """)
        
        row = idx // max_col
        col = idx % max_col
        grid.addWidget(file_widget, row, col)
        
        # マップに登録
        if thumb_widget_map is not None:
            thumb_widget_map[norm_path] = thumb_btn
    
    grid_widget = QWidget()
    grid_widget.setLayout(grid)
    
    # スクロールエリア
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(grid_widget)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    scroll.setStyleSheet("""
        QScrollArea {
            background: transparent;
            border: none;
        }
        QScrollBar:vertical {
            width: 8px;
            background: transparent;
        }
        QScrollBar::handle:vertical {
            background: #aaa;
            border-radius: 4px;
        }
    """)
    
    # メインレイアウト
    layout = QVBoxLayout()
    layout.setSpacing(0)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(header_widget)
    layout.addWidget(scroll)
    group_box.setLayout(layout)
    
    return group_box