import time

import cv2
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ui.dialogs import show_error
from ui.theme import COLOR_BG, COLOR_BG_MID, COLOR_MUTED, apply_dark_title_bar


class RtspPreviewWorker(QThread):
    frame_ready = pyqtSignal(QImage)
    failed = pyqtSignal(str)

    def __init__(self, url, parent=None):
        super().__init__(parent)
        self._url = url
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        cap = cv2.VideoCapture(self._url)
        try:
            if cap is None or not cap.isOpened():
                if not self._stop:
                    self.failed.emit("Kameraya bağlanılamadı.")
                return
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass

            min_interval = 1.0 / 15.0
            last_emit = 0.0
            while not self._stop:
                ok, frame = cap.read()
                if not ok or frame is None:
                    if not self._stop:
                        self.failed.emit("Canlı görüntü koptu.")
                    break
                now = time.time()
                if now - last_emit < min_interval:
                    time.sleep(0.01)
                    continue
                last_emit = now
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                height, width, channels = rgb.shape
                qimg = QImage(
                    rgb.data,
                    width,
                    height,
                    channels * width,
                    QImage.Format.Format_RGB888,
                ).copy()
                self.frame_ready.emit(qimg)
        finally:
            if cap is not None:
                cap.release()


class CameraPreviewDialog(QDialog):
    def __init__(self, parent, camera_name, channel_id, rtsp_url):
        super().__init__(parent)
        self._channel_id = str(channel_id)
        self._rtsp_url = rtsp_url
        self._worker = None
        self._closing = False
        self.setWindowTitle(camera_name or f"Kamera {channel_id}")
        self.setMinimumSize(640, 420)
        flags = self.windowFlags()
        flags &= ~Qt.WindowType.WindowContextHelpButtonHint
        self.setWindowFlags(flags)
        self._build(camera_name)

    def _build(self, camera_name):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(12)

        title = QLabel(camera_name or "")
        title.setObjectName("titleLabel")
        title.setWordWrap(True)
        root.addWidget(title)

        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 12, 12, 12)

        self.status_label = QLabel("Bağlanılıyor…")
        self.status_label.setObjectName("mutedLabel")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumSize(560, 280)
        self.image_label.setStyleSheet(f"color: {COLOR_MUTED}; background: transparent;")

        card_layout.addWidget(self.status_label)
        card_layout.addWidget(self.image_label, 1)
        root.addWidget(card, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        refresh_btn = QPushButton("Yenile")
        refresh_btn.setObjectName("secondaryButton")
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.clicked.connect(self._start_stream)
        close_btn = QPushButton("Kapat")
        close_btn.setObjectName("primaryButton")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(refresh_btn)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    def showEvent(self, event):
        super().showEvent(event)
        apply_dark_title_bar(self)
        if self._worker is None:
            self._start_stream()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0, QColor(COLOR_BG))
        grad.setColorAt(0.55, QColor(COLOR_BG_MID))
        grad.setColorAt(1, QColor(COLOR_BG))
        painter.fillRect(self.rect(), grad)
        super().paintEvent(event)

    def closeEvent(self, event):
        self._closing = True
        self._stop_worker()
        super().closeEvent(event)

    def accept(self):
        self._closing = True
        self._stop_worker()
        super().accept()

    def reject(self):
        self._closing = True
        self._stop_worker()
        super().reject()

    def _stop_worker(self):
        worker = self._worker
        self._worker = None
        if worker is None:
            return
        worker.stop()
        try:
            worker.frame_ready.disconnect()
        except TypeError:
            pass
        try:
            worker.failed.disconnect()
        except TypeError:
            pass
        worker.quit()
        worker.wait(2500)

    def _start_stream(self):
        self._stop_worker()
        self._closing = False
        self.status_label.setText("Bağlanılıyor…")
        self.status_label.show()
        worker = RtspPreviewWorker(self._rtsp_url, self)
        worker.frame_ready.connect(self._on_frame)
        worker.failed.connect(self._on_fail)
        self._worker = worker
        worker.start()

    def _on_frame(self, image):
        if image is None or image.isNull():
            return
        target = self.image_label.size()
        pixmap = QPixmap.fromImage(image)
        self.image_label.setPixmap(
            pixmap.scaled(
                target,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self.status_label.setText("")
        self.status_label.hide()

    def _on_fail(self, message):
        self.status_label.show()
        self.status_label.setText(message or "Canlı görüntü alınamadı.")
        if self.isVisible() and not self._closing:
            show_error(self, "Önizleme", message or "Canlı görüntü alınamadı.")
