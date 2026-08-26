import hashlib
import hmac

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QKeyEvent, QLinearGradient, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from ui.app_info import APP_DISPLAY_NAME, APP_PUBLISHER, APP_VERSION
from ui.paths import asset_path
from ui.theme import (
    COLOR_ACCENT,
    COLOR_BG,
    COLOR_BG_MID,
    COLOR_MUTED,
)

ADMIN_USERNAME = "admin"
PASSWORD_SHA256 = "40ebe7f7aa495a96fd0921e73985fa668c52b781c0de7f95310cb56ebe4d2871"
MAX_ATTEMPTS = 3
ERROR_TEXT = "Kullanıcı adı veya şifre hatalı."
LOCKOUT_SUFFIX = " (Yetkisiz erişim engellenir.)"


def verify_credentials(username, password):
    if username.strip() != ADMIN_USERNAME:
        return False
    digest = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return hmac.compare_digest(digest, PASSWORD_SHA256)


class LoginWindow(QDialog):
    def __init__(self, parent=None, exit_on_reject=True):
        super().__init__(parent)
        self.is_authenticated = False
        self.exit_on_reject = exit_on_reject
        self._failed_attempts = 0
        self._drag_pos = None
        self.setObjectName("loginDialog")
        self.setWindowTitle("Giriş")
        self.setFixedSize(440, 580)
        self.setModal(True)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 32, 36, 28)
        root.setSpacing(10)

        logo = QLabel()
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = QPixmap(asset_path("logo.png"))
        if not pixmap.isNull():
            logo.setPixmap(
                pixmap.scaled(
                    72,
                    72,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            logo.setText("PH")
            logo.setStyleSheet(f"color: {COLOR_ACCENT}; font-size: 28px; font-weight: 700;")
        logo.setFixedHeight(76)
        root.addWidget(logo)

        title = QLabel(APP_DISPLAY_NAME)
        title.setObjectName("titleLabel")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(title)

        subtitle = QLabel("Personel Takip Sistemi")
        subtitle.setObjectName("mutedLabel")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(subtitle)
        root.addSpacing(8)

        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 20, 20, 16)
        card_layout.setSpacing(8)

        user_lbl = QLabel("Kullanıcı adı")
        user_lbl.setStyleSheet(f"color: {COLOR_MUTED}; background: transparent;")
        self.user_input = QLineEdit()
        self.user_input.setText(ADMIN_USERNAME)
        self.user_input.setPlaceholderText("Kullanıcı adı")
        self.user_input.returnPressed.connect(self.check_login)

        pass_lbl = QLabel("Şifre")
        pass_lbl.setStyleSheet(f"color: {COLOR_MUTED}; background: transparent;")
        self.pass_input = QLineEdit()
        self.pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.pass_input.setPlaceholderText("Şifre")
        self.pass_input.returnPressed.connect(self.check_login)

        self.error_label = QLabel("")
        self.error_label.setObjectName("errorLabel")
        self.error_label.setWordWrap(True)
        self.error_label.setMinimumHeight(36)
        self.error_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        card_layout.addWidget(user_lbl)
        card_layout.addWidget(self.user_input)
        card_layout.addSpacing(4)
        card_layout.addWidget(pass_lbl)
        card_layout.addWidget(self.pass_input)
        card_layout.addWidget(self.error_label)
        root.addWidget(card)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.btn_login = QPushButton("Giriş yap")
        self.btn_login.setObjectName("primaryButton")
        self.btn_login.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_login.setMinimumHeight(42)
        self.btn_login.clicked.connect(self.check_login)

        self.btn_cancel = QPushButton("İptal")
        self.btn_cancel.setObjectName("secondaryButton")
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.setMinimumHeight(42)
        self.btn_cancel.clicked.connect(self._cancel)

        btn_row.addWidget(self.btn_login)
        btn_row.addWidget(self.btn_cancel)
        root.addLayout(btn_row)

        footer = QLabel(f"{APP_PUBLISHER}  ·  v{APP_VERSION}")
        footer.setObjectName("mutedLabel")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addStretch()
        root.addWidget(footer)

    def showEvent(self, event):
        super().showEvent(event)
        screen = self.screen() or self.windowHandle().screen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(
                geo.x() + (geo.width() - self.width()) // 2,
                geo.y() + (geo.height() - self.height()) // 2,
            )
        self.user_input.setText(ADMIN_USERNAME)
        self.pass_input.setFocus()
        self.pass_input.selectAll()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0.0, QColor(COLOR_BG))
        grad.setColorAt(0.45, QColor(COLOR_BG_MID))
        grad.setColorAt(1.0, QColor(COLOR_BG))
        painter.fillRect(self.rect(), grad)

        glow = QLinearGradient(0, 0, 0, 170)
        glow.setColorAt(0.0, QColor(61, 223, 208, 38))
        glow.setColorAt(1.0, QColor(61, 223, 208, 0))
        painter.fillRect(0, 0, self.width(), 170, glow)
        super().paintEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Escape:
            self._cancel()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        if not self.is_authenticated:
            self.reject()
        event.accept()

    def _cancel(self):
        self.is_authenticated = False
        self.reject()

    def check_login(self):
        username = self.user_input.text()
        password = self.pass_input.text()
        if verify_credentials(username, password):
            self.is_authenticated = True
            self.error_label.clear()
            self.accept()
            return

        self._failed_attempts += 1
        if self._failed_attempts >= MAX_ATTEMPTS:
            self.error_label.setText(ERROR_TEXT + LOCKOUT_SUFFIX)
            self.btn_login.setEnabled(False)
            self.pass_input.setEnabled(False)
            self.user_input.setEnabled(False)
            QTimer.singleShot(1200, self._cancel)
            return

        self.error_label.setText(ERROR_TEXT)
        self.pass_input.selectAll()
        self.pass_input.setFocus()
