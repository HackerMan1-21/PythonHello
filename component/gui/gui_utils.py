# gui_utils.py
# 共通UIユーティリティ（スタイル・汎用関数など）
from PyQt5.QtWidgets import QStyledItemDelegate
from PyQt5.QtCore import QSize

class ThumbnailDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(180, 180)
