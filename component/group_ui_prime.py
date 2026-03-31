"""Prime Video風カードUI (改善版)"""

from typing import List

from PyQt5.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
    QGridLayout,
    QSizePolicy,
    QMessageBox,
)
from PyQt5.QtCore import QSize, QEvent, QObject, QTimer, Qt
from PyQt5.QtGui import QCursor, QIcon, QPixmap
import os

from component.thumbnail.thumbnail_util import (
    pil_image_to_qpixmap,
    get_no_thumbnail_image,
    get_video_preview_frames,
)

THUMB_MAX_SIZE = QSize(288, 162)  # サムネイルの最大サイズ
INFO_WIDTH = 256  # 情報パネルを少し広めに取りつつ幅を抑える
CARD_SIDE_PADDING = 10
CARD_INTERNAL_SPACING = 8
CARD_WIDTH = THUMB_MAX_SIZE.width() + INFO_WIDTH + CARD_INTERNAL_SPACING + (CARD_SIDE_PADDING * 2)
KEEP_ASPECT = getattr(Qt, "KeepAspectRatio", getattr(getattr(Qt, "AspectRatioMode", Qt), "KeepAspectRatio"))
SMOOTH_TRANSFORM = getattr(Qt, "SmoothTransformation", getattr(getattr(Qt, "TransformationMode", Qt), "SmoothTransformation"))
POINTING_CURSOR = getattr(Qt, "PointingHandCursor", getattr(getattr(Qt, "CursorShape", Qt), "PointingHandCursor"))
PREVIEW_INTERVAL_MS = 350
VIDEO_EXTS = ('.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm', '.mpg', '.mpeg')
BUTTON_BASE_STYLE = (
    # 親の包括QSSと競合しにくいよう、セレクタ無し（プロパティ直指定）で定義
    "font-size: 14px;"
    "font-weight: bold;"
    "color: {fg};"
    "background-color: {bg};"
    "border: 2px solid {border};"
    "border-radius: 12px;"
    "padding: 1px;"
    "min-height: 49px;"
)


def build_button_style(fg: str, bg: str, border: str, hover: str) -> str:
    return BUTTON_BASE_STYLE.format(fg=fg, bg=bg, border=border, hover=hover)


HEADER_BUTTON_STYLE = build_button_style(
    fg="#00ffe7",
    # Qtのrgbaはalphaが0-255前提なので整数で指定
    bg="rgba(0,255,231,30)",
    border="rgba(0,255,231,170)",
    hover="rgba(0,255,231,60)",
)

OPEN_BUTTON_STYLE = build_button_style(
    fg="#ffffff",
    bg="#2ecc71",
    border="#2ecc71",
    hover="#39d77d",
)

DELETE_BUTTON_STYLE = build_button_style(
    fg="#ffffff",
    bg="#f35f4d",
    border="#f35f4d",
    hover="#ff7568",
)


class AdaptiveGridWidget(QWidget):
    """画面幅に応じて列数を自動調整するグリッドコンテナ。"""

    def __init__(self, card_width: int, max_columns: int = 4, horizontal_spacing: int = 4, vertical_spacing: int = 8):
        super().__init__()
        self.card_width = card_width
        self.max_columns = max_columns
        self.horizontal_spacing = horizontal_spacing
        self.vertical_spacing = vertical_spacing
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(horizontal_spacing)
        self.grid.setVerticalSpacing(vertical_spacing)
        self.grid.setContentsMargins(0, 0, 0, 0)
        align_left = getattr(Qt, "AlignLeft", getattr(getattr(Qt, "AlignmentFlag", Qt), "AlignLeft"))
        align_top = getattr(Qt, "AlignTop", getattr(getattr(Qt, "AlignmentFlag", Qt), "AlignTop"))
        self.grid.setAlignment(align_left | align_top)  # type: ignore[arg-type]
        self.setLayout(self.grid)
        self.cards: List[QWidget] = []
        self._current_columns = 0

    def add_card(self, card: QWidget) -> None:
        self.cards.append(card)
        self._relayout(force=True)

    def remove_card(self, card: QWidget) -> None:
        if card in self.cards:
            self.cards.remove(card)
        card.setParent(None)
        card.deleteLater()
        self._relayout(force=True)

    def has_cards(self) -> bool:
        return any(card for card in self.cards if card is not None and not card.isHidden())

    def resizeEvent(self, event):  # type: ignore[override]
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self, force: bool = False) -> None:
        if not self.cards:
            return
        available_width = max(self.width(), self.card_width)
        if available_width <= 0:
            available_width = self.card_width * min(len(self.cards), self.max_columns)
        columns = max(1, min(self.max_columns, max(1, (available_width + self.horizontal_spacing) // (self.card_width + self.horizontal_spacing))))
        if not force and columns == self._current_columns:
            return
        self._current_columns = columns

        while self.grid.count():
            item = self.grid.takeAt(0)
            if item is None:
                continue
            child_widget = item.widget()
            if child_widget is not None:
                child_widget.setParent(None)

        for idx, card in enumerate(self.cards):
            row = idx // columns
            col = idx % columns
            self.grid.addWidget(card, row, col)


class HoverPreviewController(QObject):
    """サムネイルボタンにホバー時プレビューを提供するイベントフィルタ。"""

    def __init__(self, button: QPushButton, file_path: str, max_thumb_size: QSize):
        super().__init__(button)
        self.button = button
        self.file_path = file_path
        self.max_thumb_size = max_thumb_size
        self.timer = QTimer(button)
        self.timer.setInterval(PREVIEW_INTERVAL_MS)
        self.timer.timeout.connect(self._advance_frame)
        self.frames: List[QPixmap] = []
        self.frame_index = 0
        self._base_pixmap: QPixmap | None = None

    def eventFilter(self, obj, event):  # type: ignore[override]
        if obj is not self.button:
            return False

        event_type = event.type()
        enter_type = getattr(QEvent, "Enter", getattr(QEvent.Type, "Enter"))
        leave_type = getattr(QEvent, "Leave", getattr(QEvent.Type, "Leave"))
        hover_leave_type = getattr(QEvent, "HoverLeave", getattr(QEvent.Type, "HoverLeave", None))

        if event_type == enter_type:
            self._start_preview()
        elif event_type == leave_type or (hover_leave_type is not None and event_type == hover_leave_type):
            self._stop_preview()
        return False

    def _start_preview(self) -> None:
        current_icon = self.button.icon()
        self._base_pixmap = current_icon.pixmap(self.button.iconSize())
        frames = get_video_preview_frames(self.file_path, size=(self.max_thumb_size.width(), self.max_thumb_size.height()))
        if not frames:
            return
        self.frames = [pil_image_to_qpixmap(img) for img in frames]
        self.frame_index = 0
        self.timer.start()

    def _advance_frame(self) -> None:
        if not self.frames:
            self.timer.stop()
            return
        frame_pix = self.frames[self.frame_index]
        if hasattr(self.button, "set_preview_pixmap"):
            self.button.set_preview_pixmap(frame_pix)  # type: ignore[attr-defined]
        else:
            scaled = frame_pix.scaled(self.max_thumb_size, KEEP_ASPECT, SMOOTH_TRANSFORM)
            self.button.setIcon(QIcon(scaled))
            self.button.setIconSize(scaled.size())
        self.frame_index = (self.frame_index + 1) % len(self.frames)

    def _stop_preview(self) -> None:
        self.timer.stop()
        if self._base_pixmap is not None:
            if hasattr(self.button, "set_pixmap"):
                self.button.set_pixmap(self._base_pixmap)  # type: ignore[attr-defined]
            else:
                base_icon = QIcon(self._base_pixmap)
                self.button.setIcon(base_icon)
                self.button.setIconSize(base_icon.actualSize(self.max_thumb_size))


class ResizableThumbnailButton(QPushButton):
    """元画像の縦横比を保ったまま最大サイズ内に収めるサムネイルボタン。"""

    def __init__(self, max_size: QSize):
        super().__init__()
        self._max_size = max_size
        self._suppress_resize = False
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setCursor(QCursor(POINTING_CURSOR))

    def _apply_pixmap(self, pixmap: QPixmap | None) -> None:
        if pixmap is None or pixmap.isNull():
            super().setIcon(QIcon())
            self.setIconSize(self._max_size)
            self.setFixedSize(self._max_size)
            return
        scaled = pixmap.scaled(self._max_size, KEEP_ASPECT, SMOOTH_TRANSFORM)
        super().setIcon(QIcon(scaled))
        self.setIconSize(scaled.size())
        self.setFixedSize(scaled.size())
        self.updateGeometry()

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._suppress_resize = False
        self._apply_pixmap(pixmap)

    def setIcon(self, icon: QIcon) -> None:  # type: ignore[override]
        if self._suppress_resize:
            super().setIcon(icon)
            return
        if icon.isNull():
            self._apply_pixmap(None)
            return
        pixmap = icon.pixmap(self._max_size)
        if pixmap.isNull():
            pixmap = icon.pixmap(icon.actualSize(self._max_size))
        self._apply_pixmap(pixmap)

    def set_preview_pixmap(self, pixmap: QPixmap) -> None:
        current_size = self.iconSize()
        if current_size.isEmpty():
            current_size = self._max_size
        scaled = pixmap.scaled(current_size, KEEP_ASPECT, SMOOTH_TRANSFORM)
        self._suppress_resize = True
        try:
            super().setIcon(QIcon(scaled))
            self.setIconSize(current_size)
            self.setFixedSize(current_size)
        finally:
            self._suppress_resize = False


def create_prime_group_ui(
    group: list,
    get_thumbnail_for_file,
    detail_cb,
    delete_cb,
    compare_cb,
    thumb_cache=None,
    defer_queue=None,
    thumb_widget_map=None,
    parent=None,
    elapsed_time=None,
    eta_time=None,
    remain_count=None
) -> QGroupBox:

    group_box = QGroupBox()
    group_box.setStyleSheet(
        """
        QGroupBox {
            background: #151515;
            border: none;
            margin: 0;
            padding: 6px 0 8px 0;
        }
        """
    )

    header_hbox = QHBoxLayout()
    header_hbox.setContentsMargins(0, 0, 0, 4)
    header_hbox.setSpacing(8)

    header_label = QLabel(f"重複グループ（{len(group)}ファイル）")
    header_label.setStyleSheet("font-size:15px;color:#ffffff;font-weight:bold;")
    header_hbox.addWidget(header_label)

    dismiss_btn = QPushButton("非表示")
    dismiss_btn.setMinimumHeight(53)
    dismiss_btn.setMinimumWidth(132)
    dismiss_btn.setStyleSheet(HEADER_BUTTON_STYLE)

    def dismiss_group_cb() -> None:
        group_box.hide()

    dismiss_btn.clicked.connect(dismiss_group_cb)
    header_hbox.addWidget(dismiss_btn)
    header_hbox.addStretch(1)

    # 間隔を少し詰めてカード間の余白を小さくする
    grid_widget = AdaptiveGridWidget(card_width=CARD_WIDTH, max_columns=4, horizontal_spacing=4, vertical_spacing=8)

    for f in group:
        norm_path = os.path.abspath(os.path.normpath(f))

        file_card = QWidget()
        file_card.setMinimumWidth(CARD_WIDTH)
        file_card.setMinimumHeight(THUMB_MAX_SIZE.height() + 84)
        file_card.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.MinimumExpanding)
        file_card.setContentsMargins(0, 0, 0, 0)
        file_card.setStyleSheet(
            """
            QWidget {
                background-color: #202020;
                border-radius: 6px;
                border: 1px solid #2f2f2f;
            }
            QWidget:hover {
                border: 1px solid #00ffe7;
            }
            """
        )

        card_hbox = QHBoxLayout()
        card_hbox.setContentsMargins(CARD_SIDE_PADDING, 4, CARD_SIDE_PADDING, 6)
        card_hbox.setSpacing(CARD_INTERNAL_SPACING)

        thumb_btn = ResizableThumbnailButton(THUMB_MAX_SIZE)
        thumb_btn.setStyleSheet(
            """
            QPushButton {
                background-color: transparent;
                border: none;
                padding: 0;
            }
            QPushButton:hover {
                background-color: rgba(255,255,255,0.05);
            }
            """
        )
        placeholder_pix = pil_image_to_qpixmap(
            get_no_thumbnail_image((THUMB_MAX_SIZE.width(), THUMB_MAX_SIZE.height()))
        )
        thumb_btn.set_pixmap(placeholder_pix)

        def make_detail_cb(file_path: str):
            def show_detail() -> None:
                detail_cb(parent, file_path)

            return show_detail

        thumb_btn.clicked.connect(make_detail_cb(f))

        if f.lower().endswith(VIDEO_EXTS):
            preview_controller = HoverPreviewController(thumb_btn, f, THUMB_MAX_SIZE)
            thumb_btn.setMouseTracking(True)
            thumb_btn.installEventFilter(preview_controller)
            thumb_btn._preview_controller = preview_controller  # type: ignore[attr-defined]

        info_widget = QWidget()
        info_widget.setMinimumWidth(INFO_WIDTH)
        info_widget.setMinimumHeight(THUMB_MAX_SIZE.height())
        info_widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        info_widget.setStyleSheet(
            """
            QWidget {
                background-color: rgba(56, 66, 77, 0.14);
                border-radius: 6px;
            }
            """
        )

        info_vbox = QVBoxLayout()
        info_vbox.setContentsMargins(6, 4, 6, 6)
        info_vbox.setSpacing(2)

        path_parts = f.replace('\\', '/').split('/')
        file_name = os.path.basename(f)
        folder_text = '/'.join(path_parts[-4:-1]) if len(path_parts) > 1 else '(ルート)'

        folder_label = QLabel(folder_text)
        folder_label.setStyleSheet(
            "font-size:13px;color:#9ad8ff;background-color:rgba(154,216,255,0.08);padding:1px 5px;border-radius:3px;"
        )
        folder_label.setWordWrap(True)
        folder_label.setToolTip(folder_text)

        name_label = QLabel(file_name)
        name_label.setStyleSheet("font-size:13px;color:#ffffff;font-weight:bold;line-height:1.25;")
        name_label.setWordWrap(True)
        name_label.setToolTip(f)

        info_vbox.addWidget(folder_label)
        info_vbox.addWidget(name_label)

        meta_texts: List[str] = []

        try:
            size_bytes = os.path.getsize(f)
            if size_bytes < 1024:
                meta_texts.append(f"📁 {size_bytes} B")
            elif size_bytes < 1024 * 1024:
                meta_texts.append(f"📁 {size_bytes / 1024:.1f} KB")
            elif size_bytes < 1024 * 1024 * 1024:
                meta_texts.append(f"📁 {size_bytes / (1024 * 1024):.1f} MB")
            else:
                meta_texts.append(f"📁 {size_bytes / (1024 * 1024 * 1024):.2f} GB")
        except OSError:
            meta_texts.append("📁 不明")

        resolution_text = None
        duration_text = None
        if f.lower().endswith(VIDEO_EXTS):
            try:
                import cv2

                cap = cv2.VideoCapture(f)
                if cap.isOpened():
                    fps = cap.get(cv2.CAP_PROP_FPS) or 0
                    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
                    if fps > 0 and frame_count > 0:
                        duration_seconds = int(frame_count / fps)
                        hours = duration_seconds // 3600
                        minutes = (duration_seconds % 3600) // 60
                        seconds = duration_seconds % 60
                        duration_text = f"⏱ {hours:02d}:{minutes:02d}:{seconds:02d}"
                    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
                    if width > 0 and height > 0:
                        resolution_text = f"📐 {width}×{height}"
                cap.release()
            except Exception as err:
                print(f"[PREVIEW WARN] 動画メタ情報取得失敗: {err}")
        else:
            try:
                from PIL import Image

                with Image.open(f) as img:
                    width, height = img.size
                    resolution_text = f"📐 {width}×{height}"
            except Exception as err:
                print(f"[PREVIEW WARN] 画像メタ情報取得失敗: {err}")

        if duration_text:
            meta_texts.append(duration_text)
        if resolution_text:
            meta_texts.append(resolution_text)

        for meta in meta_texts:
            meta_label = QLabel(meta)
            meta_label.setStyleSheet(
                "font-size:13px;color:#d0f0ff;background-color:rgba(208,240,255,0.08);padding:1px 5px;border-radius:3px;"
            )
            meta_label.setWordWrap(True)
            info_vbox.addWidget(meta_label)

        info_vbox.addSpacing(2)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 4, 0, 0)
        button_row.setSpacing(10)

        def make_open_folder(file_path: str):
            def open_folder() -> None:
                import subprocess
                import sys

                try:
                    folder = os.path.dirname(os.path.abspath(file_path))
                    if sys.platform.startswith('win'):
                        subprocess.Popen(['explorer', '/select,', os.path.normpath(file_path)])
                    elif sys.platform.startswith('darwin'):
                        subprocess.Popen(['open', folder])
                    else:
                        subprocess.Popen(['xdg-open', folder])
                except (OSError, subprocess.SubprocessError) as err:
                    print(f"Error opening folder: {err}")

            return open_folder

        open_btn = QPushButton("フォルダ")
        open_btn.setMinimumHeight(53)
        open_btn.setFixedWidth(150)
        open_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        open_btn.setStyleSheet(OPEN_BUTTON_STYLE)
        open_btn.clicked.connect(make_open_folder(f))

        def make_delete_cb(file_path: str, card_widget: QWidget):
            def delete_file() -> None:
                parent_widget = parent if parent is not None else group_box
                reply = QMessageBox.question(
                    parent_widget,
                    "削除確認",
                    f"選択したファイルを削除しますか？\n{file_path}",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    return
                try:
                    delete_cb(file_path)
                except Exception as err:
                    QMessageBox.critical(parent_widget, "削除エラー", str(err))
                    return
                grid_widget.remove_card(card_widget)
                if not grid_widget.has_cards():
                    group_box.hide()

            return delete_file

        del_btn = QPushButton("削除")
        del_btn.setMinimumHeight(53)
        del_btn.setFixedWidth(132)
        del_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        del_btn.setStyleSheet(DELETE_BUTTON_STYLE)
        del_btn.clicked.connect(make_delete_cb(f, file_card))

        button_row.addWidget(open_btn)
        button_row.addWidget(del_btn)
        info_vbox.addLayout(button_row)

        info_widget.setLayout(info_vbox)

        card_hbox.addWidget(thumb_btn)
        card_hbox.addWidget(info_widget)

        file_card.setLayout(card_hbox)

        grid_widget.add_card(file_card)

        if thumb_widget_map is not None:
            thumb_widget_map[norm_path] = thumb_btn

    layout = QVBoxLayout()
    layout.setContentsMargins(10, 6, 10, 10)
    layout.setSpacing(6)
    layout.addLayout(header_hbox)
    layout.addWidget(grid_widget)
    group_box.setLayout(layout)

    return group_box
