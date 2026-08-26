from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QLinearGradient, QPainter
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from training.extract_frames import (
    DEFAULT_FRAMES,
    MAX_FRAMES,
    MIN_FRAMES,
    default_output_dir,
    extract_frames,
    probe_video,
)
from ui.dialogs import show_error, show_warning
from ui.theme import COLOR_ACCENT, COLOR_BG, COLOR_BG_MID, apply_dark_title_bar


class ExtractWorker(QThread):
    progressed = pyqtSignal(int, int, int)
    failed = pyqtSignal(str)
    succeeded = pyqtSignal(dict)

    def __init__(self, video_path, dest_dir, count, parent=None):
        super().__init__(parent)
        self._video_path = video_path
        self._dest_dir = dest_dir
        self._count = count
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            info = extract_frames(
                self._video_path,
                self._dest_dir,
                self._count,
                progress=lambda scanned, total, saved: self.progressed.emit(scanned, total, saved),
                should_cancel=lambda: self._cancel,
            )
            self.succeeded.emit(info)
        except Exception as exc:
            self.failed.emit(str(exc))


class ExtractFramesDialog(QDialog):
    def __init__(self, parent, app_dir, camera_name="kamera"):
        super().__init__(parent)
        self._app_dir = Path(app_dir)
        self._camera_name = camera_name or "kamera"
        self._video_path = ""
        self._worker = None
        self.output_dir = ""
        self.saved_count = 0
        self.setWindowTitle("Videodan kare çıkar")
        self.setMinimumSize(560, 420)
        flags = self.windowFlags()
        flags &= ~Qt.WindowType.WindowContextHelpButtonHint
        self.setWindowFlags(flags)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 16)
        root.setSpacing(12)

        title = QLabel("Videodan kare çıkar")
        title.setObjectName("titleLabel")
        root.addWidget(title)

        hint = QLabel(
            "Kayıt videosundan eşit aralıklı fotoğraf üretir. "
            "10 dakikalık videoda 300 kare ≈ her 2 saniyede bir kare. "
            "Ardışık binlerce kare çıkarmayın; çeşitlilik daha önemli."
        )
        hint.setWordWrap(True)
        hint.setObjectName("mutedLabel")
        root.addWidget(hint)

        card = QFrame()
        card.setObjectName("card")
        form = QVBoxLayout(card)
        form.setContentsMargins(16, 14, 16, 14)
        form.setSpacing(8)

        form.addWidget(self._step("1. Video seçin"))
        video_row = QHBoxLayout()
        self.video_label = QLabel("Henüz video seçilmedi")
        self.video_label.setObjectName("mutedLabel")
        self.video_label.setWordWrap(True)
        pick = QPushButton("Video seç…")
        pick.setObjectName("secondaryButton")
        pick.setCursor(Qt.CursorShape.PointingHandCursor)
        pick.clicked.connect(self._pick_video)
        video_row.addWidget(self.video_label, 1)
        video_row.addWidget(pick)
        form.addLayout(video_row)
        self.video_info = QLabel("")
        self.video_info.setWordWrap(True)
        self.video_info.setObjectName("mutedLabel")
        form.addWidget(self.video_info)

        form.addWidget(self._step("2. Kaç kare alınacak"))
        count_row = QHBoxLayout()
        self.count_spin = QSpinBox()
        self.count_spin.setRange(MIN_FRAMES, MAX_FRAMES)
        self.count_spin.setValue(DEFAULT_FRAMES)
        self.count_spin.setSuffix(" kare")
        count_row.addWidget(self.count_spin)
        count_row.addStretch()
        form.addLayout(count_row)

        form.addWidget(self._step("3. Kayıt klasörü"))
        self.dest_label = QLabel(str(self._planned_dest()))
        self.dest_label.setWordWrap(True)
        self.dest_label.setObjectName("mutedLabel")
        form.addWidget(self.dest_label)

        root.addWidget(card)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        root.addWidget(self.progress)
        self.status_label = QLabel("Video seçip Çıkar deyin.")
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("mutedLabel")
        root.addWidget(self.status_label)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel_btn = QPushButton("İptal")
        self.cancel_btn.setObjectName("secondaryButton")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.clicked.connect(self._on_cancel)
        self.start_btn = QPushButton("Kareleri çıkar")
        self.start_btn.setObjectName("primaryButton")
        self.start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_btn.clicked.connect(self._start)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.start_btn)
        root.addLayout(buttons)

    def _step(self, text):
        widget = QLabel(text)
        widget.setStyleSheet(f"font-weight: 700; color: {COLOR_ACCENT};")
        return widget

    def _planned_dest(self):
        return default_output_dir(self._app_dir, self._camera_name)

    def _videos_dir(self):
        path = self._app_dir / "videos"
        return str(path if path.is_dir() else self._app_dir)

    def _pick_video(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Video seçin",
            self._videos_dir(),
            "Video (*.mp4 *.avi *.mkv *.mov *.ts);;Tüm dosyalar (*.*)",
        )
        if not path:
            return
        try:
            info = probe_video(path)
        except Exception as exc:
            show_error(self, "Video okunamadı", str(exc))
            return
        self._video_path = path
        self.video_label.setText(path)
        duration = info.get("duration_sec") or 0
        minutes = duration / 60.0
        frames = info.get("frame_count") or 0
        if duration > 0:
            self.video_info.setText(
                f"Süre ≈ {minutes:.1f} dk, {frames} kare, "
                f"{info.get('width')}×{info.get('height')}."
            )
        else:
            self.video_info.setText(
                f"{info.get('width')}×{info.get('height')}. Süre okunamadı; eşit aralık yine denenir."
            )
        self.dest_label.setText(str(self._planned_dest()))

    def _set_busy(self, busy):
        self.start_btn.setEnabled(not busy)
        self.count_spin.setEnabled(not busy)

    def _start(self):
        if not self._video_path:
            show_warning(self, "Video yok", "Önce çıkarılacak videoyu seçin.")
            return
        dest = self._planned_dest()
        self.dest_label.setText(str(dest))
        self._set_busy(True)
        self.status_label.setText("Kareler çıkarılıyor…")
        self.progress.setValue(0)
        self._worker = ExtractWorker(self._video_path, dest, self.count_spin.value(), self)
        self._worker.progressed.connect(self._on_progress)
        self._worker.failed.connect(self._on_fail)
        self._worker.succeeded.connect(self._on_done)
        self._worker.start()

    def _on_progress(self, scanned, total, saved):
        total = max(1, int(total))
        self.progress.setValue(min(100, int(scanned * 100 / total)))
        self.status_label.setText(f"{saved} kare yazıldı…")

    def _on_fail(self, message):
        self._set_busy(False)
        self._worker = None
        self.status_label.setText(message)
        if "iptal" in (message or "").lower():
            self.reject()
            return
        show_error(self, "Kare çıkarılamadı", message)

    def _on_done(self, info):
        self._set_busy(False)
        self._worker = None
        saved = int(info.get("saved") or 0)
        folder = info.get("folder") or ""
        self.progress.setValue(100)
        if saved <= 0:
            show_error(self, "Kare yok", "Videodan fotoğraf yazılamadı.")
            return
        self.output_dir = folder
        self.saved_count = saved
        extra = ""
        if saved < self.count_spin.value():
            extra = f" İstenen {self.count_spin.value()} idi; video daha kısaydı."
        self.status_label.setText(f"{saved} kare kaydedildi.{extra}")
        if saved < MIN_FRAMES:
            show_warning(
                self,
                "Az kare",
                f"Sadece {saved} kare çıktı. Eğitim için en az {MIN_FRAMES} etiketli kare gerekir.",
            )
        self.accept()

    def _on_cancel(self):
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self.status_label.setText("Durduruluyor…")
            return
        self.reject()

    def closeEvent(self, event):
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(4000)
        super().closeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        apply_dark_title_bar(self)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0, QColor(COLOR_BG))
        grad.setColorAt(0.55, QColor(COLOR_BG_MID))
        grad.setColorAt(1, QColor(COLOR_BG))
        painter.fillRect(self.rect(), grad)
        super().paintEvent(event)
