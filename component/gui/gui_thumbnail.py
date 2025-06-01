# gui_thumbnail.py
# サムネイルリスト・サムネイル関連UI
from PyQt5.QtCore import QSize, QAbstractListModel, QModelIndex, QVariant, pyqtSignal, Qt
from PyQt5.QtGui import QIcon
from component.thumbnail.thumbnail_util import get_thumbnail_for_file, get_no_thumbnail_image, pil_image_to_qpixmap

class ThumbnailListModel(QAbstractListModel):
    thumb_updated = pyqtSignal(str, object)  # path, pil_image
    def __init__(self, file_list, thumb_cache, defer_queue, parent=None):
        super().__init__(parent)
        self.file_list = file_list
        self.thumb_cache = thumb_cache
        self.defer_queue = defer_queue
        self.thumb_updated.connect(self.on_thumb_updated)
        self._pending = set()
        self._icon_cache = {}  # path -> QIcon キャッシュ
    def rowCount(self, parent=QModelIndex()):
        return len(self.file_list)
    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self.file_list)):
            return QVariant()
        path = self.file_list[index.row()]
        if role == Qt.DecorationRole:
            if path in self._icon_cache:
                return self._icon_cache[path]
            pil_thumb = self.thumb_cache.get(path) if self.thumb_cache else None
            if pil_thumb is not None:
                from PyQt5.QtCore import QTimer
                def set_icon():
                    try:
                        icon = QIcon(pil_image_to_qpixmap(pil_thumb))
                        self._icon_cache[path] = icon
                        self.thumb_updated.emit(path, pil_thumb)
                    except RuntimeError:
                        pass
                QTimer.singleShot(0, set_icon)
                return QIcon()  # 一時的に空
            else:
                if path not in self._pending:
                    self._pending.add(path)
                    def worker():
                        pil_thumb = get_thumbnail_for_file(path, (180, 180), cache=self.thumb_cache, defer_queue=self.defer_queue)
                        from PyQt5.QtCore import QTimer
                        def set_icon():
                            try:
                                icon = QIcon(pil_image_to_qpixmap(pil_thumb))
                                self._icon_cache[path] = icon
                                self.thumb_updated.emit(path, pil_thumb)
                                self._pending.discard(path)
                            except RuntimeError:
                                pass
                        QTimer.singleShot(0, set_icon)
                    import threading
                    threading.Thread(target=worker, daemon=True).start()
                return QIcon(pil_image_to_qpixmap(get_no_thumbnail_image((180, 180))))
        if role == Qt.DisplayRole:
            import os
            return os.path.basename(path)
        return QVariant()
    def on_thumb_updated(self, path, pil_image):
        if path in self.file_list:
            row = self.file_list.index(path)
            self.dataChanged.emit(self.index(row), self.index(row))
