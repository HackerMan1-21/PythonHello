# 高速グループUI - 数万件対応
from PyQt5.QtWidgets import QGroupBox, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QWidget, QGridLayout
from PyQt5.QtCore import QSize, Qt
from PyQt5.QtGui import QPixmap, QIcon
import os
from component.thumbnail.thumbnail_util import get_no_thumbnail_image, pil_image_to_qpixmap

def create_fast_group_ui(group, delete_cb, thumb_widget_map):
    group_box = QGroupBox(f"重複グループ（{len(group)}ファイル）")
    grid = QGridLayout()
    grid.setHorizontalSpacing(8)
    grid.setVerticalSpacing(8)
    
    max_col = 6
    no_thumb_pix = pil_image_to_qpixmap(get_no_thumbnail_image((120, 120)))
    
    for idx, f in enumerate(group):
        norm_path = os.path.abspath(os.path.normpath(f))
        
        # サムネイルボタン
        thumb_btn = QPushButton()
        thumb_btn.setFixedSize(120, 120)
        thumb_btn.setIcon(QIcon(no_thumb_pix))
        thumb_btn.setIconSize(QSize(120, 120))
        thumb_btn.setStyleSheet("border:1px solid #00ffe7;border-radius:6px;")
        
        # ファイル名ラベル
        fname = os.path.basename(f)
        if len(fname) > 15:
            fname = fname[:12] + "..."
        name_label = QLabel(fname)
        name_label.setStyleSheet("font-size:10px;color:#00ffe7;")
        name_label.setMaximumWidth(120)
        
        # 削除ボタン
        del_btn = QPushButton("削除")
        del_btn.setStyleSheet("font-size:10px;color:#ff00c8;")
        del_btn.setFixedSize(120, 20)
        del_btn.clicked.connect(lambda _, path=f: delete_cb(path))
        
        # レイアウト
        vbox = QVBoxLayout()
        vbox.setSpacing(2)
        vbox.addWidget(thumb_btn)
        vbox.addWidget(name_label)
        vbox.addWidget(del_btn)
        
        file_widget = QWidget()
        file_widget.setLayout(vbox)
        file_widget.setFixedSize(130, 170)
        
        row = idx // max_col
        col = idx % max_col
        grid.addWidget(file_widget, row, col)
        
        # マップに登録
        thumb_widget_map[norm_path] = thumb_btn
    
    group_box.setLayout(grid)
    return group_box