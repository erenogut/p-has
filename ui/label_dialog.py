from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from training.prepare import IMAGE_EXTS
from training.yolo_label import CLASS_NAME, has_label, load_boxes, save_boxes
from ui.dialogs import show_warning
from ui.theme import (
    COLOR_ACCENT,
    COLOR_BG,
    COLOR_BG_MID,
    COLOR_ERROR,
    COLOR_MUTED,
    apply_dark_title_bar,
)


def list_images(folder):
    folder = Path(folder)
    if not folder.is_dir():
        return []
    files = [
        path
        for path in folder.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_EXTS
    ]
    files.sort()
    return files


class LabelCanvas(QWidget):
    boxes_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(720, 420)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._pixmap = QPixmap()
        self._image_size = (0, 0)
        self._boxes = []
        self._selected = -1
        self._drag_origin = None
        self._drag_current = None
        self._changed = False

    def has_image(self):
        return not self._pixmap.isNull()

    def boxes(self):
        return list(self._boxes)

    def image_size(self):
        return self._image_size

    def is_dirty(self):
        return self._changed

    def mark_clean(self):
        self._changed = False

    def mark_dirty(self):
        self._changed = True

    def set_image(self, path):
        image = QImage(str(path))
        if image.isNull():
            self._pixmap = QPixmap()
            self._image_size = (0, 0)
            self._boxes = []
            self._selected = -1
            self._drag_origin = None
            self._drag_current = None
            self._changed = False
            self.update()
            return False
        self._pixmap = QPixmap.fromImage(image)
        self._image_size = (image.width(), image.height())
        self._boxes = load_boxes(path, image.width(), image.height())
        self._selected = -1
        self._drag_origin = None
        self._drag_current = None
        self._changed = False
        self.update()
        return True

    def clear_boxes(self):
        if self._boxes:
            self._boxes = []
            self._changed = True
            self.boxes_changed.emit()
        self._selected = -1
        self.update()

    def delete_selected(self):
        if 0 <= self._selected < len(self._boxes):
            del self._boxes[self._selected]
            self._selected = -1
            self._changed = True
            self.update()
            self.boxes_changed.emit()
            return True
        return False

    def _view_rect(self):
        width, height = self._image_size
        if width <= 0 or height <= 0:
            return QRectF(), 1.0
        scale = min(self.width() / width, self.height() / height)
        draw_w = width * scale
        draw_h = height * scale
        left = (self.width() - draw_w) / 2.0
        top = (self.height() - draw_h) / 2.0
        return QRectF(left, top, draw_w, draw_h), scale

    def _to_image(self, pos):
        rect, scale = self._view_rect()
        if scale <= 0 or rect.isEmpty():
            return None
        if not rect.contains(pos):
            x = min(max(pos.x(), rect.left()), rect.right())
            y = min(max(pos.y(), rect.top()), rect.bottom())
            pos = QPointF(x, y)
        ix = (pos.x() - rect.left()) / scale
        iy = (pos.y() - rect.top()) / scale
        width, height = self._image_size
        return QPointF(
            min(max(ix, 0.0), float(width)),
            min(max(iy, 0.0), float(height)),
        )

    def _to_view(self, x, y):
        rect, scale = self._view_rect()
        return QPointF(rect.left() + x * scale, rect.top() + y * scale)

    def _hit_box(self, image_pos):
        x, y = image_pos.x(), image_pos.y()
        best = -1
        best_area = None
        for index, (x1, y1, x2, y2) in enumerate(self._boxes):
            left, right = min(x1, x2), max(x1, x2)
            top, bottom = min(y1, y2), max(y1, y2)
            if left <= x <= right and top <= y <= bottom:
                area = (right - left) * (bottom - top)
                if best_area is None or area < best_area:
                    best = index
                    best_area = area
        return best

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#101A2E"))
        if self._pixmap.isNull():
            painter.setPen(QColor(COLOR_MUTED))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Fotoğraf yok")
            return
        rect, _scale = self._view_rect()
        painter.drawPixmap(rect.toRect(), self._pixmap)
        for index, (x1, y1, x2, y2) in enumerate(self._boxes):
            p1 = self._to_view(x1, y1)
            p2 = self._to_view(x2, y2)
            box = QRectF(p1, p2).normalized()
            selected = index == self._selected
            color = QColor(COLOR_ERROR if selected else COLOR_ACCENT)
            painter.setPen(QPen(color, 2.5 if selected else 2))
            painter.setBrush(QColor(color.red(), color.green(), color.blue(), 35))
            painter.drawRect(box)
            painter.setPen(color)
            painter.drawText(box.adjusted(4, 2, -4, 0), CLASS_NAME)
        if self._drag_origin is not None and self._drag_current is not None:
            p1 = self._to_view(self._drag_origin.x(), self._drag_origin.y())
            p2 = self._to_view(self._drag_current.x(), self._drag_current.y())
            painter.setPen(QPen(QColor(COLOR_ACCENT), 1.5, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(61, 223, 208, 40))
            painter.drawRect(QRectF(p1, p2).normalized())

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self.has_image():
            return
        image_pos = self._to_image(event.position())
        if image_pos is None:
            return
        hit = self._hit_box(image_pos)
        if hit >= 0:
            self._selected = hit
            self._drag_origin = None
            self.update()
            return
        self._selected = -1
        self._drag_origin = image_pos
        self._drag_current = image_pos
        self.update()

    def mouseMoveEvent(self, event):
        if self._drag_origin is None:
            return
        image_pos = self._to_image(event.position())
        if image_pos is None:
            return
        self._drag_current = image_pos
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self._drag_origin is None:
            return
        image_pos = self._to_image(event.position()) or self._drag_current
        origin = self._drag_origin
        self._drag_origin = None
        self._drag_current = None
        if image_pos is None:
            self.update()
            return
        x1, y1 = origin.x(), origin.y()
        x2, y2 = image_pos.x(), image_pos.y()
        if abs(x2 - x1) >= 8 and abs(y2 - y1) >= 8:
            self._boxes.append((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)))
            self._selected = len(self._boxes) - 1
            self._changed = True
            self.boxes_changed.emit()
        self.update()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
            return
        if event.key() == Qt.Key.Key_Escape:
            self._drag_origin = None
            self._drag_current = None
            self._selected = -1
            self.update()
            return
        event.ignore()


class LabelDialog(QDialog):
    def __init__(self, parent, folder):
        super().__init__(parent)
        self._folder = Path(folder)
        self._images = list_images(self._folder)
        self._index = 0
        self.setWindowTitle("Kareleri etiketle")
        self.setMinimumSize(980, 720)
        flags = self.windowFlags()
        flags &= ~Qt.WindowType.WindowContextHelpButtonHint
        self.setWindowFlags(flags)
        self._build()
        if self._images:
            self._show_index(0)

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)

        title = QLabel("Kareleri etiketle")
        title.setObjectName("titleLabel")
        root.addWidget(title)

        hint = QLabel(
            "İnsanın üzerine sürükleyerek kutu çizin. Sınıf otomatik «insan». "
            "A / D veya ok tuşları: önceki / sonraki. Del: seçili kutuyu sil. "
            "İnsan yoksa «İnsan yok» deyin."
        )
        hint.setWordWrap(True)
        hint.setObjectName("mutedLabel")
        root.addWidget(hint)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        self.canvas = LabelCanvas(self)
        self.canvas.boxes_changed.connect(self._refresh_status)
        frame = QFrame()
        frame.setObjectName("card")
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(8, 8, 8, 8)
        frame_layout.addWidget(self.canvas, 1)
        root.addWidget(frame, 1)

        nav = QHBoxLayout()
        self.prev_btn = QPushButton("◀ Önceki")
        self.prev_btn.setObjectName("secondaryButton")
        self.prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.prev_btn.clicked.connect(lambda: self._step(-1))
        self.next_btn = QPushButton("Sonraki ▶")
        self.next_btn.setObjectName("secondaryButton")
        self.next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.next_btn.clicked.connect(lambda: self._step(1))
        self.skip_btn = QPushButton("İnsan yok")
        self.skip_btn.setObjectName("secondaryButton")
        self.skip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.skip_btn.clicked.connect(self._mark_empty)
        self.unlabeled_btn = QPushButton("Sonraki etiketsiz")
        self.unlabeled_btn.setObjectName("secondaryButton")
        self.unlabeled_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.unlabeled_btn.clicked.connect(self._next_unlabeled)
        self.done_btn = QPushButton("Bitir")
        self.done_btn.setObjectName("primaryButton")
        self.done_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.done_btn.clicked.connect(self._finish)
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.next_btn)
        nav.addWidget(self.skip_btn)
        nav.addWidget(self.unlabeled_btn)
        nav.addStretch()
        nav.addWidget(self.done_btn)
        root.addLayout(nav)

    def _current_path(self):
        if not self._images:
            return None
        return self._images[self._index]

    def _labeled_count(self):
        return sum(1 for path in self._images if has_label(path))

    def _refresh_status(self):
        if not self._images:
            self.status.setText("Bu klasörde fotoğraf yok.")
            return
        path = self._current_path()
        boxes = self.canvas.boxes()
        self.status.setText(
            f"{path.name}   ·   kare {self._index + 1} / {len(self._images)}   ·   "
            f"{len(boxes)} kutu   ·   {self._labeled_count()} etiketli   ·   sınıf: {CLASS_NAME}"
        )

    def _save_current(self):
        path = self._current_path()
        if path is None or not self.canvas.has_image():
            return
        if not self.canvas.is_dirty():
            return
        width, height = self.canvas.image_size()
        save_boxes(path, self.canvas.boxes(), width, height)
        self.canvas.mark_clean()

    def _show_index(self, index):
        if not self._images:
            return
        self._index = max(0, min(index, len(self._images) - 1))
        path = self._images[self._index]
        if not self.canvas.set_image(path):
            self.status.setText(f"{path.name} açılamadı.")
            return
        self.canvas.setFocus()
        self._refresh_status()

    def _step(self, delta):
        if not self._images:
            return
        self._save_current()
        self._show_index(self._index + delta)

    def _mark_empty(self):
        self.canvas.clear_boxes()
        self.canvas.mark_dirty()
        self._save_current()
        self._step(1)

    def _next_unlabeled(self):
        self._save_current()
        if not self._images:
            return
        for offset in range(1, len(self._images) + 1):
            index = (self._index + offset) % len(self._images)
            if not has_label(self._images[index]):
                self._show_index(index)
                return
        show_warning(self, "Bitti", "Etiketsiz kare kalmadı.")

    def _finish(self):
        self._save_current()
        self.accept()

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key.Key_A, Qt.Key.Key_Left):
            self._step(-1)
            return
        if key in (Qt.Key.Key_D, Qt.Key.Key_Right):
            self._step(1)
            return
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.canvas.delete_selected()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        self._save_current()
        super().closeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        apply_dark_title_bar(self)
        self.canvas.setFocus()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0, QColor(COLOR_BG))
        grad.setColorAt(0.55, QColor(COLOR_BG_MID))
        grad.setColorAt(1, QColor(COLOR_BG))
        painter.fillRect(self.rect(), grad)
        super().paintEvent(event)
