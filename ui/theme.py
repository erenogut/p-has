import ctypes
import sys

from PyQt6.QtGui import QColor, QFont, QPalette
from PyQt6.QtWidgets import QApplication, QWidget

# Spec sapması: zemin / panel / input / vurgu
COLOR_BG = "#0B1220"
COLOR_BG_MID = "#101A2E"
COLOR_PANEL = "#162033"
COLOR_INPUT = "#1C2940"
COLOR_ACCENT = "#3DDFD0"
COLOR_ACCENT_HOVER = "#5EE8DC"
COLOR_ACCENT_PRESSED = "#2BC4B6"
COLOR_TEXT = "#E8EEF7"
COLOR_MUTED = "#8B9BB4"
COLOR_BORDER = "#2A3A55"
COLOR_ERROR = "#F07178"
COLOR_TITLE = "#F4F7FB"


def app_stylesheet():
    return f"""
        QWidget {{
            background-color: {COLOR_BG};
            color: {COLOR_TEXT};
            font-family: 'Segoe UI', sans-serif;
            font-size: 13px;
        }}
        QMainWindow, QDialog {{
            background-color: {COLOR_BG};
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 {COLOR_BG}, stop:0.55 {COLOR_BG_MID}, stop:1 {COLOR_BG});
            color: {COLOR_TEXT};
        }}
        QLabel {{
            background: transparent;
            color: {COLOR_TEXT};
        }}
        QLabel#mutedLabel {{
            color: {COLOR_MUTED};
            font-size: 12px;
        }}
        QLabel#errorLabel {{
            color: {COLOR_ERROR};
            font-size: 12px;
        }}
        QLabel#titleLabel {{
            color: {COLOR_TITLE};
            font-size: 20px;
            font-weight: 700;
        }}
        QFrame#card, QFrame#panel {{
            background-color: {COLOR_PANEL};
            border: 1px solid {COLOR_BORDER};
            border-radius: 12px;
        }}
        QLineEdit, QTextEdit, QPlainTextEdit, QTextBrowser, QSpinBox, QDoubleSpinBox,
        QComboBox, QListWidget, QDateEdit, QTimeEdit, QDateTimeEdit, QTableWidget {{
            background-color: {COLOR_INPUT};
            color: {COLOR_TEXT};
            border: 1px solid {COLOR_BORDER};
            border-radius: 8px;
            padding: 8px 10px;
            selection-background-color: {COLOR_ACCENT};
            selection-color: {COLOR_BG};
        }}
        QSpinBox, QDoubleSpinBox {{
            min-width: 76px;
            min-height: 36px;
            padding: 6px 10px;
        }}
        QTimeEdit {{
            min-width: 78px;
            min-height: 36px;
            padding: 6px 8px;
        }}
        QTableWidget QTimeEdit {{
            min-width: 72px;
            min-height: 30px;
            padding: 4px 6px;
            border-radius: 6px;
        }}
        QTableWidget QPushButton {{
            min-height: 30px;
            padding: 4px 8px;
        }}
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
        QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QListWidget:focus,
        QDateEdit:focus, QTableWidget:focus {{
            border: 1px solid {COLOR_ACCENT};
        }}
        QTableWidget {{
            padding: 0;
            gridline-color: {COLOR_BORDER};
            alternate-background-color: {COLOR_PANEL};
        }}
        QTableWidget::item {{
            padding: 6px 8px;
        }}
        QHeaderView::section {{
            background-color: {COLOR_PANEL};
            color: {COLOR_MUTED};
            border: none;
            border-bottom: 1px solid {COLOR_BORDER};
            padding: 8px;
            font-weight: 600;
        }}
        QTabWidget::pane {{
            border: 1px solid {COLOR_BORDER};
            border-radius: 10px;
            background: {COLOR_PANEL};
            top: -1px;
        }}
        QTabBar::tab {{
            background: {COLOR_INPUT};
            color: {COLOR_MUTED};
            border: 1px solid {COLOR_BORDER};
            border-bottom: none;
            padding: 8px 16px;
            margin-right: 4px;
            border-top-left-radius: 8px;
            border-top-right-radius: 8px;
        }}
        QTabBar::tab:selected {{
            background: {COLOR_PANEL};
            color: {COLOR_ACCENT};
        }}
        QScrollArea {{
            background: transparent;
            border: none;
        }}
        QScrollArea > QWidget > QWidget {{
            background: transparent;
        }}
        QComboBox {{
            padding-right: 24px;
        }}
        QComboBox::drop-down {{
            border: none;
            width: 22px;
        }}
        QComboBox::down-arrow {{
            width: 0;
            height: 0;
            border-left: 5px solid transparent;
            border-right: 5px solid transparent;
            border-top: 6px solid {COLOR_ACCENT};
            margin-right: 8px;
        }}
        QComboBox QAbstractItemView {{
            background-color: {COLOR_PANEL};
            color: {COLOR_TEXT};
            border: 1px solid {COLOR_BORDER};
            selection-background-color: {COLOR_ACCENT};
            selection-color: {COLOR_BG};
            outline: none;
        }}
        QListWidget::item {{
            padding: 5px;
            border-radius: 4px;
        }}
        QListWidget::item:selected {{
            background-color: {COLOR_PANEL};
            color: {COLOR_ACCENT};
            border: 1px solid {COLOR_ACCENT};
        }}
        QListWidget#cameraList::item {{
            padding: 0;
            border-radius: 6px;
        }}
        QListWidget#cameraList::item:selected {{
            background-color: transparent;
            border: none;
        }}
        QListWidget#peopleList::item {{
            padding: 10px 12px;
            border-radius: 6px;
            border: 1px solid transparent;
        }}
        QListWidget#peopleList::item:selected {{
            background-color: {COLOR_PANEL};
            color: {COLOR_ACCENT};
            border: 1px solid {COLOR_ACCENT};
            font-weight: 700;
        }}
        QCheckBox, QRadioButton {{
            background: transparent;
            color: {COLOR_TEXT};
            spacing: 8px;
        }}
        QCheckBox::indicator, QRadioButton::indicator, QListWidget::indicator {{
            width: 16px;
            height: 16px;
            border: 1px solid {COLOR_BORDER};
            border-radius: 4px;
            background: {COLOR_INPUT};
        }}
        QCheckBox::indicator:checked, QRadioButton::indicator:checked, QListWidget::indicator:checked {{
            background: {COLOR_ACCENT};
            border: 1px solid {COLOR_ACCENT};
        }}
        QPushButton {{
            background-color: {COLOR_INPUT};
            color: {COLOR_TEXT};
            border: 1px solid {COLOR_BORDER};
            border-radius: 8px;
            padding: 10px 16px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            border: 1px solid {COLOR_ACCENT};
            color: {COLOR_ACCENT};
        }}
        QPushButton:pressed {{
            background-color: {COLOR_PANEL};
        }}
        QPushButton#primaryButton {{
            background-color: {COLOR_ACCENT};
            color: {COLOR_BG};
            border: none;
        }}
        QPushButton#primaryButton:hover {{
            background-color: {COLOR_ACCENT_HOVER};
            color: {COLOR_BG};
        }}
        QPushButton#primaryButton:pressed {{
            background-color: {COLOR_ACCENT_PRESSED};
        }}
        QPushButton#primaryButton:disabled {{
            background-color: {COLOR_BORDER};
            color: {COLOR_MUTED};
        }}
        QPushButton#secondaryButton {{
            background-color: transparent;
            color: {COLOR_ACCENT};
            border: 1px solid {COLOR_ACCENT};
        }}
        QPushButton#secondaryButton:disabled {{
            color: {COLOR_MUTED};
            border: 1px solid {COLOR_BORDER};
        }}
        QPushButton#secondaryButton:hover {{
            background-color: {COLOR_PANEL};
        }}
        QScrollBar:vertical {{
            background: {COLOR_PANEL};
            width: 10px;
            margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background: {COLOR_BORDER};
            min-height: 24px;
            border-radius: 5px;
        }}
        QScrollBar:horizontal {{
            background: {COLOR_PANEL};
            height: 10px;
        }}
        QScrollBar::handle:horizontal {{
            background: {COLOR_BORDER};
            min-width: 24px;
            border-radius: 5px;
        }}
        QScrollBar::add-line, QScrollBar::sub-line {{
            width: 0;
            height: 0;
        }}
        QMenu {{
            background-color: {COLOR_PANEL};
            color: {COLOR_TEXT};
            border: 1px solid {COLOR_BORDER};
            padding: 4px;
        }}
        QMenu::item {{
            padding: 8px 18px;
            border-radius: 4px;
        }}
        QMenu::item:selected {{
            background-color: {COLOR_INPUT};
            color: {COLOR_ACCENT};
        }}
        QProgressBar {{
            background-color: {COLOR_INPUT};
            color: {COLOR_TEXT};
            border: 1px solid {COLOR_BORDER};
            border-radius: 8px;
            text-align: center;
            min-height: 18px;
        }}
        QProgressBar::chunk {{
            background-color: {COLOR_ACCENT};
            border-radius: 7px;
        }}
        QToolTip {{
            background-color: {COLOR_PANEL};
            color: {COLOR_TEXT};
            border: 1px solid {COLOR_ACCENT};
            padding: 10px 12px;
            font-size: 13px;
        }}
        QMessageBox, QInputDialog, QFileDialog {{
            background-color: {COLOR_PANEL};
            color: {COLOR_TEXT};
        }}
        QMessageBox QLabel, QInputDialog QLabel {{
            color: {COLOR_TEXT};
            background: transparent;
        }}
    """


def apply_dark_palette(app: QApplication):
    palette = QPalette()
    bg = QColor(COLOR_BG)
    panel = QColor(COLOR_PANEL)
    text = QColor(COLOR_TEXT)
    accent = QColor(COLOR_ACCENT)
    input_bg = QColor(COLOR_INPUT)
    muted = QColor(COLOR_MUTED)

    palette.setColor(QPalette.ColorRole.Window, bg)
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Base, input_bg)
    palette.setColor(QPalette.ColorRole.AlternateBase, panel)
    palette.setColor(QPalette.ColorRole.ToolTipBase, panel)
    palette.setColor(QPalette.ColorRole.ToolTipText, text)
    palette.setColor(QPalette.ColorRole.Text, text)
    palette.setColor(QPalette.ColorRole.Button, panel)
    palette.setColor(QPalette.ColorRole.ButtonText, text)
    palette.setColor(QPalette.ColorRole.BrightText, accent)
    palette.setColor(QPalette.ColorRole.Highlight, accent)
    palette.setColor(QPalette.ColorRole.HighlightedText, bg)
    palette.setColor(QPalette.ColorRole.PlaceholderText, muted)
    palette.setColor(QPalette.ColorRole.Link, accent)
    app.setPalette(palette)


def apply_dark_title_bar(widget: QWidget):
    if sys.platform != "win32":
        return
    try:
        hwnd = int(widget.winId())
        value = ctypes.c_int(1)
        dwmapi = ctypes.windll.dwmapi
        for attr in (20, 19):
            dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


def apply_app_theme(app: QApplication):
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    apply_dark_palette(app)
    app.setStyleSheet(app_stylesheet())
