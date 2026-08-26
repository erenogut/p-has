from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QLinearGradient, QPainter
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from ui.theme import COLOR_ACCENT, COLOR_BG, COLOR_BG_MID, COLOR_ERROR, apply_dark_title_bar


class ThemedMessageDialog(QDialog):
    def __init__(self, parent, title, message, kind="info"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(380)
        self.setMaximumWidth(520)
        flags = self.windowFlags()
        flags &= ~Qt.WindowType.WindowContextHelpButtonHint
        self.setWindowFlags(flags)
        self._build(title, message, kind)

    def _build(self, title, message, kind):
        accent = COLOR_ERROR if kind == "error" else COLOR_ACCENT
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(12)

        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 16, 18, 16)
        card_layout.setSpacing(10)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("titleLabel")
        title_lbl.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {accent};")
        title_lbl.setWordWrap(True)

        body = QLabel(message)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        card_layout.addWidget(title_lbl)
        card_layout.addWidget(body)
        root.addWidget(card)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("Tamam")
        ok_btn.setObjectName("primaryButton")
        ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_btn.setMinimumWidth(110)
        ok_btn.clicked.connect(self.accept)
        ok_btn.setDefault(True)
        btn_row.addWidget(ok_btn)
        root.addLayout(btn_row)

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


def _exec_dialog(parent, title, message, kind):
    dlg = ThemedMessageDialog(parent, title, message, kind)
    return dlg.exec()


def show_info(parent, title, message):
    return _exec_dialog(parent, title, message, "info")


def show_warning(parent, title, message):
    return _exec_dialog(parent, title, message, "warning")


def show_error(parent, title, message):
    return _exec_dialog(parent, title, message, "error")


class ThemedConfirmDialog(ThemedMessageDialog):
    def _build(self, title, message, kind):
        accent = COLOR_ERROR if kind == "error" else COLOR_ACCENT
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(12)

        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 16, 18, 16)
        card_layout.setSpacing(10)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("titleLabel")
        title_lbl.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {accent};")
        title_lbl.setWordWrap(True)

        body = QLabel(message)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        card_layout.addWidget(title_lbl)
        card_layout.addWidget(body)
        root.addWidget(card)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("İptal")
        cancel_btn.setObjectName("secondaryButton")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setMinimumWidth(110)
        cancel_btn.clicked.connect(self.reject)
        ok_btn = QPushButton("Evet")
        ok_btn.setObjectName("primaryButton")
        ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_btn.setMinimumWidth(110)
        ok_btn.clicked.connect(self.accept)
        ok_btn.setDefault(True)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        root.addLayout(btn_row)


def ask_confirm(parent, title, message, kind="warning"):
    dialog = ThemedConfirmDialog(parent, title, message, kind)
    return dialog.exec() == QDialog.DialogCode.Accepted


class ThemedInputDialog(QDialog):
    def __init__(self, parent, title, message, default=""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(380)
        self.setMaximumWidth(520)
        flags = self.windowFlags()
        flags &= ~Qt.WindowType.WindowContextHelpButtonHint
        flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self._build(title, message, default)

    def _build(self, title, message, default):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(12)

        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 16, 18, 16)
        card_layout.setSpacing(10)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("titleLabel")
        title_lbl.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {COLOR_ACCENT};")
        title_lbl.setWordWrap(True)

        body = QLabel(message)
        body.setWordWrap(True)

        self.input = QLineEdit()
        self.input.setText(default or "")
        self.input.setMaxLength(100)
        self.input.selectAll()
        self.input.returnPressed.connect(self.accept)

        card_layout.addWidget(title_lbl)
        card_layout.addWidget(body)
        card_layout.addWidget(self.input)
        root.addWidget(card)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("İptal")
        cancel_btn.setObjectName("secondaryButton")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setMinimumWidth(110)
        cancel_btn.clicked.connect(self.reject)
        ok_btn = QPushButton("Tamam")
        ok_btn.setObjectName("primaryButton")
        ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_btn.setMinimumWidth(110)
        ok_btn.clicked.connect(self.accept)
        ok_btn.setDefault(True)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        root.addLayout(btn_row)

    def value(self):
        return self.input.text()

    def showEvent(self, event):
        super().showEvent(event)
        apply_dark_title_bar(self)
        self.input.setFocus()
        self.input.selectAll()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0, QColor(COLOR_BG))
        grad.setColorAt(0.55, QColor(COLOR_BG_MID))
        grad.setColorAt(1, QColor(COLOR_BG))
        painter.fillRect(self.rect(), grad)
        super().paintEvent(event)


def ask_text(parent, title, message, default=""):
    dlg = ThemedInputDialog(parent, title, message, default)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        return dlg.value()
    return None
