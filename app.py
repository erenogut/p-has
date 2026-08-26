import cv2
import numpy as np
import pyodbc
from datetime import datetime, timedelta
import uuid
from ultralytics import YOLO
from collections import deque
import os
import sys
import json
import signal
import subprocess
import re
import requests
from requests.auth import HTTPDigestAuth
import xml.etree.ElementTree as ET
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QLineEdit, QDoubleSpinBox, QPushButton, QFormLayout, QDialog,
                             QSystemTrayIcon, QMenu, QCheckBox, QAbstractSpinBox,
                             QListWidget, QListWidgetItem, QSizePolicy, QFrame)
from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QFont, QIcon
from ui.theme import (
    COLOR_ACCENT,
    COLOR_PANEL,
    apply_app_theme,
    apply_dark_title_bar,
)
from ui.login_window import LoginWindow
from ui.dialogs import ask_text, show_warning, show_info, show_error
from ui.preview_dialog import CameraPreviewDialog
from ui.train_dialog import ModelTrainDialog
from ui.report_dialog import ReportDialog
from ui.shift_dialog import ShiftDialog
from ui.shift_store import normalize_people
from ui.paths import runtime_dir, asset_path
from training.hard_frames import HardFrameSink, classify_hard_reason
import threading
import time
import queue

stop_ai_flag = False
pipeline_running = False
pipeline_finished = threading.Event()
CAMERA_NAME_ROLE = Qt.ItemDataRole.UserRole + 1
_video_finalizers = []
_video_finalizers_lock = threading.Lock()
_ctrl_handler_ref = None
_report_dialog = None


def register_video_finalizer(fn):
    with _video_finalizers_lock:
        if fn not in _video_finalizers:
            _video_finalizers.append(fn)


def unregister_video_finalizer(fn):
    with _video_finalizers_lock:
        if fn in _video_finalizers:
            _video_finalizers.remove(fn)


def finalize_all_videos():
    with _video_finalizers_lock:
        fns = list(_video_finalizers)
    for fn in fns:
        try:
            fn()
        except Exception:
            pass


def _safe_console_note(text):
    try:
        os.write(2, (text + "\n").encode("utf-8", errors="replace"))
    except Exception:
        pass


def trigger_shutdown(*args, from_console=False):
    global stop_ai_flag
    stop_ai_flag = True
    if from_console:
        _safe_console_note("[BILGI] Kapanis: videolar kaydediliyor, bekleyin...")
    else:
        print("\n[BİLGİ] Kapanış protokolü başlatıldı. Lütfen video ve veritabanı kaydedilene kadar bekleyin...")
    if not pipeline_running:
        os._exit(0)


def _console_ctrl_handler(ctrl_type):
    # 0=Ctrl+C, 1=Ctrl+Break, 2=konsol kapatıldı, 5=logoff, 6=shutdown
    closing = ctrl_type in (2, 5, 6)
    trigger_shutdown(from_console=True)
    finalize_all_videos()
    pipeline_finished.wait(timeout=4.0 if closing else 30.0)
    if closing:
        os._exit(0)
    return True


def install_windows_console_handler():
    """Konsol X ile kapatılınca Windows süreci öldürmeden önce videoları kapat."""
    global _ctrl_handler_ref
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
    _ctrl_handler_ref = handler_type(_console_ctrl_handler)
    kernel32 = ctypes.windll.kernel32
    kernel32.SetConsoleCtrlHandler.argtypes = [handler_type, wintypes.BOOL]
    kernel32.SetConsoleCtrlHandler.restype = wintypes.BOOL
    kernel32.SetConsoleCtrlHandler(_ctrl_handler_ref, True)


signal.signal(signal.SIGINT, trigger_shutdown)
if hasattr(signal, "SIGBREAK"):
    signal.signal(signal.SIGBREAK, trigger_shutdown)

# Optional JIT acceleration with numba
try:
    from numba import njit
    _NUMBA_AVAILABLE = True
except ImportError:
    _NUMBA_AVAILABLE = False

# Global assignment gives more stable IDs than pair-by-pair greedy matching.
# SciPy is normally installed with Ultralytics; a deterministic fallback is
# provided below so the application still runs without it.
try:
    from scipy.optimize import linear_sum_assignment
    _HUNGARIAN_AVAILABLE = True
except ImportError:
    linear_sum_assignment = None
    _HUNGARIAN_AVAILABLE = False

# ============================================================================
#  AYARLAR (SETTINGS) - config.json Destekli
# ============================================================================

APP_DIR = runtime_dir()
CONFIG_FILE = os.path.join(APP_DIR, "config.json")

# -- Varsayılan Ayarlar (Eğer config.json yoksa bu kullanılır) --
DEFAULT_CONFIG = {
    "RTSP_USER": "admin",
    "RTSP_PASS": "password123",
    "RTSP_IP": "192.168.1.100",
    "RTSP_CHANNEL": "101",
    "SELECTED_CAMERAS": ["101"],
    "CAMERAS": {},
    "VIDEO_SAVE_ENABLED": True,
    "HARD_FRAMES_ENABLED": True,
    "KOPRU_SURESI_SN": 1.0,
    "HAYALET_ESIK_SN": 2.0,
    "MIN_BOLGE_KALMA_SN": 2.0,
    "MAX_KAYIP_SURESI_SN": 12.0,
    "CONFIDENCE": 0.5,
    "SQL_SERVER": "localhost",
    "SQL_DATABASE": "MY_DATABASE",
    "SQL_USER": "sa",
    "SQL_PASSWORD": "StrongPassword123!",
    "SQL_PORT": 1433,
    "SHIFT_PEOPLE": [],
}

ZONE_NAME_MAX = 100


def default_zone_name(zone_id):
    return f"Zone {zone_id}"


def sanitize_zone_name(name, zone_id):
    text = str(name or "").strip()
    if not text:
        text = default_zone_name(zone_id)
    return text[:ZONE_NAME_MAX]


def zone_points(entry):
    if isinstance(entry, dict):
        pts = entry.get("points")
    elif isinstance(entry, (list, tuple)):
        pts = entry
    else:
        pts = None
    if not pts:
        return []
    return [list(pt) for pt in pts]


def zone_display_name(zone_id, entry):
    if isinstance(entry, dict):
        return sanitize_zone_name(entry.get("name"), zone_id)
    return default_zone_name(zone_id)


def zone_numeric_id(zone_id):
    try:
        return int(zone_id)
    except (TypeError, ValueError):
        return 0


def normalize_zone_map(raw):
    if not isinstance(raw, dict):
        return {}
    normalized = {}
    for zid, entry in raw.items():
        key = str(zid)
        pts = zone_points(entry)
        if len(pts) < 3:
            continue
        normalized[key] = {
            "name": zone_display_name(key, entry),
            "points": pts,
        }
    return normalized


def report_zone_name(report, zone_id):
    names = report.get("bolge_adlari") or {}
    return names.get(zone_id) or names.get(str(zone_id)) or default_zone_name(zone_id)


def migrate_config(config_data):
    """Eski tek-kanal CUSTOM_ZONES şemasını CAMERAS + SELECTED_CAMERAS yapısına taşır."""
    migrated = dict(config_data) if config_data else {}
    cameras = migrated.get("CAMERAS")
    if not isinstance(cameras, dict):
        cameras = {}

    channel = str(migrated.get("RTSP_CHANNEL", DEFAULT_CONFIG["RTSP_CHANNEL"]) or "")
    custom_zones = migrated.get("CUSTOM_ZONES")
    if isinstance(custom_zones, dict) and channel:
        entry = dict(cameras.get(channel, {}))
        if not entry.get("zones"):
            entry.setdefault("name", f"Kamera {channel}")
            entry["zones"] = custom_zones
            cameras[channel] = entry

    selected = migrated.get("SELECTED_CAMERAS")
    if not isinstance(selected, list) or not selected:
        selected = [channel] if channel else []

    for ch_id, cam in list(cameras.items()):
        if not isinstance(cam, dict):
            continue
        cam = dict(cam)
        cam["zones"] = normalize_zone_map(cam.get("zones"))
        cameras[ch_id] = cam

    migrated["CAMERAS"] = cameras
    migrated["SELECTED_CAMERAS"] = [str(x) for x in selected]
    if isinstance(migrated.get("CUSTOM_ZONES"), dict):
        migrated["CUSTOM_ZONES"] = normalize_zone_map(migrated["CUSTOM_ZONES"])
    for key in ("SQL_SERVER", "SQL_DATABASE", "SQL_USER", "SQL_PASSWORD", "SQL_PORT"):
        if key not in migrated:
            migrated[key] = DEFAULT_CONFIG[key]
    if "HARD_FRAMES_ENABLED" not in migrated:
        migrated["HARD_FRAMES_ENABLED"] = DEFAULT_CONFIG["HARD_FRAMES_ENABLED"]
    migrated["SHIFT_PEOPLE"] = normalize_people(migrated.get("SHIFT_PEOPLE"))
    return migrated

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return migrate_config(json.load(f))
        except Exception as e:
            print(f"Config okuma hatası: {e}. Varsayılanlar kullanılıyor.")
    return migrate_config(DEFAULT_CONFIG.copy())

def save_config(config_data):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=4, ensure_ascii=False)

def build_rtsp_url(user, password, ip, channel):
    return f"rtsp://{user}:{password}@{ip}:554/Streaming/Channels/{channel}"

def get_camera_entry(channel_id, config_data=None):
    data = config_data if config_data is not None else CONFIG
    cameras = data.get("CAMERAS", {})
    entry = cameras.get(str(channel_id), {})
    if not isinstance(entry, dict):
        return {"name": f"Kamera {channel_id}", "zones": {}}
    return entry

def get_camera_zones(channel_id, config_data=None):
    zones = get_camera_entry(channel_id, config_data).get("zones", {})
    return normalize_zone_map(zones)

def resolve_model_path():
    """Önce TensorRT engine, yoksa/yüklenemezse .pt ağırlığını dener."""
    preferred = MODEL_YOLU if MODEL_YOLU else "best.engine"
    candidates = []
    for path in (preferred, "best.engine", "best.pt"):
        if path and path not in candidates:
            candidates.append(path)

    existing = []
    for path in candidates:
        full = path if os.path.isabs(path) else os.path.join(APP_DIR, path)
        if os.path.exists(full):
            existing.append(full)
    if not existing:
        return None, (
            "Model dosyası bulunamadı. Proje klasörüne best.engine veya best.pt koyun."
        )
    return existing[0], None

def load_yolo_model():
    path, missing_error = resolve_model_path()
    if missing_error:
        print(f"[HATA] {missing_error}")
        return None

    try:
        print(f"[BİLGİ] YOLO modeli yükleniyor: {path}")
        return YOLO(path)
    except Exception as first_error:
        pt_fallback = os.path.join(APP_DIR, "best.pt")
        if path.endswith(".engine") and os.path.exists(pt_fallback):
            print(f"[UYARI] {path} yüklenemedi ({first_error}). best.pt deneniyor...")
            try:
                return YOLO(pt_fallback)
            except Exception as second_error:
                print(f"[HATA] best.pt de yüklenemedi: {second_error}")
                return None
        print(f"[HATA] Model yüklenemedi ({path}): {first_error}")
        return None


def warmup_yolo_model(model, infer_lock=None, prefix="[BİLGİ]"):
    """İlk TensorRT/CUDA çıkarımını kayıt başlamadan bu thread'de yap."""
    if model is None:
        return
    dummy = np.full((OUTPUT_HEIGHT, OUTPUT_WIDTH, 3), 32, dtype=np.uint8)
    print(f"{prefix} YOLO/TensorRT ısındırılıyor (ilk çıkarım GPU'ya yükleniyor)...")
    t0 = time.time()
    try:
        if infer_lock is None:
            model(dummy, conf=0.25, imgsz=IMGSZ, verbose=False, device=0)
        else:
            with infer_lock:
                model(dummy, conf=0.25, imgsz=IMGSZ, verbose=False, device=0)
        print(f"{prefix} YOLO hazır ({time.time() - t0:.1f} sn). Kayıt başlıyor.")
    except Exception as exc:
        print(f"{prefix} UYARI: YOLO ısınma atlandı ({exc}).")

# Config'i yükle
CONFIG = load_config()

# Global değişkenleri ayarla
RTSP_USER = CONFIG.get("RTSP_USER", DEFAULT_CONFIG["RTSP_USER"])
RTSP_PASS = CONFIG.get("RTSP_PASS", DEFAULT_CONFIG["RTSP_PASS"])
RTSP_IP = CONFIG.get("RTSP_IP", DEFAULT_CONFIG["RTSP_IP"])
RTSP_CHANNEL = CONFIG.get("RTSP_CHANNEL", DEFAULT_CONFIG["RTSP_CHANNEL"])
VIDEO_SAVE_ENABLED = CONFIG.get("VIDEO_SAVE_ENABLED", DEFAULT_CONFIG["VIDEO_SAVE_ENABLED"])
HARD_FRAMES_ENABLED = CONFIG.get("HARD_FRAMES_ENABLED", DEFAULT_CONFIG["HARD_FRAMES_ENABLED"])
KOPRU_SURESI_SN = float(CONFIG.get("KOPRU_SURESI_SN", DEFAULT_CONFIG["KOPRU_SURESI_SN"]))
HAYALET_ESIK_SN = float(CONFIG.get("HAYALET_ESIK_SN", DEFAULT_CONFIG["HAYALET_ESIK_SN"]))
MIN_BOLGE_KALMA_SN = float(CONFIG.get("MIN_BOLGE_KALMA_SN", DEFAULT_CONFIG["MIN_BOLGE_KALMA_SN"]))
MAX_KAYIP_SURESI_SN = float(CONFIG.get("MAX_KAYIP_SURESI_SN", DEFAULT_CONFIG["MAX_KAYIP_SURESI_SN"]))
CONFIDENCE = float(CONFIG.get("CONFIDENCE", DEFAULT_CONFIG["CONFIDENCE"]))

# -- Sabit Ayarlar (Değiştirilmedi) --
MIN_BBOX_ALAN = 3000
AGIRLIK_MESAFE = 0.30
AGIRLIK_IOU = 0.15
AGIRLIK_GORUNUM = 0.40
AGIRLIK_BOYUT = 0.15
ESLESTIRME_ESIK = 0.55
MESAFE_ESIK_PX = 250
HISTOGRAM_BIN = 16
HIZ_GECMIS_UZUNLUK = 15
MODEL_YOLU = "best.engine"
CIKIS_VIDEO = "sunum_videom.mp4"
VIDEOS_DIR = os.path.join(APP_DIR, "videos")
SQL_WRITE_RETRIES = 3
SQL_DRIVER_CANDIDATES = (
    "ODBC Driver 18 for SQL Server",
    "ODBC Driver 17 for SQL Server",
    "SQL Server",
)
SQL_MODERN_DRIVERS = {
    "ODBC Driver 18 for SQL Server",
    "ODBC Driver 17 for SQL Server",
}

IMGSZ = 640
OUTPUT_WIDTH = 1280
OUTPUT_HEIGHT = 720
OUTPUT_FPS = 25.0
RTSP_FFMPEG_OPTIONS = "rtsp_transport;tcp"
GRAY_FRAME_RECONNECT = 15
RTSP_FAIL_RECONNECT = 15
MAX_RTSP_RECONNECT_FAILURES = 8
VIDEO_PACE_MAX_COPIES = 10
DATA_PACE_MAX_SECONDS = 2.0


# ============================================================================
#  YARDIMCI FONKSİYONLAR
# ============================================================================

def paced_write_count(elapsed_sec, fps, max_copies=VIDEO_PACE_MAX_COPIES):
    """Duvar saatindeki boşluğu doldurmak için kaç kopya kare yazılacağını döndürür."""
    interval = 1.0 / max(float(fps), 1.0)
    copies = int(round(float(elapsed_sec) / interval))
    return min(int(max_copies), max(1, copies))


def elapsed_frame_weight(elapsed_sec, fps, max_seconds=DATA_PACE_MAX_SECONDS):
    """Atlanan canlı süreyi sanal kare sayısına çevirir; insan süreleri kısalmasın."""
    max_copies = max(1, int(round(float(max_seconds) * max(float(fps), 1.0))))
    return paced_write_count(elapsed_sec, fps, max_copies=max_copies)


def apply_rtsp_ffmpeg_options():
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = RTSP_FFMPEG_OPTIONS


def normalize_fps(raw_fps):
    try:
        raw_fps = float(raw_fps)
    except (TypeError, ValueError):
        return OUTPUT_FPS
    if raw_fps < 5 or raw_fps > 30:
        return OUTPUT_FPS
    return raw_fps


def open_rtsp_capture(url):
    apply_rtsp_ffmpeg_options()
    return cv2.VideoCapture(url, cv2.CAP_FFMPEG)


def sanitize_filename(name):
    """Windows dosya adında geçersiz karakterleri temizler."""
    text = str(name or "").strip()
    for ch in '<>:"/\\|?*':
        text = text.replace(ch, "")
    text = " ".join(text.split())
    return text.rstrip(" .") or "Kamera"


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def build_camera_video_path(camera_name, when=None):
    stamp = (when or datetime.now()).strftime("%Y%m%d_%H%M%S")
    filename = f"{sanitize_filename(camera_name)}_{stamp}.mp4"
    return os.path.join(ensure_dir(VIDEOS_DIR), filename)


def sql_config(config_data=None):
    data = config_data if config_data is not None else CONFIG
    return {
        "server": data.get("SQL_SERVER", DEFAULT_CONFIG["SQL_SERVER"]),
        "database": data.get("SQL_DATABASE", DEFAULT_CONFIG["SQL_DATABASE"]),
        "user": data.get("SQL_USER", DEFAULT_CONFIG["SQL_USER"]),
        "password": data.get("SQL_PASSWORD", DEFAULT_CONFIG["SQL_PASSWORD"]),
        "port": int(data.get("SQL_PORT", DEFAULT_CONFIG["SQL_PORT"])),
    }


def available_sql_driver():
    installed = set(pyodbc.drivers())
    for name in SQL_DRIVER_CANDIDATES:
        if name in installed:
            return name
    raise RuntimeError(
        "SQL Server ODBC sürücüsü bulunamadı. ODBC Driver 17 veya 18 kurun."
    )


def sql_connection(config_data=None):
    cfg = sql_config(config_data)
    driver = available_sql_driver()
    parts = [
        f"DRIVER={{{driver}}}",
        f"SERVER={cfg['server']},{cfg['port']}",
        f"DATABASE={cfg['database']}",
        f"UID={cfg['user']}",
        f"PWD={cfg['password']}",
        "Connection Timeout=8",
    ]
    if driver in SQL_MODERN_DRIVERS:
        parts.extend(["Encrypt=yes", "TrustServerCertificate=yes"])
    return pyodbc.connect(";".join(parts) + ";")


def ensure_sql_schema(cursor):
    cursor.execute("""
        IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'Bolge_Doluluk_Loglari')
        BEGIN
            CREATE TABLE Bolge_Doluluk_Loglari (
                Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
                LogTarihi DATETIME2 NOT NULL,
                KameraAdi NVARCHAR(200) NULL,
                KanalId NVARCHAR(50) NULL,
                BolgeAdi NVARCHAR(100) NULL,
                Dolu_Saniye FLOAT NULL,
                Bos_Saniye FLOAT NULL,
                Doluluk_Yuzdesi FLOAT NULL
            )
        END
    """)
    cursor.execute("""
        IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'Isci_Zaman_Loglari')
        BEGIN
            CREATE TABLE Isci_Zaman_Loglari (
                Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
                LogTarihi DATETIME2 NOT NULL,
                KameraAdi NVARCHAR(200) NULL,
                KanalId NVARCHAR(50) NULL,
                Isci_ID INT NULL,
                BolgeAdi NVARCHAR(100) NULL,
                Sure_Saniye FLOAT NULL
            )
        END
    """)
    cursor.execute("""
        IF NOT EXISTS (
            SELECT 1 FROM sys.indexes
            WHERE name = 'IX_Bolge_Doluluk_Loglari_LogTarihi_KanalId'
        )
        BEGIN
            CREATE INDEX IX_Bolge_Doluluk_Loglari_LogTarihi_KanalId
            ON Bolge_Doluluk_Loglari (LogTarihi, KanalId)
        END
    """)
    cursor.execute("""
        IF NOT EXISTS (
            SELECT 1 FROM sys.indexes
            WHERE name = 'IX_Isci_Zaman_Loglari_LogTarihi_KanalId'
        )
        BEGIN
            CREATE INDEX IX_Isci_Zaman_Loglari_LogTarihi_KanalId
            ON Isci_Zaman_Loglari (LogTarihi, KanalId)
        END
    """)
    cursor.execute("""
        IF COL_LENGTH('Bolge_Doluluk_Loglari', 'OturumId') IS NULL
            ALTER TABLE Bolge_Doluluk_Loglari ADD OturumId UNIQUEIDENTIFIER NULL
    """)
    cursor.execute("""
        IF COL_LENGTH('Bolge_Doluluk_Loglari', 'BolgeId') IS NULL
            ALTER TABLE Bolge_Doluluk_Loglari ADD BolgeId INT NULL
    """)
    cursor.execute("""
        IF COL_LENGTH('Isci_Zaman_Loglari', 'OturumId') IS NULL
            ALTER TABLE Isci_Zaman_Loglari ADD OturumId UNIQUEIDENTIFIER NULL
    """)
    cursor.execute("""
        IF COL_LENGTH('Isci_Zaman_Loglari', 'BolgeId') IS NULL
            ALTER TABLE Isci_Zaman_Loglari ADD BolgeId INT NULL
    """)
    cursor.execute("""
        IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'Oturum_Loglari')
        BEGIN
            CREATE TABLE Oturum_Loglari (
                OturumId UNIQUEIDENTIFIER NOT NULL PRIMARY KEY,
                Baslangic DATETIME2 NOT NULL,
                Bitis DATETIME2 NOT NULL,
                KameraAdi NVARCHAR(200) NULL,
                KanalId NVARCHAR(50) NULL,
                Fps FLOAT NULL,
                FrameSayisi INT NULL,
                ToplamZiyaret INT NULL,
                OnaylananZiyaret INT NULL,
                FiltrelenenZiyaret INT NULL,
                ToplamTakipId INT NULL,
                HayaletId INT NULL
            )
        END
    """)
    cursor.execute("""
        IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'Ziyaret_Loglari')
        BEGIN
            CREATE TABLE Ziyaret_Loglari (
                Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
                OturumId UNIQUEIDENTIFIER NOT NULL,
                KameraAdi NVARCHAR(200) NULL,
                KanalId NVARCHAR(50) NULL,
                Isci_ID INT NULL,
                BolgeId INT NULL,
                BolgeAdi NVARCHAR(100) NULL,
                Baslangic DATETIME2 NOT NULL,
                Bitis DATETIME2 NOT NULL,
                Sure_Saniye FLOAT NULL
            )
        END
    """)
    cursor.execute("""
        IF NOT EXISTS (
            SELECT 1 FROM sys.indexes
            WHERE name = 'IX_Oturum_Loglari_Bitis_KanalId'
        )
        BEGIN
            CREATE INDEX IX_Oturum_Loglari_Bitis_KanalId
            ON Oturum_Loglari (Bitis, KanalId)
        END
    """)
    cursor.execute("""
        IF NOT EXISTS (
            SELECT 1 FROM sys.indexes
            WHERE name = 'IX_Ziyaret_Loglari_OturumId'
        )
        BEGIN
            CREATE INDEX IX_Ziyaret_Loglari_OturumId
            ON Ziyaret_Loglari (OturumId)
        END
    """)
    cursor.execute("""
        IF NOT EXISTS (
            SELECT 1 FROM sys.indexes
            WHERE name = 'IX_Ziyaret_Loglari_Baslangic_KanalId'
        )
        BEGIN
            CREATE INDEX IX_Ziyaret_Loglari_Baslangic_KanalId
            ON Ziyaret_Loglari (Baslangic, KanalId)
        END
    """)
    cursor.execute("""
        IF NOT EXISTS (
            SELECT 1 FROM sys.indexes
            WHERE name = 'IX_Bolge_Doluluk_Loglari_OturumId'
        )
        AND COL_LENGTH('Bolge_Doluluk_Loglari', 'OturumId') IS NOT NULL
        BEGIN
            CREATE INDEX IX_Bolge_Doluluk_Loglari_OturumId
            ON Bolge_Doluluk_Loglari (OturumId)
        END
    """)
    cursor.execute("""
        IF NOT EXISTS (
            SELECT 1 FROM sys.indexes
            WHERE name = 'IX_Isci_Zaman_Loglari_OturumId'
        )
        AND COL_LENGTH('Isci_Zaman_Loglari', 'OturumId') IS NOT NULL
        BEGIN
            CREATE INDEX IX_Isci_Zaman_Loglari_OturumId
            ON Isci_Zaman_Loglari (OturumId)
        END
    """)


def frame_to_datetime(oturum_baslangic, frame_no, fps):
    fps = float(fps) or 25.0
    seconds = max(0.0, (max(1, int(frame_no)) - 1) / fps)
    return oturum_baslangic + timedelta(seconds=seconds)


def load_report_bundle(*_unused):
    from ui.report_data import fetch_report_bundle

    conn = sql_connection()
    try:
        cursor = conn.cursor()
        ensure_sql_schema(cursor)
        return fetch_report_bundle(cursor)
    finally:
        conn.close()


def persist_report_ui(state):
    if not isinstance(state, dict):
        return
    CONFIG["REPORT_UI"] = {
        "type": str(state.get("type") or "camera"),
        "item": str(state.get("item") or ""),
        "recording": str(state.get("recording") or ""),
        "period": str(state.get("period") or "all"),
    }
    try:
        save_config(CONFIG)
    except Exception:
        pass


def open_report_dialog(parent=None):
    global _report_dialog
    if _report_dialog is None:
        _report_dialog = ReportDialog(
            None,
            loader=load_report_bundle,
            people_loader=lambda: CONFIG,
            persist_fn=persist_report_ui,
        )
    _report_dialog.show()
    _report_dialog.raise_()
    _report_dialog.activateWindow()
    return _report_dialog


def reconnect_rtsp(url, cap, prefix=""):
    if cap is not None:
        try:
            cap.release()
        except Exception:
            pass
    time.sleep(0.3)
    new_cap = open_rtsp_capture(url)
    if new_cap.isOpened():
        for _ in range(10):
            if not new_cap.grab():
                break
        print(f"{prefix} RTSP yeniden bağlandı.")
        return new_cap
    print(f"{prefix} RTSP yeniden bağlanamadı.")
    return new_cap


def normalize_output_frame(frame, width=OUTPUT_WIDTH, height=OUTPUT_HEIGHT):
    if frame is None or getattr(frame, "size", 0) == 0:
        return None
    if frame.ndim == 2:
        frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    elif frame.shape[2] == 4:
        frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
    if frame.shape[1] != width or frame.shape[0] != height:
        frame = cv2.resize(frame, (width, height))
    if frame.dtype != np.uint8:
        frame = np.clip(frame, 0, 255).astype(np.uint8)
    if not frame.flags["C_CONTIGUOUS"]:
        frame = np.ascontiguousarray(frame)
    return frame


def frame_quality_stats(frame):
    if frame is None or getattr(frame, "size", 0) == 0:
        return 0.0, 0.0
    sample = frame[::8, ::8]
    if sample.size == 0:
        return 0.0, 0.0
    sample_f = sample.astype(np.float32)
    std = float(sample_f.std())
    means = sample_f.reshape(-1, sample_f.shape[-1]).mean(axis=0)
    chroma = float(np.max(means) - np.min(means))
    return std, chroma


def is_flat_gray_frame(frame, baseline_std=None):
    """Decoder kaybı: düşük varyanslı gri/soluk kare veya ani std düşüşü."""
    if frame is None or getattr(frame, "size", 0) == 0:
        return True
    std, chroma = frame_quality_stats(frame)
    if std <= 18.0 and chroma < 15.0:
        return True
    if baseline_std is not None and baseline_std > 22.0 and std < 0.40 * baseline_std:
        return True
    return False


def ciz_takip_katmani(frame, bolge_poligonlari, bolge_renkleri, overlay):
    """Zone ve son YOLO kutularını kopya kareye çizer (kayıt her yakalanan kareye uygulanır)."""
    vis = frame
    poly_overlay = vis.copy()
    for z, poli in bolge_poligonlari.items():
        cv2.polylines(vis, [poli], isClosed=True, color=bolge_renkleri[z], thickness=3)
        cv2.fillPoly(poly_overlay, [poli], color=bolge_renkleri[z])
    cv2.addWeighted(poly_overlay, 0.1, vis, 0.9, 0, vis)

    for kutu in overlay.get("kutular", []):
        x1, y1, x2, y2 = kutu["bbox"]
        renk = kutu["renk"]
        etiket = kutu["etiket"]
        kalinlik = kutu["kalinlik"]
        cv2.rectangle(vis, (x1, y1), (x2, y2), renk, kalinlik)
        etiket_boyutu = cv2.getTextSize(etiket, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)[0]
        cv2.rectangle(vis, (x1, y1 - etiket_boyutu[1] - 10),
                      (x1 + etiket_boyutu[0] + 4, y1), renk, -1)
        cv2.putText(vis, etiket, (x1 + 2, y1 - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        cv2.circle(vis, kutu["ayak"], 6, (0, 255, 255), -1)

    for kayip in overlay.get("kayip", []):
        mx, my = kayip["merkez"]
        tx, ty = kayip["tahmin"]
        cv2.circle(vis, (mx, my), 10, (128, 128, 128), 2)
        cv2.circle(vis, (tx, ty), 14, (0, 165, 255), 2)
        cv2.putText(vis, kayip["text"], (tx - 30, ty - 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 165, 255), 1)
        cv2.line(vis, (mx, my), (tx, ty), (128, 128, 128), 1)

    bilgi_satirlari = overlay.get("bilgi", [])
    if bilgi_satirlari:
        frame_genislik = vis.shape[1]
        panel_y = 10
        panel_genislik = 620
        panel_yukseklik = len(bilgi_satirlari) * 30 + 15
        panel_x = int((frame_genislik - panel_genislik) / 2)
        cv2.rectangle(vis, (panel_x, panel_y),
                      (panel_x + panel_genislik, panel_y + panel_yukseklik), (0, 0, 0), -1)
        cv2.rectangle(vis, (panel_x, panel_y),
                      (panel_x + panel_genislik, panel_y + panel_yukseklik), (0, 200, 0), 1)
        for idx, satir in enumerate(bilgi_satirlari):
            cv2.putText(vis, satir, (panel_x + 10, panel_y + 28 + idx * 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
    return vis


def bbox_alani(x1, y1, x2, y2):
    """Bounding box alanını hesaplar."""
    return max(0, x2 - x1) * max(0, y2 - y1)

# JIT‑accelerated helpers (fallback to pure Python if numba unavailable)
if _NUMBA_AVAILABLE:
    @njit(fastmath=True)
    def _oklidyen_mesafe_numba(ax, ay, bx, by):
        return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5

    @njit(fastmath=True)
    def _iou_numba(boxA, boxB):
        xA = max(boxA[0], boxB[0])
        yA = max(boxA[1], boxB[1])
        xB = min(boxA[2], boxB[2])
        yB = min(boxA[3], boxB[3])
        inter = max(0, xB - xA) * max(0, yB - yA)
        if inter == 0:
            return 0.0
        areaA = (boxA[2] - boxA[0]) * (boxA[3] - boxA[1])
        areaB = (boxB[2] - boxB[0]) * (boxB[3] - boxB[1])
        union = areaA + areaB - inter
        return inter / union if union > 0 else 0.0

    @njit(fastmath=True)
    def _histogram_benzerligi_numba(histA, histB):
        # Bhattacharyya distance via dot product of normalized histograms
        # Both histograms assumed pre‑normalized
        diff = 0.0
        for i in range(histA.shape[0]):
            diff += (histA[i] - histB[i]) ** 2
        return max(0.0, 1.0 - (diff ** 0.5) / 2.0)

    @njit(fastmath=True)
    def _boyut_benzerligi_numba(bboxA, bboxB):
        wA = bboxA[2] - bboxA[0]
        hA = bboxA[3] - bboxA[1]
        wB = bboxB[2] - bboxB[0]
        hB = bboxB[3] - bboxB[1]
        if wA <= 0 or hA <= 0 or wB <= 0 or hB <= 0:
            return 0.0
        w_oran = min(wA, wB) / max(wA, wB)
        h_oran = min(hA, hB) / max(hA, hB)
        return (w_oran + h_oran) / 2.0

    def oklidyen_mesafe(p1, p2):
        return _oklidyen_mesafe_numba(p1[0], p1[1], p2[0], p2[1])

    def iou_hesapla(boxA, boxB):
        return _iou_numba(np.array(boxA, dtype=np.float32), np.array(boxB, dtype=np.float32))

    def histogram_benzerligi(histA, histB):
        return _histogram_benzerligi_numba(histA.astype(np.float32), histB.astype(np.float32))

    def boyut_benzerligi(bboxA, bboxB):
        return _boyut_benzerligi_numba(np.array(bboxA, dtype=np.float32), np.array(bboxB, dtype=np.float32))
else:
    def oklidyen_mesafe(merkezA, merkezB):
        return np.sqrt((merkezA[0] - merkezB[0])**2 + (merkezA[1] - merkezB[1])**2)

    def iou_hesapla(boxA, boxB):
        xA = max(boxA[0], boxB[0])
        yA = max(boxA[1], boxB[1])
        xB = min(boxA[2], boxB[2])
        yB = min(boxA[3], boxB[3])
        kesisim = max(0, xB - xA) * max(0, yB - yA)
        if kesisim == 0:
            return 0.0
        alanA = bbox_alani(*boxA)
        alanB = bbox_alani(*boxB)
        birlesim = alanA + alanB - kesisim
        return kesisim / birlesim if birlesim > 0 else 0.0

    def histogram_benzerligi(histA, histB):
        if histA is None or histB is None:
            return 0.0
        if np.sum(histA) == 0 or np.sum(histB) == 0:
            return 0.0
        mesafe = cv2.compareHist(histA, histB, cv2.HISTCMP_BHATTACHARYYA)
        return max(0.0, 1.0 - mesafe)

    def boyut_benzerligi(bboxA, bboxB):
        wA = bboxA[2] - bboxA[0]
        hA = bboxA[3] - bboxA[1]
        wB = bboxB[2] - bboxB[0]
        hB = bboxB[3] - bboxB[1]
        if wA <= 0 or hA <= 0 or wB <= 0 or hB <= 0:
            return 0.0
        w_oran = min(wA, wB) / max(wA, wB)
        h_oran = min(hA, hB) / max(hA, hB)
        return (w_oran + h_oran) / 2.0


def oklidyen_mesafe(merkezA, merkezB):
    """İki merkez noktası arasındaki Öklidyen mesafe."""
    return np.sqrt((merkezA[0] - merkezB[0])**2 + (merkezA[1] - merkezB[1])**2)


def merkez_bul(x1, y1, x2, y2):
    """Bounding box merkez noktasını döndürür."""
    return (int((x1 + x2) / 2), int((y1 + y2) / 2))


def ayak_noktasi(x1, y1, x2, y2):
    """Bounding box alt-orta (ayak) noktasını döndürür."""
    return (int((x1 + x2) / 2), int(y2))


def _normalize_feature(feature):
    """Görünüm vektörünü güvenli biçimde L2 normalize eder."""
    if feature is None:
        return None
    feature = np.asarray(feature, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(feature))
    if norm <= 1e-8:
        return np.zeros_like(feature, dtype=np.float32)
    return feature / norm


def renk_histogrami_cikar(frame, x1, y1, x2, y2, bins=16):
    """
    Kişinin görünüm imzasını çıkarır.

    Tek bir büyük histogram yerine gövdeyi üst ve alt parça olarak işler.
    Böylece pantolon/üst kıyafet ayrımı korunur ve arka planın ID kararına
    etkisi azalır. Ağır bir Re-ID modeli gerektirmez.
    """
    fh, fw = frame.shape[:2]
    x1 = max(0, min(fw - 1, int(x1)))
    y1 = max(0, min(fh - 1, int(y1)))
    x2 = max(0, min(fw, int(x2)))
    y2 = max(0, min(fh, int(y2)))

    w = x2 - x1
    h = y2 - y1
    feature_len = bins * 3 * 2
    if w < 4 or h < 8:
        return np.zeros(feature_len, dtype=np.float32)

    # Kenarlardaki arka planı azalt; baş ve ayak bölgelerini kısmen dışarıda bırak.
    rx1 = x1 + int(w * 0.10)
    rx2 = x2 - int(w * 0.10)
    ry1 = y1 + int(h * 0.08)
    ry2 = y1 + int(h * 0.94)
    if rx2 <= rx1 or ry2 <= ry1:
        return np.zeros(feature_len, dtype=np.float32)

    split_y = ry1 + int((ry2 - ry1) * 0.52)
    parcalar = [frame[ry1:split_y, rx1:rx2], frame[split_y:ry2, rx1:rx2]]
    ozellikler = []

    for roi in parcalar:
        if roi.size == 0:
            ozellikler.extend([np.zeros(bins, dtype=np.float32)] * 3)
            continue

        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        hist_h = cv2.calcHist([hsv], [0], None, [bins], [0, 180]).flatten()
        hist_s = cv2.calcHist([hsv], [1], None, [bins], [0, 256]).flatten()
        hist_v = cv2.calcHist([hsv], [2], None, [bins], [0, 256]).flatten()

        for hist in (hist_h, hist_s, hist_v):
            toplam = float(np.sum(hist))
            if toplam > 0:
                hist = hist / toplam
            ozellikler.append(hist.astype(np.float32))

    return _normalize_feature(np.concatenate(ozellikler))


def histogram_benzerligi(histA, histB):
    """İki normalize görünüm vektörünün kosinüs benzerliğini döndürür."""
    if histA is None or histB is None:
        return 0.0
    a = np.asarray(histA, dtype=np.float32).reshape(-1)
    b = np.asarray(histB, dtype=np.float32).reshape(-1)
    if a.size == 0 or b.size == 0 or a.shape != b.shape:
        return 0.0
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na <= 1e-8 or nb <= 1e-8:
        return 0.0
    return float(np.clip(np.dot(a, b) / (na * nb), 0.0, 1.0))


def boyut_benzerligi(bboxA, bboxB):
    """Genişlik, yükseklik ve en-boy oranından kararlı boyut benzerliği."""
    w_a = max(1.0, float(bboxA[2] - bboxA[0]))
    h_a = max(1.0, float(bboxA[3] - bboxA[1]))
    w_b = max(1.0, float(bboxB[2] - bboxB[0]))
    h_b = max(1.0, float(bboxB[3] - bboxB[1]))

    w_oran = min(w_a, w_b) / max(w_a, w_b)
    h_oran = min(h_a, h_b) / max(h_a, h_b)
    alan_benzerligi = float(np.sqrt(w_oran * h_oran))

    ar_a = w_a / h_a
    ar_b = w_b / h_b
    oran_benzerligi = min(ar_a, ar_b) / max(ar_a, ar_b)
    return float(np.clip(0.78 * alan_benzerligi + 0.22 * oran_benzerligi, 0.0, 1.0))


def _bbox_kaydir(bbox, dx, dy):
    return (
        float(bbox[0]) + dx,
        float(bbox[1]) + dy,
        float(bbox[2]) + dx,
        float(bbox[3]) + dy,
    )


# ============================================================================
#  TUTARLI ID TRACKER SINIFI
# ============================================================================

class GelismisIsciTracker:
    """
    Hafif fakat kararlı çoklu kişi takip algoritması.

    Temel yaklaşım:
      1. Sabit piksel eşiği yerine kişi boyutuna ve hızına göre uyarlanan kapı.
      2. Son konumdan EMA/medyan hız ile bir sonraki konum tahmini.
      3. Kısa ve uzun süreli iki ayrı görünüm hafızası.
      4. Yüksek güvenli tespitlerle ana eşleştirme.
      5. Düşük güvenli tespitlerle yalnızca mevcut ID'leri köprüleme.
      6. Kayıp izleri, kayıp süresine göre yakın-orta-uzun kademelerde geri alma.
      7. Greedy yerine mümkünse Hungarian global atama.

    Dış arayüz önceki sınıfla uyumludur: guncelle() aynı tuple listesini döndürür
    ve aktif_izler/_tahmini_pozisyon alanları mevcut çizim koduyla çalışır.
    """

    def __init__(self, max_kayip_suresi_sn, fps, yuksek_guven_esigi=0.5):
        self.sonraki_id = 1
        self.aktif_izler = {}
        self.fps = max(1.0, float(fps) if fps and fps > 0 else 25.0)
        self.max_kayip_kare = max(1, int(max_kayip_suresi_sn * self.fps))

        self.yuksek_guven_esigi = float(np.clip(yuksek_guven_esigi, 0.05, 1.0))
        self.dusuk_guven_esigi = float(
            np.clip(self.yuksek_guven_esigi * 0.50, 0.08, self.yuksek_guven_esigi)
        )

        self.frame_genislik = 1280
        self.frame_yukseklik = 720
        self.frame_diyagonal = float(np.hypot(self.frame_genislik, self.frame_yukseklik))

        # Görünüm hafızası güncelleme katsayıları: küçük değer = daha az sürüklenme.
        self.kisa_hist_yeni_oran = 0.18
        self.uzun_hist_yeni_oran = 0.035

    # ------------------------------------------------------------------
    # İz tahmini ve temel ölçüler
    # ------------------------------------------------------------------
    def _hiz_tahmin_et(self, iz):
        hiz = np.asarray(iz.get("hiz", (0.0, 0.0)), dtype=np.float32)
        return (float(hiz[0]), float(hiz[1]))

    def _etkin_tahmin_adimi(self, iz):
        """Uzun kayıplarda sabit hızın sonsuza doğru kaçmasını yumuşatır."""
        adim = max(1.0, float(iz.get("kayip_kare", 0) + 1))
        bir_saniye = self.fps
        if adim <= bir_saniye:
            return adim
        return bir_saniye + (adim - bir_saniye) * 0.22

    def _tahmini_pozisyon(self, iz):
        mx, my = iz["merkez"]
        vx, vy = self._hiz_tahmin_et(iz)
        adim = self._etkin_tahmin_adimi(iz)
        tx = int(round(mx + vx * adim))
        ty = int(round(my + vy * adim))
        return (tx, ty)

    def _tahmini_bbox(self, iz):
        tx, ty = self._tahmini_pozisyon(iz)
        mx, my = iz["merkez"]
        return _bbox_kaydir(iz["bbox"], tx - mx, ty - my)

    def _mesafe_kapisi(self, iz, det, mod):
        son_h = max(1.0, float(iz["bbox"][3] - iz["bbox"][1]))
        det_h = max(1.0, float(det["bbox"][3] - det["bbox"][1]))
        ref_h = max(36.0, (son_h + det_h) * 0.5)
        hiz = float(np.linalg.norm(np.asarray(iz.get("hiz", (0.0, 0.0)))))

        if mod == "aktif":
            kapi = max(44.0, ref_h * 0.72) + hiz * 1.9
        elif mod == "dusuk":
            kapi = max(38.0, ref_h * 0.58) + hiz * 1.5
        else:
            kayip_sn = float(iz.get("kayip_kare", 0)) / self.fps
            etkin_adim = min(self._etkin_tahmin_adimi(iz), self.fps * 1.7)
            kapi = (
                max(58.0, ref_h * 0.86)
                + hiz * etkin_adim * 1.15
                + ref_h * min(kayip_sn, 4.0) * 0.22
            )

        return float(min(kapi, max(140.0, self.frame_diyagonal * 0.46)))

    def _gorunum_skoru(self, iz, det_hist):
        kisa = histogram_benzerligi(iz.get("histogram"), det_hist)
        uzun = histogram_benzerligi(iz.get("histogram_uzun"), det_hist)
        if iz.get("toplam_gorunen_kare", 0) < 4:
            return max(kisa, uzun)
        return float(np.clip(0.38 * kisa + 0.62 * uzun, 0.0, 1.0))

    def _yon_skoru(self, iz, det_merkez):
        hiz = np.asarray(iz.get("hiz", (0.0, 0.0)), dtype=np.float32)
        hareket = np.asarray(det_merkez, dtype=np.float32) - np.asarray(
            iz["merkez"], dtype=np.float32
        )
        hiz_norm = float(np.linalg.norm(hiz))
        hareket_norm = float(np.linalg.norm(hareket))
        if hiz_norm < 1.5 or hareket_norm < 2.0:
            return 0.5
        kosinus = float(np.dot(hiz, hareket) / (hiz_norm * hareket_norm + 1e-8))
        return float(np.clip((kosinus + 1.0) * 0.5, 0.0, 1.0))

    # ------------------------------------------------------------------
    # Tespit hazırlama ve aday skoru
    # ------------------------------------------------------------------
    def _tespitleri_hazirla(self, tespitler, frame):
        self.frame_yukseklik, self.frame_genislik = frame.shape[:2]
        self.frame_diyagonal = float(np.hypot(self.frame_genislik, self.frame_yukseklik))

        hazir = []
        for det in tespitler:
            if len(det) < 4:
                continue
            x1, y1, x2, y2 = [int(v) for v in det[:4]]
            x1 = max(0, min(self.frame_genislik - 1, x1))
            y1 = max(0, min(self.frame_yukseklik - 1, y1))
            x2 = max(0, min(self.frame_genislik, x2))
            y2 = max(0, min(self.frame_yukseklik, y2))
            if x2 <= x1 or y2 <= y1:
                continue
            if bbox_alani(x1, y1, x2, y2) < MIN_BBOX_ALAN:
                continue

            conf = float(det[4]) if len(det) > 4 else 1.0
            bbox = (x1, y1, x2, y2)
            hazir.append({
                "bbox": bbox,
                "merkez": merkez_bul(*bbox),
                "ayak": ayak_noktasi(*bbox),
                "histogram": renk_histogrami_cikar(frame, *bbox, bins=HISTOGRAM_BIN),
                "conf": conf,
                "okluzyonlu": False,
            })

        # Model farklı sınıflardan neredeyse aynı kutuyu iki kez üretirse ikinci
        # kutunun gereksiz yeni ID açmasını engelle. Eşik bilinçli olarak çok
        # yüksektir; gerçek iki kişiyi bastırmak yerine yalnızca kopyayı hedefler.
        tekil = []
        for det in sorted(hazir, key=lambda d: d["conf"], reverse=True):
            if any(iou_hesapla(det["bbox"], secili["bbox"]) >= 0.88 for secili in tekil):
                continue
            tekil.append(det)
        hazir = tekil

        # Kişiler üst üste geldiğinde görünüm modelini güncellemeyerek renk karışmasını önle.
        for i in range(len(hazir)):
            for j in range(i + 1, len(hazir)):
                if iou_hesapla(hazir[i]["bbox"], hazir[j]["bbox"]) >= 0.16:
                    hazir[i]["okluzyonlu"] = True
                    hazir[j]["okluzyonlu"] = True

        return hazir

    def _esik(self, iz, mod):
        if mod == "aktif":
            # Yeni izlerin görünüm hafızası henüz oturmadığından biraz daha seçici ol.
            return 0.555 if iz.get("toplam_gorunen_kare", 0) < 3 else 0.495
        if mod == "dusuk":
            return 0.43

        kayip_sn = float(iz.get("kayip_kare", 0)) / self.fps
        esik = 0.54 + min(0.18, kayip_sn * 0.038)
        if iz.get("toplam_gorunen_kare", 0) < 3:
            esik += 0.035
        return float(min(esik, 0.75))

    def _aday_skoru(self, iz, det, mod):
        tahmini_merkez = self._tahmini_pozisyon(iz)
        tahmini_bbox = self._tahmini_bbox(iz)
        dx = tahmini_merkez[0] - iz["merkez"][0]
        dy = tahmini_merkez[1] - iz["merkez"][1]
        tahmini_ayak = (iz["ayak"][0] + dx, iz["ayak"][1] + dy)

        merkez_mesafe = oklidyen_mesafe(tahmini_merkez, det["merkez"])
        ayak_mesafe = oklidyen_mesafe(tahmini_ayak, det["ayak"])
        kapi = self._mesafe_kapisi(iz, det, mod)

        # Hem merkez hem ayak kapının dışındaysa bu eşleşme fiziksel olarak olası değil.
        if merkez_mesafe > kapi * 1.12 and ayak_mesafe > kapi:
            return None

        merkez_skor = float(np.exp(-1.85 * (merkez_mesafe / max(kapi, 1.0)) ** 2))
        ayak_skor = float(np.exp(-1.85 * (ayak_mesafe / max(kapi, 1.0)) ** 2))
        hareket_skoru = 0.35 * merkez_skor + 0.65 * ayak_skor

        iou_skor = iou_hesapla(tahmini_bbox, det["bbox"])
        gorunum_skor = self._gorunum_skoru(iz, det["histogram"])
        boyut_skor = boyut_benzerligi(iz["bbox"], det["bbox"])
        yon_skor = self._yon_skoru(iz, det["merkez"])
        conf_skor = float(np.clip(det["conf"], 0.0, 1.0))

        if boyut_skor < 0.26:
            return None

        if mod == "aktif":
            if iou_skor < 0.01 and hareket_skoru < 0.28 and gorunum_skor < 0.58:
                return None
            skor = (
                0.30 * hareket_skoru
                + 0.22 * iou_skor
                + 0.25 * gorunum_skor
                + 0.13 * boyut_skor
                + 0.07 * yon_skor
                + 0.03 * conf_skor
            )

        elif mod == "dusuk":
            # Düşük güvenli kutular görünüm hafızasını kirletmesin; sadece kısa boşluğu kapatsın.
            if iou_skor < 0.01 and hareket_skoru < 0.42:
                return None
            skor = (
                0.48 * hareket_skoru
                + 0.30 * iou_skor
                + 0.16 * boyut_skor
                + 0.06 * yon_skor
            )

        else:
            kayip_sn = float(iz.get("kayip_kare", 0)) / self.fps
            if kayip_sn > 1.5 and gorunum_skor < 0.46:
                return None
            if kayip_sn > 4.0 and gorunum_skor < 0.58:
                return None
            if hareket_skoru < 0.14 and gorunum_skor < 0.76:
                return None

            if kayip_sn <= 1.0:
                agirliklar = (0.32, 0.10, 0.38, 0.10, 0.06, 0.04)
            elif kayip_sn <= 4.0:
                agirliklar = (0.24, 0.06, 0.50, 0.10, 0.07, 0.03)
            else:
                agirliklar = (0.18, 0.03, 0.58, 0.10, 0.08, 0.03)

            skor = (
                agirliklar[0] * hareket_skoru
                + agirliklar[1] * iou_skor
                + agirliklar[2] * gorunum_skor
                + agirliklar[3] * boyut_skor
                + agirliklar[4] * yon_skor
                + agirliklar[5] * conf_skor
            )

        return float(np.clip(skor, 0.0, 1.0))

    # ------------------------------------------------------------------
    # Global eşleştirme
    # ------------------------------------------------------------------
    def _eslestir(self, iz_ids, det_indices, detler, mod):
        if not iz_ids or not det_indices:
            return []

        n_iz = len(iz_ids)
        n_det = len(det_indices)
        skorlar = np.full((n_iz, n_det), -1.0, dtype=np.float64)
        esikler = np.zeros(n_iz, dtype=np.float64)

        for i, iz_id in enumerate(iz_ids):
            iz = self.aktif_izler[iz_id]
            esik = self._esik(iz, mod)
            esikler[i] = esik
            for j, det_idx in enumerate(det_indices):
                skor = self._aday_skoru(iz, detler[det_idx], mod)
                if skor is not None and skor >= esik:
                    # Çok küçük olgunluk bonusu yalnızca tam eşitliklerde eski ID'yi korur.
                    olgunluk = min(iz.get("toplam_gorunen_kare", 0), 500) * 1e-7
                    skorlar[i, j] = skor + olgunluk

        if not np.any(skorlar >= 0.0):
            return []

        if _HUNGARIAN_AVAILABLE:
            # Gerçek eşleşmeler + her iz/tespit için ayrı "eşleşmeden kal" seçeneği.
            boyut = n_iz + n_det
            fayda = np.full((boyut, boyut), -1e6, dtype=np.float64)
            fayda[:n_iz, :n_det] = skorlar

            for i in range(n_iz):
                fayda[i, n_det + i] = esikler[i]
            for j in range(n_det):
                fayda[n_iz + j, j] = 0.0
            fayda[n_iz:, n_det:] = 0.0

            satirlar, sutunlar = linear_sum_assignment(-fayda)
            eslesmeler = []
            for r, c in zip(satirlar, sutunlar):
                if r < n_iz and c < n_det and skorlar[r, c] >= esikler[r]:
                    eslesmeler.append((iz_ids[r], det_indices[c], float(skorlar[r, c])))
            return eslesmeler

        # SciPy yoksa global skora göre deterministik bire-bir fallback.
        adaylar = []
        for i in range(n_iz):
            for j in range(n_det):
                if skorlar[i, j] >= esikler[i]:
                    adaylar.append((float(skorlar[i, j]), iz_ids[i], det_indices[j]))
        adaylar.sort(key=lambda x: (-x[0], x[1], x[2]))

        kullanilan_iz = set()
        kullanilan_det = set()
        eslesmeler = []
        for skor, iz_id, det_idx in adaylar:
            if iz_id in kullanilan_iz or det_idx in kullanilan_det:
                continue
            kullanilan_iz.add(iz_id)
            kullanilan_det.add(det_idx)
            eslesmeler.append((iz_id, det_idx, skor))
        return eslesmeler

    # ------------------------------------------------------------------
    # İz güncelleme/oluşturma
    # ------------------------------------------------------------------
    def _ozellik_karistir(self, eski, yeni, yeni_oran):
        if yeni is None or float(np.linalg.norm(yeni)) <= 1e-8:
            return eski
        if eski is None or float(np.linalg.norm(eski)) <= 1e-8:
            return yeni.copy()
        return _normalize_feature((1.0 - yeni_oran) * eski + yeni_oran * yeni)

    def _iz_guncelle(self, iz_id, det, eslesme_skoru, gorunum_guncelle=True):
        iz = self.aktif_izler[iz_id]
        eski_merkez = np.asarray(iz["merkez"], dtype=np.float32)
        yeni_merkez = np.asarray(det["merkez"], dtype=np.float32)
        onceki_kayip = int(iz.get("kayip_kare", 0))
        gecen_kare = max(1, onceki_kayip + 1)

        olculen_hiz = (yeni_merkez - eski_merkez) / float(gecen_kare)
        bbox_h = max(1.0, float(det["bbox"][3] - det["bbox"][1]))
        max_hiz = max(18.0, bbox_h * 0.38)
        hiz_norm = float(np.linalg.norm(olculen_hiz))
        if hiz_norm > max_hiz:
            olculen_hiz *= max_hiz / (hiz_norm + 1e-8)

        iz["hiz_gecmisi"].append((float(olculen_hiz[0]), float(olculen_hiz[1])))
        hiz_dizisi = np.asarray(iz["hiz_gecmisi"], dtype=np.float32)
        medyan_hiz = np.median(hiz_dizisi[-7:], axis=0)
        eski_hiz = np.asarray(iz.get("hiz", (0.0, 0.0)), dtype=np.float32)

        if iz.get("toplam_gorunen_kare", 0) < 3:
            eski_oran = 0.25
        elif onceki_kayip > 0:
            eski_oran = 0.38
        else:
            eski_oran = 0.62
        iz["hiz"] = eski_oran * eski_hiz + (1.0 - eski_oran) * medyan_hiz

        if (
            gorunum_guncelle
            and not det.get("okluzyonlu", False)
            and det["conf"] >= self.yuksek_guven_esigi
        ):
            kisa_oran = 0.08 if onceki_kayip > 0 else self.kisa_hist_yeni_oran
            iz["histogram"] = self._ozellik_karistir(
                iz.get("histogram"), det["histogram"], kisa_oran
            )

            # Uzun hafızayı sadece çok güvenilir, kesintisiz eşleşmelerde güncelle.
            if onceki_kayip == 0 and eslesme_skoru >= 0.64:
                iz["histogram_uzun"] = self._ozellik_karistir(
                    iz.get("histogram_uzun"),
                    det["histogram"],
                    self.uzun_hist_yeni_oran,
                )

        iz["bbox"] = det["bbox"]
        iz["merkez"] = det["merkez"]
        iz["ayak"] = det["ayak"]
        iz["kayip_kare"] = 0
        iz["gorunuyor"] = True
        iz["toplam_gorunen_kare"] = iz.get("toplam_gorunen_kare", 0) + 1
        iz["ardisik_eslesme"] = iz.get("ardisik_eslesme", 0) + 1
        iz["son_guven"] = det["conf"]
        iz["son_eslesme_skoru"] = eslesme_skoru

    def _yeni_iz_olustur(self, det):
        yeni_id = self.sonraki_id
        self.sonraki_id += 1
        hist = det["histogram"].copy()
        self.aktif_izler[yeni_id] = {
            "bbox": det["bbox"],
            "merkez": det["merkez"],
            "ayak": det["ayak"],
            "histogram": hist.copy(),
            "histogram_uzun": hist.copy(),
            "hiz": np.zeros(2, dtype=np.float32),
            "hiz_gecmisi": deque(maxlen=HIZ_GECMIS_UZUNLUK),
            "kayip_kare": 0,
            "gorunuyor": True,
            "toplam_gorunen_kare": 1,
            "ardisik_eslesme": 1,
            "yas": 1,
            "son_guven": det["conf"],
            "son_eslesme_skoru": 1.0,
        }
        return yeni_id

    # ------------------------------------------------------------------
    # Ana takip döngüsü
    # ------------------------------------------------------------------
    def guncelle(self, tespitler, frame):
        """
        Her kare için çağrılır.

        Döndürür:
            [(id, x1, y1, x2, y2, confidence, durum), ...]
        """
        detler = self._tespitleri_hazirla(tespitler, frame)
        for iz in self.aktif_izler.values():
            iz["yas"] = iz.get("yas", 0) + 1

        yuksek_det = [
            i for i, d in enumerate(detler)
            if d["conf"] >= self.yuksek_guven_esigi
        ]
        dusuk_det = [
            i for i, d in enumerate(detler)
            if self.dusuk_guven_esigi <= d["conf"] < self.yuksek_guven_esigi
        ]

        gorunen_izler = [iz_id for iz_id, iz in self.aktif_izler.items() if iz["gorunuyor"]]
        kayip_izler = [iz_id for iz_id, iz in self.aktif_izler.items() if not iz["gorunuyor"]]

        sonuclar = []
        eslesen_izler = set()
        eslesen_detler = set()

        # 1) Görünen izleri yüksek güvenli tespitlerle global olarak eşleştir.
        for iz_id, det_idx, skor in self._eslestir(gorunen_izler, yuksek_det, detler, "aktif"):
            det = detler[det_idx]
            self._iz_guncelle(iz_id, det, skor, gorunum_guncelle=True)
            x1, y1, x2, y2 = det["bbox"]
            sonuclar.append((iz_id, x1, y1, x2, y2, det["conf"], "aktif"))
            eslesen_izler.add(iz_id)
            eslesen_detler.add(det_idx)

        # 2) Yüksek güven eşleşmesini kaçıran aktif izleri düşük güvenli kutularla köprüle.
        kalan_gorunen = [iz_id for iz_id in gorunen_izler if iz_id not in eslesen_izler]
        kalan_dusuk = [idx for idx in dusuk_det if idx not in eslesen_detler]
        for iz_id, det_idx, skor in self._eslestir(kalan_gorunen, kalan_dusuk, detler, "dusuk"):
            det = detler[det_idx]
            self._iz_guncelle(iz_id, det, skor, gorunum_guncelle=False)
            x1, y1, x2, y2 = det["bbox"]
            sonuclar.append((iz_id, x1, y1, x2, y2, det["conf"], "aktif"))
            eslesen_izler.add(iz_id)
            eslesen_detler.add(det_idx)

        # 3) Kayıp izleri önce yakın geçmişten başlayarak geri çağır.
        kalan_yuksek = [idx for idx in yuksek_det if idx not in eslesen_detler]
        bir_sn = int(self.fps)
        kayip_kademeleri = [
            [i for i in kayip_izler if self.aktif_izler[i]["kayip_kare"] <= bir_sn],
            [
                i for i in kayip_izler
                if bir_sn < self.aktif_izler[i]["kayip_kare"] <= bir_sn * 3
            ],
            [i for i in kayip_izler if self.aktif_izler[i]["kayip_kare"] > bir_sn * 3],
        ]

        for kademe in kayip_kademeleri:
            if not kalan_yuksek:
                break
            for iz_id, det_idx, skor in self._eslestir(kademe, kalan_yuksek, detler, "kayip"):
                det = detler[det_idx]
                self._iz_guncelle(iz_id, det, skor, gorunum_guncelle=True)
                x1, y1, x2, y2 = det["bbox"]
                sonuclar.append((iz_id, x1, y1, x2, y2, det["conf"], "geri_dondu"))
                eslesen_izler.add(iz_id)
                eslesen_detler.add(det_idx)
            kalan_yuksek = [idx for idx in kalan_yuksek if idx not in eslesen_detler]

        # 4) Eşleşmeyen yüksek güvenli tespitler yeni ID olabilir.
        for det_idx in yuksek_det:
            if det_idx in eslesen_detler:
                continue
            det = detler[det_idx]
            yeni_id = self._yeni_iz_olustur(det)
            x1, y1, x2, y2 = det["bbox"]
            sonuclar.append((yeni_id, x1, y1, x2, y2, det["conf"], "yeni"))
            eslesen_izler.add(yeni_id)
            eslesen_detler.add(det_idx)

        # 5) Bu karede eşleşmeyen eski izleri yalnızca bir kez yaşlandır.
        mevcut_eski_izler = gorunen_izler + kayip_izler
        for iz_id in mevcut_eski_izler:
            if iz_id in eslesen_izler or iz_id not in self.aktif_izler:
                continue
            iz = self.aktif_izler[iz_id]
            iz["kayip_kare"] = iz.get("kayip_kare", 0) + 1
            iz["gorunuyor"] = False
            iz["ardisik_eslesme"] = 0
            iz["hiz"] = np.asarray(iz.get("hiz", (0.0, 0.0)), dtype=np.float32) * 0.992

        # 6) Hafıza süresi dolan izleri temizle.
        silinecekler = [
            iz_id for iz_id, iz in self.aktif_izler.items()
            if iz.get("kayip_kare", 0) > self.max_kayip_kare
        ]
        for iz_id in silinecekler:
            del self.aktif_izler[iz_id]

        return sonuclar


# ============================================================================
#  ZİYARET TAKİP SINIFI (Visit Tracker)
# ============================================================================

class ZiyaretTakip:
    """
    Her (işçi_id, bölge_no) çifti için bireysel ziyaretleri takip eder.
    
    Bir "ziyaret", bir işçinin bir bölgeye girip çıkmasıdır.
    Köprüleme (bridge) mantığı burada da uygulanır:
      - İşçi bölgeden çıkıp KOPRU süresi içinde geri dönerse,
        aynı ziyaretin parçası sayılır.
      - Köprü süresi aşılırsa, ziyaret biter.
    
    Filtreleme (rapor zamanı):
      - MIN_ZIYARET_KARE'den kısa ziyaretler gürültü sayılır ve yok sayılır.
      - Bu sayede bbox titremesi yüzünden 1 saniyelik sahte bölge kesişimleri
        otomatik olarak temizlenir.
    
    Ziyaret verisi yapısı:
      - onaylanan_kare: Kesinleşmiş kare sayısı (tespit + başarılı köprü)
      - bekleyen_kopru: Henüz köprülenip köprülenmediği belli olmayan kare sayısı
        → İşçi geri dönerse → onaylanan_kare'ye eklenir
        → Köprü süresi aşılırsa → iptal edilir (ziyaret bunlarsız biter)
    """

    def __init__(self, kopru_kare, min_ziyaret_kare):
        self.kopru_kare = kopru_kare
        self.min_ziyaret_kare = min_ziyaret_kare

        # Aktif (devam eden) ziyaretler: {(isci_id, bolge_no): ziyaret_bilgi}
        self.aktif_ziyaretler = {}

        # Tamamlanmış ziyaretler listesi
        self.tamamlanan_ziyaretler = []

    def guncelle(self, isci_id, bolge_no, bu_karede_icinde, frame_no, agirlik=1):
        """
        Her işlenen karede, her (işçi, bölge) çifti için çağrılır.
        agirlik: atlanan canlı sürenin sanal kare karşılığı (en az 1).
        """
        agirlik = max(1, int(agirlik))
        key = (isci_id, bolge_no)

        if key not in self.aktif_ziyaretler:
            if bu_karede_icinde:
                self.aktif_ziyaretler[key] = {
                    "baslangic_frame": max(1, int(frame_no) - agirlik + 1),
                    "son_tespit_frame": frame_no,
                    "onaylanan_kare": agirlik,
                    "bekleyen_kopru": 0,
                }
            return

        ziyaret = self.aktif_ziyaretler[key]

        if bu_karede_icinde:
            ziyaret["onaylanan_kare"] += agirlik + ziyaret["bekleyen_kopru"]
            ziyaret["bekleyen_kopru"] = 0
            ziyaret["son_tespit_frame"] = frame_no
        else:
            ziyaret["bekleyen_kopru"] += agirlik

            if ziyaret["bekleyen_kopru"] > self.kopru_kare:
                self._ziyaret_bitir(key)

    def _ziyaret_bitir(self, key):
        """Aktif bir ziyareti tamamlanmış listeye taşır."""
        ziyaret = self.aktif_ziyaretler.pop(key)
        isci_id, bolge_no = key

        self.tamamlanan_ziyaretler.append({
            "isci_id": isci_id,
            "bolge_no": bolge_no,
            "baslangic_frame": ziyaret["baslangic_frame"],
            "bitis_frame": ziyaret["son_tespit_frame"],
            "kare_sayisi": ziyaret["onaylanan_kare"],
        })

    def video_bitti(self):
        """Video sonu — aktif ziyaretleri finalize et."""
        for key in list(self.aktif_ziyaretler.keys()):
            self._ziyaret_bitir(key)

    def filtreli_ziyaretler(self):
        """MIN_ZIYARET_KARE'yi geçen ham ziyaret listesi (kare aralıkları korunur)."""
        return [
            dict(z)
            for z in self.tamamlanan_ziyaretler
            if z["kare_sayisi"] >= self.min_ziyaret_kare
        ]

    def filtreli_isci_bolge_sureleri(self):
        """
        Sadece MIN_ZIYARET_KARE'yi aşan ziyaretleri döndürür.
        
        Döndürür:
            {isci_id: {bolge_no: toplam_onaylanan_kare}}
        """
        sonuc = {}
        for z in self.filtreli_ziyaretler():
            iid = z["isci_id"]
            bn = z["bolge_no"]
            if iid not in sonuc:
                sonuc[iid] = {}
            sonuc[iid][bn] = sonuc[iid].get(bn, 0) + z["kare_sayisi"]
        return sonuc

    def filtreli_zone_doluluk(self, bolge_listesi, toplam_frame):
        """
        Sadece onaylanmış ziyaretlerden zone doluluk sayısını hesaplar.
        Çakışan ziyaret aralıklarını birleştirerek doğru kare sayısını verir.
        
        Döndürür:
            {bolge_no: dolu_kare_sayisi}
        """
        zone_dolu = {z: 0 for z in bolge_listesi}

        # Her bölge için onaylanmış ziyaret aralıklarını topla
        zone_araliklar = {z: [] for z in bolge_listesi}
        for z in self.tamamlanan_ziyaretler:
            if z["kare_sayisi"] >= self.min_ziyaret_kare:
                bn = z["bolge_no"]
                if bn in zone_araliklar:
                    zone_araliklar[bn].append(
                        (z["baslangic_frame"], z["bitis_frame"])
                    )

        # Her bölge için çakışan aralıkları birleştir ve kare say
        for bn in bolge_listesi:
            araliklar = sorted(zone_araliklar[bn])
            if not araliklar:
                continue

            # Merge overlapping/adjacent ranges
            birlesik = [list(araliklar[0])]
            for baslangic, bitis in araliklar[1:]:
                if baslangic <= birlesik[-1][1] + 1:
                    birlesik[-1][1] = max(birlesik[-1][1], bitis)
                else:
                    birlesik.append([baslangic, bitis])

            # Toplam kare sayısı
            for baslangic, bitis in birlesik:
                zone_dolu[bn] += (bitis - baslangic + 1)

        return zone_dolu

    def ziyaret_istatistikleri(self):
        """Debug/bilgi: toplam ve filtrelenmiş ziyaret sayılarını döndürür."""
        toplam = len(self.tamamlanan_ziyaretler)
        onaylanan = sum(
            1 for z in self.tamamlanan_ziyaretler
            if z["kare_sayisi"] >= self.min_ziyaret_kare
        )
        filtrelenen = toplam - onaylanan
        return toplam, onaylanan, filtrelenen


# ============================================================================
#  BÖLGE DOLULUK DEBOUNCER (Gerçek zamanlı görüntüleme için)
# ============================================================================

class ZoneDolulukDebouncer:
    """
    Bölge doluluk sayacı için gerçek zamanlı debouncing.
    Kısa boşlukları köprüler — video overlay'de kullanılır.
    (Final veritabanı raporu ZiyaretTakip'ten hesaplanır.)
    """

    def __init__(self, kopru_kare):
        self.kopru_kare = kopru_kare
        self.durumlar = {}

    def guncelle(self, bolge_no, bu_karede_dolu):
        """Döndürür: efektif_dolu (bool)"""
        if bolge_no not in self.durumlar:
            self.durumlar[bolge_no] = {"durum": "bos", "bekleme": 0}

        d = self.durumlar[bolge_no]

        if d["durum"] == "dolu":
            if bu_karede_dolu:
                d["bekleme"] = 0
                return True
            else:
                d["bekleme"] += 1
                if d["bekleme"] <= self.kopru_kare:
                    return True
                else:
                    d["durum"] = "bos"
                    d["bekleme"] = 0
                    return False
        else:
            if bu_karede_dolu:
                d["durum"] = "dolu"
                d["bekleme"] = 0
                return True
            else:
                return False


# ============================================================================
#  DİNAMİK BÖLGE ÇİZİMİ
# ============================================================================
def raise_opencv_window(window_name):
    try:
        cv2.setWindowProperty(window_name, cv2.WND_PROP_TOPMOST, 1)
        cv2.waitKey(1)
    except cv2.error:
        return
    if sys.platform == "win32":
        import ctypes
        hwnd = ctypes.windll.user32.FindWindowW(None, window_name)
        if hwnd:
            ctypes.windll.user32.SetForegroundWindow(hwnd)
    try:
        cv2.setWindowProperty(window_name, cv2.WND_PROP_TOPMOST, 0)
        cv2.waitKey(1)
    except cv2.error:
        pass


def draw_zones_interactive(image, existing_zones=None, parent=None):
    zones = normalize_zone_map(existing_zones)
    current_zone = []
    numeric_ids = [zone_numeric_id(zid) for zid in zones.keys() if str(zid).isdigit()]
    zone_id_counter = (max(numeric_ids) + 1) if numeric_ids else 1

    img_clean = image.copy()

    def mouse_callback(event, x, y, flags, param):
        nonlocal current_zone
        if event == cv2.EVENT_LBUTTONDOWN:
            current_zone.append([x, y])

    def next_zone_id():
        ids = [zone_numeric_id(zid) for zid in zones.keys() if str(zid).isdigit()]
        return (max(ids) + 1) if ids else 1

    def commit_current_zone():
        nonlocal zone_id_counter, current_zone
        if len(current_zone) < 3:
            return False
        zone_id = str(zone_id_counter)
        default_name = default_zone_name(zone_id)
        typed = ask_text(
            parent,
            "Bölge adı",
            f"Bölge {zone_id} için bir isim girin.",
            default_name,
        )
        zones[zone_id] = {
            "name": sanitize_zone_name(typed, zone_id),
            "points": [list(pt) for pt in current_zone],
        }
        current_zone = []
        zone_id_counter = next_zone_id()
        return True

    window_name = 'Bolge Cizimi (S/ESC: Kaydet, N: Yeni, C: Temizle, Z: Nokta Sil, D: Bolge Sil)'
    cv2.namedWindow(window_name)
    cv2.setMouseCallback(window_name, mouse_callback)

    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (0, 255, 255), (255, 0, 255)]

    while True:
        img_display = img_clean.copy()

        for zid, entry in zones.items():
            pts = zone_points(entry)
            if len(pts) < 3:
                continue
            color = colors[(zone_numeric_id(zid) - 1) % len(colors)]
            pts_arr = np.array(pts, np.int32).reshape((-1, 1, 2))
            cv2.polylines(img_display, [pts_arr], True, color, 2)
            label = zone_display_name(zid, entry)
            cv2.putText(img_display, label, tuple(pts[0]), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

        if len(current_zone) > 0:
            color = colors[(zone_id_counter - 1) % len(colors)]
            if len(current_zone) > 1:
                pts_arr = np.array(current_zone, np.int32).reshape((-1, 1, 2))
                cv2.polylines(img_display, [pts_arr], False, color, 2)
            for pt in current_zone:
                cv2.circle(img_display, tuple(pt), 4, color, -1)

        cv2.imshow(window_name, img_display)

        key = cv2.waitKey(20) & 0xFF
        if key == 27 or key == ord('s') or key == ord('S'):
            commit_current_zone()
            break
        elif key == ord('n') or key == ord('N'):
            if commit_current_zone():
                raise_opencv_window(window_name)
        elif key == ord('c') or key == ord('C'):
            current_zone = []
        elif key == ord('z') or key == ord('Z'):
            if current_zone:
                current_zone.pop()
        elif key == ord('d') or key == ord('D'):
            if current_zone:
                current_zone = []
            elif zones:
                last_id = max(zones.keys(), key=lambda zid: zone_numeric_id(zid) if str(zid).isdigit() else -1)
                del zones[last_id]
                zone_id_counter = next_zone_id()

    cv2.destroyWindow(window_name)
    return normalize_zone_map(zones)

# ============================================================================
#  PYQT6 AYARLAR ARAYÜZÜ (GUI)
# ============================================================================

class CameraRowWidget(QWidget):
    preview_requested = pyqtSignal(str, str)
    row_activated = pyqtSignal()

    def __init__(self, channel_id, label, checked=False, parent=None):
        super().__init__(parent)
        self.channel_id = str(channel_id)
        self.camera_name = ""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 8, 4)
        layout.setSpacing(8)

        self._mark = QFrame()
        self._mark.setFixedWidth(4)
        self._mark.setObjectName("cameraSelectMark")

        self.checkbox = QCheckBox()
        self.checkbox.setChecked(checked)
        self.checkbox.clicked.connect(self.row_activated.emit)

        self.name_label = QLabel(label)
        self.name_label.setMinimumWidth(0)
        self.name_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.name_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._full_label = label

        preview_btn = QPushButton("Önizle")
        preview_btn.setObjectName("secondaryButton")
        preview_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        preview_btn.setFixedHeight(28)
        preview_btn.setStyleSheet("QPushButton#secondaryButton { padding: 2px 10px; }")
        preview_btn.adjustSize()
        preview_btn.setFixedWidth(max(preview_btn.sizeHint().width() + 8, 90))
        preview_btn.clicked.connect(self._emit_preview)

        layout.addWidget(self._mark)
        layout.addWidget(self.checkbox)
        layout.addWidget(self.name_label, 1)
        layout.addWidget(preview_btn)
        self.setMinimumHeight(36)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.set_highlighted(False)

    def set_highlighted(self, on):
        if on:
            self.setStyleSheet(
                "CameraRowWidget {"
                f" background-color: {COLOR_PANEL};"
                f" border: 1px solid {COLOR_ACCENT};"
                " border-radius: 6px;"
                "}"
                "CameraRowWidget QLabel {"
                f" color: {COLOR_ACCENT};"
                " font-weight: 700;"
                " background: transparent;"
                "}"
                "CameraRowWidget QCheckBox { background: transparent; }"
                f"CameraRowWidget QFrame#cameraSelectMark {{ background-color: {COLOR_ACCENT}; border-radius: 2px; }}"
            )
        else:
            self.setStyleSheet(
                "CameraRowWidget { background: transparent; border: 1px solid transparent; border-radius: 6px; }"
                "CameraRowWidget QLabel { background: transparent; font-weight: 400; }"
                "CameraRowWidget QCheckBox { background: transparent; }"
                "CameraRowWidget QFrame#cameraSelectMark { background: transparent; }"
            )

    def set_camera_name(self, name):
        self.camera_name = name or ""

    def set_label(self, text):
        self._full_label = text
        self._elide_name()

    def is_checked(self):
        return self.checkbox.isChecked()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide_name()

    def _elide_name(self):
        text = self._full_label or ""
        width = max(self.name_label.width(), 40)
        self.name_label.setText(
            self.name_label.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, width)
        )
        self.name_label.setToolTip(text)

    def _emit_preview(self):
        self.row_activated.emit()
        self.preview_requested.emit(self.channel_id, self.camera_name)

    def mousePressEvent(self, event):
        self.row_activated.emit()
        super().mousePressEvent(event)


class SettingsGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("P-HAS Ayarlar")
        self.setMinimumSize(1000, 900)
        self.start_processing = False
        self._preview_windows = []
        self.setup_ui()
        self.apply_styles()

    def setup_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)
        layout.setContentsMargins(25, 25, 25, 25)
        layout.setSpacing(15)

        # Başlık
        title = QLabel("P-HAS Gelişmiş Takip Ayarları")
        title.setObjectName("titleLabel")
        title.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        layout.addSpacing(10)

        columns_layout = QHBoxLayout()
        columns_layout.setSpacing(20)

        left_column = QVBoxLayout()
        left_column.setSpacing(10)

        left_form = QFormLayout()
        left_form.setSpacing(12)

        self.input_rtsp_user = QLineEdit()
        self.input_rtsp_user.setText(CONFIG.get("RTSP_USER", DEFAULT_CONFIG["RTSP_USER"]))
        left_form.addRow("Kullanıcı Adı:", self.input_rtsp_user)

        self.input_rtsp_pass = QLineEdit()
        self.input_rtsp_pass.setText(CONFIG.get("RTSP_PASS", DEFAULT_CONFIG["RTSP_PASS"]))
        self.input_rtsp_pass.setEchoMode(QLineEdit.EchoMode.Password)
        left_form.addRow("Şifre:", self.input_rtsp_pass)

        self.input_rtsp_ip = QLineEdit()
        self.input_rtsp_ip.setText(CONFIG.get("RTSP_IP", DEFAULT_CONFIG["RTSP_IP"]))
        left_form.addRow("IP:", self.input_rtsp_ip)
        left_column.addLayout(left_form)

        base_cameras = [
            "D1 Kamera 1", "D2 Kamera 2", "D3 Kamera 3", "D4 Kamera 4", 
            "D5 Kamera 5", "D6 Kamera 6", "D7 Kamera 7", "D8 Kamera 8", 
            "D9 Kamera 9", "D10 Kamera 10", "D11 Kamera 11", "D12 Kamera 12", 
            "D13 Kamera 13", "D14 Kamera 14", "D15 Kamera 15", "D16 Kamera 16", 
            "D17 Kamera 17", "D18 Kamera 18", "D19 Kamera 19", "D20 Kamera 20", 
            "D21 Kamera 21", "D22 Kamera 22", "D23 Kamera 23", "D24 Kamera 24", 
            "D25 Kamera 25", "D26 Kamera 26", "D27 Kamera 27", "D28 Kamera 28", 
            "D29 Kamera 29", "D30 Kamera 30", "D31 Kamera 31", "D32 Kamera 32", 
            "D33 Kamera 33", "D34 Kamera 34", "D35 Kamera 35", "D36 Kamera 36", 
            "D37 Kamera 37", "D38 Kamera 38", "D39 Kamera 39", "D40 Kamera 40", 
            "D41 Kamera 41", "D45 Kamera 45", "D46 Kamera 46", "D47 Kamera 47", 
            "D48 Kamera 48", "D49 Kamera 49", "D52 Kamera 52", "D53 Kamera 53", 
            "D55 Kamera 55", "D56 Kamera 56", "D57 Kamera 57", "D59 Kamera 59", "D60 Kamera 60", 
            "D61 Kamera 61", "D62 Kamera 62", "D63 Kamera 63", "D64 Kamera 64"
        ]
        self.kamera_isimleri = { int(cam.split(' ')[0][1:]): cam for cam in base_cameras }

        self.btn_fetch_cameras = QPushButton("Kameraları Bul / Yenile")
        self.btn_fetch_cameras.setObjectName("secondaryButton")
        self.btn_fetch_cameras.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_fetch_cameras.clicked.connect(self.fetch_cameras_isapi)
        left_column.addWidget(self.btn_fetch_cameras)
        left_column.addStretch(1)

        # Sağ Sütun
        right_widget = QWidget()
        right_widget.setMinimumWidth(300)
        right_widget.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        right_layout = QFormLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setHorizontalSpacing(12)
        right_layout.setVerticalSpacing(12)
        right_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        right_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        lbl_kayip = QLabel("Maks Kayıp Süresi (sn) ℹ️:")
        tt_kayip = "Takip edilen kişinin kameranın görüş açısından veya bir engelin arkasına geçmesi durumunda, sistemin onu hafızasında tutmaya devam edeceği maksimum süre."
        lbl_kayip.setToolTip(tt_kayip)
        lbl_kayip.setWordWrap(True)
        self.spin_kayip = QDoubleSpinBox()
        self.spin_kayip.setToolTip(tt_kayip)
        self.spin_kayip.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.spin_kayip.setRange(0.0, 100.0)
        self.spin_kayip.setSingleStep(1.0)
        self.spin_kayip.setValue(float(CONFIG.get("MAX_KAYIP_SURESI_SN", DEFAULT_CONFIG["MAX_KAYIP_SURESI_SN"])))
        self._style_param_spin(self.spin_kayip)
        right_layout.addRow(lbl_kayip, self.spin_kayip)

        lbl_kopru = QLabel("Köprü Süresi (sn) ℹ️:")
        tt_kopru = "Kameradaki anlık takılmalar veya yapay zekanın kişiyi saliselik kaybetmesi durumunda, yeni bir ID atamamak için tolere edilecek bağlantı süresi."
        lbl_kopru.setToolTip(tt_kopru)
        lbl_kopru.setWordWrap(True)
        self.spin_kopru = QDoubleSpinBox()
        self.spin_kopru.setToolTip(tt_kopru)
        self.spin_kopru.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.spin_kopru.setRange(0.0, 10.0)
        self.spin_kopru.setSingleStep(0.5)
        self.spin_kopru.setValue(float(CONFIG.get("KOPRU_SURESI_SN", DEFAULT_CONFIG["KOPRU_SURESI_SN"])))
        self._style_param_spin(self.spin_kopru)
        right_layout.addRow(lbl_kopru, self.spin_kopru)

        lbl_hayalet = QLabel("Hayalet Eşiği (sn) ℹ️:")
        tt_hayalet = "Yanlış algılanan cansız nesnelerin (makine parçası, gölge vb.) sistemden otomatik olarak silinmesi için beklenecek süre."
        lbl_hayalet.setToolTip(tt_hayalet)
        lbl_hayalet.setWordWrap(True)
        self.spin_hayalet = QDoubleSpinBox()
        self.spin_hayalet.setToolTip(tt_hayalet)
        self.spin_hayalet.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.spin_hayalet.setRange(0.0, 10.0)
        self.spin_hayalet.setSingleStep(0.5)
        self.spin_hayalet.setValue(float(CONFIG.get("HAYALET_ESIK_SN", DEFAULT_CONFIG["HAYALET_ESIK_SN"])))
        self._style_param_spin(self.spin_hayalet)
        right_layout.addRow(lbl_hayalet, self.spin_hayalet)

        lbl_min_bolge = QLabel("Min Bölge Kalma (sn) ℹ️:")
        tt_min_bolge = "Bir ihlalin kaydedilmesi için kişinin yasaklı bölge içinde kesintisiz olarak kalması gereken asgari süre."
        lbl_min_bolge.setToolTip(tt_min_bolge)
        lbl_min_bolge.setWordWrap(True)
        self.spin_min_bolge = QDoubleSpinBox()
        self.spin_min_bolge.setToolTip(tt_min_bolge)
        self.spin_min_bolge.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.spin_min_bolge.setRange(0.0, 10.0)
        self.spin_min_bolge.setSingleStep(0.5)
        self.spin_min_bolge.setValue(float(CONFIG.get("MIN_BOLGE_KALMA_SN", DEFAULT_CONFIG["MIN_BOLGE_KALMA_SN"])))
        self._style_param_spin(self.spin_min_bolge)
        right_layout.addRow(lbl_min_bolge, self.spin_min_bolge)

        lbl_conf = QLabel("Güven Eşiği ℹ️:")
        tt_conf = "Yapay zekanın bir nesneyi insan olarak kabul etmesi için gereken minimum emin olma oranı (0.0 - 1.0 arası)."
        lbl_conf.setToolTip(tt_conf)
        lbl_conf.setWordWrap(True)
        self.spin_conf = QDoubleSpinBox()
        self.spin_conf.setToolTip(tt_conf)
        self.spin_conf.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.spin_conf.setRange(0.0, 1.0)
        self.spin_conf.setSingleStep(0.05)
        self.spin_conf.setValue(float(CONFIG.get("CONFIDENCE", DEFAULT_CONFIG["CONFIDENCE"])))
        self._style_param_spin(self.spin_conf)
        right_layout.addRow(lbl_conf, self.spin_conf)

        columns_layout.addLayout(left_column, 1)
        columns_layout.addWidget(right_widget, 0)

        layout.addLayout(columns_layout)

        self.input_camera_search = QLineEdit()
        self.input_camera_search.setPlaceholderText("Kamera ara...")
        self.input_camera_search.textChanged.connect(self._filter_cameras)
        layout.addWidget(self.input_camera_search)

        cameras_label = QLabel("Kameralar:")
        layout.addWidget(cameras_label)
        cameras_hint = QLabel("Kutu = takip. Satıra tıkla = bölge çizilecek kamera.")
        cameras_hint.setObjectName("mutedLabel")
        cameras_hint.setWordWrap(True)
        layout.addWidget(cameras_hint)

        self.list_cameras = QListWidget()
        self.list_cameras.setObjectName("cameraList")
        self.list_cameras.setMinimumHeight(120)
        self.list_cameras.setMaximumHeight(150)
        self.list_cameras.setSpacing(3)
        self.list_cameras.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_cameras.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.list_cameras.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.list_cameras.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.list_cameras.currentItemChanged.connect(self._sync_camera_row_highlight)
        layout.addWidget(self.list_cameras)
        self._load_saved_cameras_into_list()

        action_row = QHBoxLayout()
        action_row.setSpacing(10)
        self.btn_reports = QPushButton("Raporlar")
        self.btn_reports.setObjectName("secondaryButton")
        self.btn_reports.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reports.clicked.connect(self._open_reports)
        self.btn_train_model = QPushButton("Model Eğit")
        self.btn_train_model.setObjectName("secondaryButton")
        self.btn_train_model.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_train_model.clicked.connect(self._open_model_train)
        self.btn_draw_zones = QPushButton("Seçili Kameraya Bölge Çiz")
        self.btn_draw_zones.setObjectName("secondaryButton")
        self.btn_draw_zones.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_draw_zones.clicked.connect(self.draw_dynamic_zones)
        action_row.addWidget(self.btn_reports, 1)
        action_row.addWidget(self.btn_train_model, 1)
        action_row.addWidget(self.btn_draw_zones, 1)
        layout.addLayout(action_row)

        shift_row = QHBoxLayout()
        shift_row.setSpacing(10)
        self.btn_shifts = QPushButton("Vardiyalar")
        self.btn_shifts.setObjectName("secondaryButton")
        self.btn_shifts.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_shifts.clicked.connect(self._open_shifts)
        shift_row.addWidget(self.btn_shifts, 1)
        shift_row.addStretch(2)
        layout.addLayout(shift_row)

        self.check_video_save = QCheckBox("İşlenmiş video kaydı tutulsun mu?")
        self.check_video_save.setChecked(CONFIG.get("VIDEO_SAVE_ENABLED", DEFAULT_CONFIG["VIDEO_SAVE_ENABLED"]))
        layout.addWidget(self.check_video_save)

        self.check_hard_frames = QCheckBox("Zor kareler otomatik kaydedilsin mi?")
        self.check_hard_frames.setChecked(
            CONFIG.get("HARD_FRAMES_ENABLED", DEFAULT_CONFIG["HARD_FRAMES_ENABLED"])
        )
        self.check_hard_frames.setToolTip(
            "Takip sırasında modelin emin olmadığı veya kaybettiği anların ham fotoğrafını "
            "datasets/zor altına yazar. Overlay yoktur. Eğitimde «Bugünün zor kareleri» ile açılır."
        )
        layout.addWidget(self.check_hard_frames)

        # Butonlar
        btn_layout = QHBoxLayout()
        self.btn_save = QPushButton("Kaydet ve Başlat")
        self.btn_save.setObjectName("primaryButton")
        self.btn_save.setMinimumHeight(40)
        self.btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self.btn_save.clicked.connect(self.save_and_start)
        btn_layout.addWidget(self.btn_save)
        
        layout.addLayout(btn_layout)

    def _style_param_spin(self, spin):
        spin.setMinimumWidth(80)
        spin.setMinimumHeight(36)
        spin.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        spin.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)

    def _zone_count(self, channel_id):
        return len(get_camera_zones(channel_id))

    def _format_camera_label(self, channel_id, name):
        return f"{name}  ({self._zone_count(channel_id)} bölge)"

    def _camera_row(self, item):
        if item is None:
            return None
        return self.list_cameras.itemWidget(item)

    def _add_camera_item(self, channel_id, name, checked=False):
        channel_id = str(channel_id)
        item = QListWidgetItem()
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        item.setData(Qt.ItemDataRole.UserRole, channel_id)
        item.setData(CAMERA_NAME_ROLE, name)
        label = self._format_camera_label(channel_id, name)
        row = CameraRowWidget(channel_id, label, checked=checked)
        row.set_camera_name(name)
        row.preview_requested.connect(self._open_camera_preview)
        row.row_activated.connect(lambda it=item: self.list_cameras.setCurrentItem(it))
        item.setSizeHint(QSize(1, 40))
        self.list_cameras.addItem(item)
        self.list_cameras.setItemWidget(item, row)
        self._sync_camera_row_highlight()
        return item

    def _refresh_camera_item_labels(self):
        for i in range(self.list_cameras.count()):
            item = self.list_cameras.item(i)
            channel_id = item.data(Qt.ItemDataRole.UserRole)
            name = item.data(CAMERA_NAME_ROLE) or get_camera_entry(channel_id).get("name") or f"Kamera {channel_id}"
            label = self._format_camera_label(channel_id, name)
            row = self._camera_row(item)
            if row is not None:
                row.set_camera_name(name)
                row.set_label(label)

    def _highlighted_camera(self):
        item = self.list_cameras.currentItem()
        if item is None:
            return None, None
        row = self._camera_row(item)
        name = item.data(CAMERA_NAME_ROLE)
        if row is not None and row.camera_name:
            name = row.camera_name
        return item.data(Qt.ItemDataRole.UserRole), name

    def _checked_cameras(self):
        selected = []
        for i in range(self.list_cameras.count()):
            item = self.list_cameras.item(i)
            row = self._camera_row(item)
            if row is None or not row.is_checked():
                continue
            channel_id = str(item.data(Qt.ItemDataRole.UserRole))
            selected.append({
                "id": channel_id,
                "name": item.data(CAMERA_NAME_ROLE) or f"Kamera {channel_id}"
            })
        return selected

    def _filter_cameras(self, text=None):
        query = (self.input_camera_search.text() if text is None else text).strip().lower()
        for i in range(self.list_cameras.count()):
            item = self.list_cameras.item(i)
            channel_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
            name = str(item.data(CAMERA_NAME_ROLE) or "")
            row = self._camera_row(item)
            label = row.name_label.text() if row is not None else ""
            haystack = " ".join((name, channel_id, label)).lower()
            item.setHidden(bool(query) and query not in haystack)

    def _open_camera_preview(self, channel_id, camera_name):
        ip = self.input_rtsp_ip.text().strip()
        user = self.input_rtsp_user.text().strip()
        password = self.input_rtsp_pass.text()
        if not ip or not user or not password:
            show_warning(self, "Uyarı", "Lütfen önce IP, Kullanıcı ve Şifre alanlarını doldurun.")
            return
        name = camera_name or f"Kamera {channel_id}"
        dialog = CameraPreviewDialog(self, name, channel_id, build_rtsp_url(user, password, ip, channel_id))
        self._preview_windows.append(dialog)
        dialog.finished.connect(lambda _result, dlg=dialog: self._forget_preview(dlg))
        dialog.show()

    def _forget_preview(self, dialog):
        if dialog in self._preview_windows:
            self._preview_windows.remove(dialog)

    def _open_reports(self):
        open_report_dialog(self)

    def _open_shifts(self):
        cameras = []
        for i in range(self.list_cameras.count()):
            item = self.list_cameras.item(i)
            cameras.append({
                "id": item.data(Qt.ItemDataRole.UserRole),
                "name": item.data(CAMERA_NAME_ROLE) or f"Kamera {item.data(Qt.ItemDataRole.UserRole)}",
            })
        dialog = ShiftDialog(
            self,
            CONFIG,
            lambda: save_config(CONFIG),
            cameras,
            get_camera_zones,
        )
        dialog.exec()

    def _open_model_train(self):
        cameras = []
        for i in range(self.list_cameras.count()):
            item = self.list_cameras.item(i)
            cameras.append({
                "id": item.data(Qt.ItemDataRole.UserRole),
                "name": item.data(CAMERA_NAME_ROLE) or f"Kamera {item.data(Qt.ItemDataRole.UserRole)}",
            })
        dialog = ModelTrainDialog(
            self,
            cameras=cameras,
            app_dir=APP_DIR,
            is_busy=lambda: pipeline_running,
        )
        dialog.exec()

    def _load_saved_cameras_into_list(self):
        self.list_cameras.clear()
        cameras = CONFIG.get("CAMERAS", {})
        selected = {str(x) for x in CONFIG.get("SELECTED_CAMERAS", [])}
        if cameras:
            for channel_id, entry in cameras.items():
                name = (entry or {}).get("name") or f"Kamera {channel_id}"
                self._add_camera_item(channel_id, name, checked=str(channel_id) in selected)
        else:
            channel = str(CONFIG.get("RTSP_CHANNEL", DEFAULT_CONFIG["RTSP_CHANNEL"]))
            if channel:
                self._add_camera_item(channel, f"Kamera {channel}", checked=True)
        self._select_default_camera_row()
        self._filter_cameras()

    def _select_default_camera_row(self):
        if self.list_cameras.count() == 0:
            return
        for i in range(self.list_cameras.count()):
            item = self.list_cameras.item(i)
            row = self._camera_row(item)
            if row is not None and row.is_checked():
                self.list_cameras.setCurrentItem(item)
                self._sync_camera_row_highlight()
                return
        self.list_cameras.setCurrentRow(0)
        self._sync_camera_row_highlight()

    def _sync_camera_row_highlight(self, current=None, _previous=None):
        if current is None:
            current = self.list_cameras.currentItem()
        for i in range(self.list_cameras.count()):
            item = self.list_cameras.item(i)
            row = self._camera_row(item)
            if row is not None:
                row.set_highlighted(item is current)

    def apply_styles(self):
        apply_dark_title_bar(self)

    def showEvent(self, event):
        super().showEvent(event)
        apply_dark_title_bar(self)

    def _has_remote_live_view_permission(self, session, ip, channel_id):
        """
        Giris yapan kullanicinin belirtilen NVR kanalinda Uzak Canli Goruntuleme
        yetkisini ISAPI uzerinden dogrular.

        Hikvision NVR/DVR tarafinda IP kanalindan anlik goruntu almak icin
        ContentMgmt/StreamingProxy picture endpoint'i kullanilir. Yetkisiz
        kullanicida istek basarisiz olur; yalnizca basarili goruntu cevabi
        alan kanallar kamera listesine dahil edilir.
        """
        picture_url = (
            f"http://{ip}/ISAPI/ContentMgmt/StreamingProxy/"
            f"channels/{channel_id}/picture"
        )

        try:
            # stream=True: Tum JPEG'i bellege indirmeden HTTP cevabini kontrol ederiz.
            response = session.get(picture_url, timeout=4, stream=True)
            try:
                if response.status_code != 200:
                    return False

                # Basarili snapshot cevabi normalde image/* olur. XML/text cevabi
                # bir ISAPI hata mesaji olabileceginden yetkili kabul etmiyoruz.
                content_type = response.headers.get("Content-Type", "").lower()
                if "xml" in content_type or content_type.startswith("text/"):
                    return False

                return True
            finally:
                response.close()

        except requests.RequestException:
            # Fail-closed: Yetki kesin dogrulanamiyorsa kamerayi gostermiyoruz.
            return False

    def fetch_cameras_isapi(self):
        ip = self.input_rtsp_ip.text().strip()
        user = self.input_rtsp_user.text().strip()
        pwd = self.input_rtsp_pass.text()

        if not ip or not user or not pwd:
            show_warning(self, "Uyari", "Lutfen once IP, Kullanici ve Sifre alanlarini doldurun.")
            return

        # Ayni Digest oturumunu hem kanal listesini cekmek hem de kanal bazli
        # Uzak Canli Goruntuleme yetkisini sinamak icin kullaniyoruz.
        session = requests.Session()
        session.auth = HTTPDigestAuth(user, pwd)

        url = f"http://{ip}/ISAPI/Streaming/channels"
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)

        try:
            response = session.get(url, timeout=5)

            if response.status_code != 200:
                show_warning(
                    self,
                    "Uyari",
                    f"HTTP Hatasi: {response.status_code}\nDetay: {response.text[:200]}"
                )
                return

            xml_text = re.sub(r'\sxmlns="[^"]+"', '', response.text, count=1)
            root = ET.fromstring(xml_text)

            # Once NVR'nin dondugu ana stream kanallarini topla.
            candidate_channels = []
            for channel_elem in root.iter('StreamingChannel'):
                id_elem = channel_elem.find('id')
                if id_elem is None or not id_elem.text:
                    continue

                ch_id = id_elem.text.strip()
                name_elem = channel_elem.find('channelName')
                ch_name = (
                    name_elem.text.strip()
                    if name_elem is not None and name_elem.text
                    else ""
                )

                try:
                    id_int = int(ch_id)
                except ValueError:
                    continue

                d_num = id_int // 100
                stream_type = id_int % 100

                # 01 = main stream. 102, 202... gibi sub-stream'leri listeleme.
                if stream_type != 1:
                    continue

                if d_num in self.kamera_isimleri:
                    friendly_name = self.kamera_isimleri[d_num]
                else:
                    friendly_name = ch_name if ch_name else f"Kamera {d_num}"

                candidate_channels.append((ch_id, friendly_name))

            # Kritik kisim: Her kanali GIRIS YAPAN KULLANICI ile ISAPI uzerinden
            # dogrula. Sadece Uzak Canli Goruntuleme yapabildigi kanallari tut.
            authorized_channels = []
            for ch_id, friendly_name in candidate_channels:
                if self._has_remote_live_view_permission(session, ip, ch_id):
                    authorized_channels.append((ch_id, friendly_name))

            if not authorized_channels:
                show_warning(
                    self,
                    "Uyari",
                    "Bu kullanici icin erisilebilir kamera bulunamadi.\n"
                    "Kullanici adi/sifreyi ve NVR'daki 'Uzak: Canli Goruntuleme' "
                    "kanal yetkilerini kontrol edin."
                )
                return

            previously_checked = {cam["id"] for cam in self._checked_cameras()}
            saved_selected = {str(x) for x in CONFIG.get("SELECTED_CAMERAS", [])}
            checked_ids = previously_checked or saved_selected

            if "CAMERAS" not in CONFIG or not isinstance(CONFIG["CAMERAS"], dict):
                CONFIG["CAMERAS"] = {}

            self.list_cameras.clear()
            for ch_id, display in authorized_channels:
                ch_id = str(ch_id)
                entry = dict(CONFIG["CAMERAS"].get(ch_id, {}))
                entry["name"] = display
                entry.setdefault("zones", {})
                CONFIG["CAMERAS"][ch_id] = entry
                self._add_camera_item(ch_id, display, checked=ch_id in checked_ids)

            self._select_default_camera_row()
            self._filter_cameras()

            show_info(
                self,
                "Basarili",
                f"{len(authorized_channels)} yetkili kamera kanali yuklendi."
            )

        except requests.RequestException as e:
            show_warning(self, "Uyari", f"Baglanti Hatasi: {str(e)}")
        except ET.ParseError as e:
            show_warning(self, "Uyari", f"XML ayristirma hatasi: {str(e)}")
        except Exception as e:
            show_warning(self, "Uyari", f"Kamera listesi alinamadi: {str(e)}")
        finally:
            session.close()
            QApplication.restoreOverrideCursor()

    def draw_dynamic_zones(self):
        channel, name = self._highlighted_camera()
        if not channel:
            show_warning(self, "Uyarı", "Bölge çizmek için listeden bir kamera seçin (satıra tıklayın).")
            return

        user = self.input_rtsp_user.text()
        passwd = self.input_rtsp_pass.text()
        ip = self.input_rtsp_ip.text()
        name = name or get_camera_entry(channel).get("name") or f"Kamera {channel}"
        rtsp_url = build_rtsp_url(user, passwd, ip, channel)

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        cap = cv2.VideoCapture(rtsp_url)
        is_opened = cap.isOpened()

        if not is_opened:
            cap.release()
            QApplication.restoreOverrideCursor()
            show_error(self, "Bağlantı Hatası", "Bu kamerada yetkiniz yok veya kamera şu an çevrimdışı/çalışmıyor.")
            return

        for _ in range(40):
            cap.read()

        ret, frame = cap.read()
        cap.release()
        QApplication.restoreOverrideCursor()

        if not ret:
            show_error(self, "Kare Hatası", "Kameraya bağlanıldı fakat kare okunamadı.")
            return

        frame = cv2.resize(frame, (1280, 720))
        existing_zones = get_camera_zones(channel)
        zones = draw_zones_interactive(frame, existing_zones=existing_zones, parent=self)

        if "CAMERAS" not in CONFIG or not isinstance(CONFIG["CAMERAS"], dict):
            CONFIG["CAMERAS"] = {}
        entry = dict(CONFIG["CAMERAS"].get(str(channel), {}))
        entry["name"] = name
        entry["zones"] = normalize_zone_map(zones)
        CONFIG["CAMERAS"][str(channel)] = entry
        CONFIG["CUSTOM_ZONES"] = entry["zones"]
        CONFIG["RTSP_CHANNEL"] = str(channel)
        save_config(CONFIG)
        self._refresh_camera_item_labels()
        show_info(self, "Başarılı", f"{name}: {len(zones)} bölge kaydedildi.")

    def save_and_start(self):
        checked = self._checked_cameras()
        if not checked:
            show_warning(self, "Uyarı", "En az bir kamera işaretleyin.")
            return

        if len(checked) >= 4:
            show_warning(
                self,
                "GPU Uyarısı",
                f"{len(checked)} kamera seçildi. Paralel işlem GPU yükünü artırabilir."
            )

        CONFIG["RTSP_USER"] = self.input_rtsp_user.text()
        CONFIG["RTSP_PASS"] = self.input_rtsp_pass.text()
        CONFIG["RTSP_IP"] = self.input_rtsp_ip.text()
        CONFIG["VIDEO_SAVE_ENABLED"] = self.check_video_save.isChecked()
        CONFIG["HARD_FRAMES_ENABLED"] = self.check_hard_frames.isChecked()
        CONFIG["MAX_KAYIP_SURESI_SN"] = self.spin_kayip.value()
        CONFIG["KOPRU_SURESI_SN"] = self.spin_kopru.value()
        CONFIG["HAYALET_ESIK_SN"] = self.spin_hayalet.value()
        CONFIG["MIN_BOLGE_KALMA_SN"] = self.spin_min_bolge.value()
        CONFIG["CONFIDENCE"] = self.spin_conf.value()
        CONFIG["SELECTED_CAMERAS"] = [cam["id"] for cam in checked]
        CONFIG["RTSP_CHANNEL"] = checked[0]["id"]

        if "CAMERAS" not in CONFIG or not isinstance(CONFIG["CAMERAS"], dict):
            CONFIG["CAMERAS"] = {}

        selected_cameras = []
        failed = []
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for cam in checked:
                channel_id = cam["id"]
                name = cam["name"]
                entry = dict(CONFIG["CAMERAS"].get(channel_id, {}))
                entry["name"] = name
                entry["zones"] = normalize_zone_map(entry.get("zones") or get_camera_zones(channel_id))
                CONFIG["CAMERAS"][channel_id] = entry
                url = build_rtsp_url(
                    CONFIG["RTSP_USER"], CONFIG["RTSP_PASS"], CONFIG["RTSP_IP"], channel_id
                )
                test_cap = cv2.VideoCapture(url)
                is_opened = test_cap.isOpened()
                test_cap.release()
                if not is_opened:
                    failed.append(name)
                    continue
                selected_cameras.append({
                    "id": channel_id,
                    "name": name,
                    "url": url,
                    "zones": entry.get("zones", {}),
                })
        finally:
            QApplication.restoreOverrideCursor()

        if failed:
            show_warning(
                self,
                "Bağlantı Uyarısı",
                "Şu kameralara bağlanılamadı, atlanacak:\n" + "\n".join(failed)
            )

        if not selected_cameras:
            QMessageBox.critical(self, "Bağlantı Hatası", "Seçilen kameraların hiçbiri açılamadı.")
            return

        CONFIG["VIDEO_YOLU"] = selected_cameras[0]["url"]
        CONFIG["CUSTOM_ZONES"] = selected_cameras[0].get("zones", {})
        save_config(CONFIG)

        self.start_processing = True
        self.close()
        threading.Thread(target=start_multi_camera, args=(selected_cameras,), daemon=True).start()


def run_single_camera(camera_id, name, url, zones, model, infer_lock):
    global stop_ai_flag
    import random

    prefix = f"[{name}]"
    cap = open_rtsp_capture(url)
    if not cap.isOpened():
        print(f"{prefix} HATA: '{url}' açılamadı!")
        return {"camera_id": camera_id, "camera_name": name, "error": "stream_open_failed"}

    print(f"{prefix} RTSP akışı senkronize ediliyor (Buffer temizliği)...")
    for _ in range(15):
        if not cap.grab():
            break

    warmup_yolo_model(model, infer_lock, prefix=prefix)
    for _ in range(15):
        if not cap.grab():
            break

    fps = normalize_fps(cap.get(cv2.CAP_PROP_FPS))
    width = OUTPUT_WIDTH
    height = OUTPUT_HEIGHT
    out = None
    video_path = build_camera_video_path(name)
    if VIDEO_SAVE_ENABLED:
        out = cv2.VideoWriter(
            video_path,
            cv2.VideoWriter_fourcc(*"mp4v"),
            float(fps),
            (width, height),
            True,
        )
        if out is None or not out.isOpened():
            print(f"{prefix} HATA: Video dosyası açılamadı: {video_path}")
            out = None
        else:
            print(f"{prefix} Video kaydı: {video_path}")

    KOPRU_KARE = int(KOPRU_SURESI_SN * fps)
    HAYALET_KARE = int(HAYALET_ESIK_SN * fps)
    MIN_ZIYARET_KARE = int(MIN_BOLGE_KALMA_SN * fps)

    tracker = GelismisIsciTracker(
        max_kayip_suresi_sn=MAX_KAYIP_SURESI_SN,
        fps=fps,
        yuksek_guven_esigi=CONFIDENCE
    )
    hard_sink = HardFrameSink(APP_DIR, name, enabled=HARD_FRAMES_ENABLED)
    if HARD_FRAMES_ENABLED:
        print(f"{prefix} Zor kare kaydı: {hard_sink.folder()}")
    ziyaret_takip = ZiyaretTakip(kopru_kare=KOPRU_KARE, min_ziyaret_kare=MIN_ZIYARET_KARE)
    zone_doluluk_debouncer = ZoneDolulukDebouncer(kopru_kare=KOPRU_KARE)

    custom_zones = normalize_zone_map(zones)
    bolge_poligonlari = {}
    bolge_adlari = {}
    if not custom_zones:
        print(f"{prefix} Çizili özel bölge bulunamadı, tüm ekran (full-screen) bölgesi kullanılıyor.")
        full_screen_pts = [[0, 0], [1280, 0], [1280, 720], [0, 720]]
        bolge_poligonlari[1] = np.array(full_screen_pts, np.int32)
        bolge_adlari[1] = default_zone_name(1)
    else:
        for zid, entry in custom_zones.items():
            znum = zone_numeric_id(zid)
            if znum <= 0:
                continue
            pts = zone_points(entry)
            if len(pts) < 3:
                continue
            bolge_poligonlari[znum] = np.array(pts, np.int32)
            bolge_adlari[znum] = zone_display_name(znum, entry)

    bolge_renkleri = {}
    for z_key in bolge_poligonlari.keys():
        bolge_renkleri[z_key] = (random.randint(50, 255), random.randint(50, 255), random.randint(50, 255))

    isci_toplam_sure = {}
    isci_ilk_gorunum = {}
    isci_son_gorunum = {}
    geri_donus_sayaci = 0
    frame_sayaci = 0
    gray_streak = 0
    fail_streak = 0
    reconnect_failures = 0
    baseline_std = None
    last_process_at = None
    overlay_state = {"kutular": [], "kayip": [], "bilgi": []}
    oturum_id = uuid.uuid4()
    oturum_baslangic = datetime.now()
    overlay_lock = threading.Lock()
    overlay_ready = threading.Event()
    latest_lock = threading.Lock()
    cap_lock = threading.Lock()
    latest_raw = [None]
    latest_seq = [0]
    last_infer_seq = -1
    capture_stop = threading.Event()
    capture_failed = threading.Event()
    video_queue = queue.Queue(maxsize=120)
    writer_thread = None
    write_lock = threading.Lock()
    video_close_lock = threading.Lock()
    video_closed = threading.Event()

    print("=" * 65)
    print(f"  {prefix} TUTARLI İŞÇİ TAKİP SİSTEMİ v4.0 (Stable ID)")
    print("-" * 65)
    print(f"  Oklusyon hafızası     : {MAX_KAYIP_SURESI_SN} saniye")
    print(f"  Min. bbox alanı       : {MIN_BBOX_ALAN} piksel²")
    print(f"  ID eşleştirme         : {'Hungarian' if _HUNGARIAN_AVAILABLE else 'Deterministik greedy'}")
    print(f"  Güven eşikleri        : yüksek={tracker.yuksek_guven_esigi:.2f}, düşük={tracker.dusuk_guven_esigi:.2f}")
    print(f"  Köprü süresi           : {KOPRU_SURESI_SN}s ({KOPRU_KARE} kare)")
    print(f"  Hayalet eşiği          : {HAYALET_ESIK_SN}s ({HAYALET_KARE} kare)")
    print(f"  Min. bölge kalma       : {MIN_BOLGE_KALMA_SN}s ({MIN_ZIYARET_KARE} kare)")
    print(f"  Video FPS              : {fps}")
    print("=" * 65)

    def capture_loop():
        nonlocal cap, out, gray_streak, fail_streak, reconnect_failures, baseline_std
        while not stop_ai_flag and not capture_stop.is_set():
            with cap_lock:
                local_cap = cap
            if local_cap is None or not local_cap.isOpened():
                fail_streak += 1
                if fail_streak >= RTSP_FAIL_RECONNECT:
                    print(f"{prefix} Kare alınamadı, RTSP yeniden bağlanıyor...")
                    with cap_lock:
                        cap = reconnect_rtsp(url, cap, prefix)
                    fail_streak = 0
                    if cap is None or not cap.isOpened():
                        reconnect_failures += 1
                        if reconnect_failures >= MAX_RTSP_RECONNECT_FAILURES:
                            print(f"\n{prefix} Kamera bağlantısı koptu. Raporlama aşamasına geçiliyor...")
                            capture_failed.set()
                            break
                time.sleep(0.05)
                continue

            success, frame = local_cap.read()
            if not success or frame is None:
                fail_streak += 1
                if fail_streak >= RTSP_FAIL_RECONNECT:
                    print(f"{prefix} Kare alınamadı, RTSP yeniden bağlanıyor...")
                    with cap_lock:
                        cap = reconnect_rtsp(url, cap, prefix)
                    fail_streak = 0
                    if cap is None or not cap.isOpened():
                        reconnect_failures += 1
                        if reconnect_failures >= MAX_RTSP_RECONNECT_FAILURES:
                            print(f"\n{prefix} Kamera bağlantısı koptu. Raporlama aşamasına geçiliyor...")
                            capture_failed.set()
                            break
                continue

            frame = normalize_output_frame(frame)
            if frame is None or is_flat_gray_frame(frame, baseline_std):
                gray_streak += 1
                if gray_streak >= GRAY_FRAME_RECONNECT:
                    print(f"{prefix} Ardışık gri kare, RTSP yeniden bağlanıyor...")
                    with cap_lock:
                        cap = reconnect_rtsp(url, cap, prefix)
                    gray_streak = 0
                    baseline_std = None
                    if cap is None or not cap.isOpened():
                        reconnect_failures += 1
                        if reconnect_failures >= MAX_RTSP_RECONNECT_FAILURES:
                            print(f"\n{prefix} Kamera bağlantısı koptu. Raporlama aşamasına geçiliyor...")
                            capture_failed.set()
                            break
                continue

            fail_streak = 0
            gray_streak = 0
            reconnect_failures = 0
            std_now, _chroma = frame_quality_stats(frame)
            if baseline_std is None:
                baseline_std = std_now
            else:
                baseline_std = 0.9 * baseline_std + 0.1 * std_now

            with latest_lock:
                latest_raw[0] = frame
                latest_seq[0] += 1

            if VIDEO_SAVE_ENABLED and out is not None and overlay_ready.is_set():
                with overlay_lock:
                    overlay_copy = {
                        "kutular": list(overlay_state.get("kutular", [])),
                        "kayip": list(overlay_state.get("kayip", [])),
                        "bilgi": list(overlay_state.get("bilgi", [])),
                    }
                item = (frame.copy(), overlay_copy)
                try:
                    video_queue.put(item, timeout=0.05)
                except queue.Full:
                    try:
                        video_queue.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        video_queue.put_nowait(item)
                    except queue.Full:
                        pass

        capture_stop.set()

    def writer_loop():
        written = 0
        try:
            while True:
                try:
                    item = video_queue.get(timeout=0.25)
                except queue.Empty:
                    if video_closed.is_set():
                        break
                    continue
                if item is None or video_closed.is_set():
                    break
                frame, overlay_copy = item
                vis = ciz_takip_katmani(frame, bolge_poligonlari, bolge_renkleri, overlay_copy)
                with write_lock:
                    if out is None or video_closed.is_set():
                        break
                    out.write(vis)
                    written += 1
        except Exception as exc:
            print(f"{prefix} Video yazıcı hatası: {exc}")
        print(f"{prefix} Video kare yazıldı: {written} -> {video_path if VIDEO_SAVE_ENABLED else '-'}")

    def finalize_video():
        nonlocal cap, out
        with video_close_lock:
            if video_closed.is_set():
                return
            capture_stop.set()
            with cap_lock:
                if cap is not None:
                    cap.release()
                    cap = None
            while True:
                try:
                    video_queue.get_nowait()
                except queue.Empty:
                    break
            try:
                video_queue.put_nowait(None)
            except queue.Full:
                pass
            if writer_thread is not None and writer_thread.is_alive():
                writer_thread.join(timeout=4.0)
            with write_lock:
                if out is not None:
                    out.release()
                    out = None
            video_closed.set()
            unregister_video_finalizer(finalize_video)

    writer_thread = threading.Thread(target=writer_loop, name=f"writer-{camera_id}", daemon=False)
    if VIDEO_SAVE_ENABLED and out is not None:
        writer_thread.start()
    register_video_finalizer(finalize_video)

    capture_thread = threading.Thread(target=capture_loop, name=f"capture-{camera_id}", daemon=True)
    capture_thread.start()

    try:
        while True:
            if stop_ai_flag or capture_failed.is_set() or capture_stop.is_set():
                if stop_ai_flag:
                    print(f"\n{prefix} Çıkış tetiklendi, döngüden çıkılıyor...")
                break

            with latest_lock:
                seq = latest_seq[0]
                frame = None if latest_raw[0] is None else latest_raw[0].copy()
            if frame is None or seq == last_infer_seq:
                time.sleep(0.002)
                continue
            last_infer_seq = seq

            now_process = time.time()
            if last_process_at is None:
                n = 1
            else:
                n = elapsed_frame_weight(now_process - last_process_at, fps)
            last_process_at = now_process
            frame_sayaci += n

            with infer_lock:
                results = model(
                    frame.copy(),
                    conf=tracker.dusuk_guven_esigi,
                    imgsz=IMGSZ,
                    verbose=False,
                    device=0
                )

            tespitler = []
            for r in results:
                boxes = r.boxes
                for box in boxes:
                    x1, y1, x2, y2 = box.xyxy[0]
                    x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
                    conf = float(box.conf[0])
                    tespitler.append((x1, y1, x2, y2, conf))

            takip_sonuclari = tracker.guncelle(tespitler, frame)

            ham_bolge_isci = {z: set() for z in bolge_poligonlari.keys()}
            bu_karede_tum_idler = set()
            kutular = []

            for (isci_id, x1, y1, x2, y2, conf, durum) in takip_sonuclari:
                cx, cy = ayak_noktasi(x1, y1, x2, y2)
                bu_karede_tum_idler.add(isci_id)

                isci_bolgesi = None
                for z, poli in bolge_poligonlari.items():
                    if cv2.pointPolygonTest(poli, (cx, cy), False) >= 0:
                        isci_bolgesi = z
                        ham_bolge_isci[z].add(isci_id)
                        break

                if durum == "geri_dondu":
                    renk = (0, 255, 255)
                    etiket = f"ID:{isci_id} GERI"
                    geri_donus_sayaci += 1
                elif isci_bolgesi is not None:
                    renk = bolge_renkleri[isci_bolgesi]
                    bolge_adi = bolge_adlari.get(isci_bolgesi) or default_zone_name(isci_bolgesi)
                    etiket = f"ID:{isci_id} {bolge_adi}"
                else:
                    renk = (200, 200, 200)
                    etiket = f"ID:{isci_id}"

                kalinlik = 3 if durum == "geri_dondu" else 2
                kutular.append({
                    "bbox": (x1, y1, x2, y2),
                    "renk": renk,
                    "etiket": etiket,
                    "kalinlik": kalinlik,
                    "ayak": (cx, cy),
                })

                isci_toplam_sure[isci_id] = isci_toplam_sure.get(isci_id, 0) + n
                if isci_id not in isci_ilk_gorunum:
                    isci_ilk_gorunum[isci_id] = max(1, frame_sayaci - n + 1)
                isci_son_gorunum[isci_id] = frame_sayaci

            guncellenmis_ciftler = set()
            for isci_id in bu_karede_tum_idler:
                for z in bolge_poligonlari.keys():
                    icinde = isci_id in ham_bolge_isci[z]
                    ziyaret_takip.guncelle(isci_id, z, icinde, frame_sayaci, agirlik=n)
                    guncellenmis_ciftler.add((isci_id, z))

            for key in list(ziyaret_takip.aktif_ziyaretler.keys()):
                if key not in guncellenmis_ciftler:
                    ziyaret_takip.guncelle(key[0], key[1], False, frame_sayaci, agirlik=n)

            for z in bolge_poligonlari.keys():
                ham_dolu = len(ham_bolge_isci[z]) > 0
                zone_doluluk_debouncer.guncelle(z, ham_dolu)

            kayip_cizim = []
            for iz_id, iz in tracker.aktif_izler.items():
                if not iz["gorunuyor"] and iz["kayip_kare"] <= tracker.max_kayip_kare:
                    tx, ty = tracker._tahmini_pozisyon(iz)
                    mx, my = iz["merkez"]
                    kalan_sn = round((tracker.max_kayip_kare - iz["kayip_kare"]) / fps, 1)
                    kayip_cizim.append({
                        "merkez": (mx, my),
                        "tahmin": (tx, ty),
                        "text": f"ID:{iz_id}? ({kalan_sn}s)",
                    })

            aktif_sayi = sum(1 for iz in tracker.aktif_izler.values() if iz["gorunuyor"])
            hafiza_sayi = sum(1 for iz in tracker.aktif_izler.values() if not iz["gorunuyor"])
            toplam_id = tracker.sonraki_id - 1
            bilgi_satirlari = [
                f"{name}  |  Frame: {frame_sayaci}  |  FPS: {fps:.0f}",
                f"Aktif: {aktif_sayi}  |  Hafizada: {hafiza_sayi}",
                f"Toplam ID: {toplam_id}  |  Geri Donus: {geri_donus_sayaci}",
                f"Kopru: {KOPRU_SURESI_SN}s | Hayalet: {HAYALET_ESIK_SN}s | MinKalma: {MIN_BOLGE_KALMA_SN}s",
            ]

            with overlay_lock:
                overlay_state["kutular"] = kutular
                overlay_state["kayip"] = kayip_cizim
                overlay_state["bilgi"] = bilgi_satirlari
            overlay_ready.set()

            if HARD_FRAMES_ENABLED:
                reason = classify_hard_reason(
                    tespitler,
                    tracker,
                    tracker.dusuk_guven_esigi,
                    tracker.yuksek_guven_esigi,
                    fps,
                )
                if reason:
                    hard_sink.offer(frame, tespitler, reason)
    except KeyboardInterrupt:
        print(f"\n{prefix} Kullanıcı tarafından durduruldu. Video ve veritabanı kaydediliyor...")
    finally:
        written_hard = hard_sink.close()
        if written_hard:
            print(f"{prefix} Zor kare yazıldı: {written_hard} -> {hard_sink.folder()}")
        finalize_video()
        capture_thread.join(timeout=8.0)


    ziyaret_takip.video_bitti()
    oturum_bitis = datetime.now()
    toplam_ziyaret, onaylanan_ziyaret, filtrelenen_ziyaret = ziyaret_takip.ziyaret_istatistikleri()
    filtreli_ziyaretler = ziyaret_takip.filtreli_ziyaretler()

    hayalet_idler = {
        isci_id for isci_id, kare in isci_toplam_sure.items() if kare < HAYALET_KARE
    }
    filtreli_sureler = ziyaret_takip.filtreli_isci_bolge_sureleri()
    filtreli_zone_dolu = ziyaret_takip.filtreli_zone_doluluk(
        bolge_listesi=list(bolge_poligonlari.keys()),
        toplam_frame=frame_sayaci
    )

    print(f"{prefix} Toplam ziyaret: {toplam_ziyaret} | Onaylanan: {onaylanan_ziyaret} | Filtrelenen: {filtrelenen_ziyaret}")
    if hayalet_idler:
        print(f"{prefix} {len(hayalet_idler)} hayalet ID filtrelendi: {sorted(hayalet_idler)}")

    return {
        "camera_id": camera_id,
        "camera_name": name,
        "error": None,
        "fps": fps,
        "frame_sayaci": frame_sayaci,
        "filtreli_sureler": filtreli_sureler,
        "filtreli_zone_dolu": filtreli_zone_dolu,
        "hayalet_idler": hayalet_idler,
        "isci_toplam_sure": isci_toplam_sure,
        "isci_ilk_gorunum": isci_ilk_gorunum,
        "isci_son_gorunum": isci_son_gorunum,
        "bolge_ids": list(bolge_poligonlari.keys()),
        "bolge_adlari": bolge_adlari,
        "ziyaret_stats": (toplam_ziyaret, onaylanan_ziyaret, filtrelenen_ziyaret),
        "toplam_id": tracker.sonraki_id - 1,
        "geri_donus_sayaci": geri_donus_sayaci,
        "oturum_id": oturum_id,
        "oturum_baslangic": oturum_baslangic,
        "oturum_bitis": oturum_bitis,
        "filtreli_ziyaretler": filtreli_ziyaretler,
    }


def build_report_rows(reports, su_an=None):
    su_an = su_an or datetime.now()
    zone_rows = []
    worker_rows = []
    session_rows = []
    visit_rows = []
    for report in reports:
        if not report or report.get("error"):
            continue
        camera_name = report["camera_name"]
        channel_id = str(report["camera_id"])
        fps = report["fps"] or 25.0
        frame_sayaci = report["frame_sayaci"]
        hayalet_idler = report["hayalet_idler"]
        oturum_id = report.get("oturum_id") or uuid.uuid4()
        oturum_baslangic = report.get("oturum_baslangic") or su_an
        oturum_bitis = report.get("oturum_bitis") or su_an
        ziyaret_stats = report.get("ziyaret_stats") or (0, 0, 0)
        toplam_ziyaret, onaylanan_ziyaret, filtrelenen_ziyaret = ziyaret_stats

        session_rows.append(
            (
                str(oturum_id),
                oturum_baslangic,
                oturum_bitis,
                camera_name,
                channel_id,
                float(fps),
                int(frame_sayaci or 0),
                int(toplam_ziyaret),
                int(onaylanan_ziyaret),
                int(filtrelenen_ziyaret),
                int(report.get("toplam_id") or 0),
                int(len(hayalet_idler or [])),
            )
        )

        for z in sorted(report["bolge_ids"]):
            dolu_kare = report["filtreli_zone_dolu"].get(z, 0)
            bos_kare = frame_sayaci - dolu_kare
            dolu_saniye = round(dolu_kare / fps, 1)
            bos_saniye = round(bos_kare / fps, 1)
            toplam = dolu_saniye + bos_saniye
            yuzde = round((dolu_saniye / toplam) * 100, 1) if toplam > 0 else 0
            zone_rows.append(
                (
                    su_an,
                    camera_name,
                    channel_id,
                    report_zone_name(report, z),
                    dolu_saniye,
                    bos_saniye,
                    yuzde,
                    str(oturum_id),
                    int(z),
                )
            )

        for isci_id in sorted(report["filtreli_sureler"].keys()):
            if isci_id in hayalet_idler:
                continue
            for bolge_no, kare_sayisi in sorted(report["filtreli_sureler"][isci_id].items()):
                sure_sn = round(kare_sayisi / fps, 1)
                if sure_sn > 0:
                    worker_rows.append(
                        (
                            su_an,
                            camera_name,
                            channel_id,
                            isci_id,
                            report_zone_name(report, bolge_no),
                            sure_sn,
                            str(oturum_id),
                            int(bolge_no),
                        )
                    )

        for ziyaret in report.get("filtreli_ziyaretler") or []:
            isci_id = ziyaret.get("isci_id")
            if isci_id in hayalet_idler:
                continue
            bolge_no = int(ziyaret.get("bolge_no") or 0)
            sure_sn = round((ziyaret.get("kare_sayisi") or 0) / fps, 1)
            if sure_sn <= 0:
                continue
            visit_rows.append(
                (
                    str(oturum_id),
                    camera_name,
                    channel_id,
                    isci_id,
                    bolge_no,
                    report_zone_name(report, bolge_no),
                    frame_to_datetime(oturum_baslangic, ziyaret.get("baslangic_frame") or 1, fps),
                    frame_to_datetime(oturum_baslangic, ziyaret.get("bitis_frame") or 1, fps),
                    sure_sn,
                )
            )
    return {
        "zone_rows": zone_rows,
        "worker_rows": worker_rows,
        "session_rows": session_rows,
        "visit_rows": visit_rows,
    }


def write_reports_to_db(reports):
    cfg = sql_config()
    target = f"{cfg['server']}/{cfg['database']}"
    payload = build_report_rows(reports)
    zone_rows = payload["zone_rows"]
    worker_rows = payload["worker_rows"]
    session_rows = payload["session_rows"]
    visit_rows = payload["visit_rows"]
    last_error = None

    for attempt in range(1, SQL_WRITE_RETRIES + 1):
        conn = None
        try:
            conn = sql_connection()
            cursor = conn.cursor()
            ensure_sql_schema(cursor)
            if session_rows:
                cursor.executemany(
                    """
                    INSERT INTO Oturum_Loglari
                    (OturumId, Baslangic, Bitis, KameraAdi, KanalId, Fps, FrameSayisi,
                     ToplamZiyaret, OnaylananZiyaret, FiltrelenenZiyaret, ToplamTakipId, HayaletId)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    session_rows,
                )
            if zone_rows:
                cursor.executemany(
                    """
                    INSERT INTO Bolge_Doluluk_Loglari
                    (LogTarihi, KameraAdi, KanalId, BolgeAdi, Dolu_Saniye, Bos_Saniye,
                     Doluluk_Yuzdesi, OturumId, BolgeId)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    zone_rows,
                )
            if worker_rows:
                cursor.executemany(
                    """
                    INSERT INTO Isci_Zaman_Loglari
                    (LogTarihi, KameraAdi, KanalId, Isci_ID, BolgeAdi, Sure_Saniye, OturumId, BolgeId)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    worker_rows,
                )
            if visit_rows:
                cursor.executemany(
                    """
                    INSERT INTO Ziyaret_Loglari
                    (OturumId, KameraAdi, KanalId, Isci_ID, BolgeId, BolgeAdi, Baslangic, Bitis, Sure_Saniye)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    visit_rows,
                )
            conn.commit()
            print("=" * 65)
            print(f"  RAPOR KAYIT EDİLDİ: {target}")
            print(
                f"  Oturum: {len(session_rows)} | Bölge: {len(zone_rows)} | "
                f"İşçi: {len(worker_rows)} | Ziyaret: {len(visit_rows)}"
            )
            print("=" * 65)
            return
        except Exception as exc:
            last_error = exc
            print(f"[SQL] Kayıt denemesi {attempt}/{SQL_WRITE_RETRIES} başarısız: {exc}")
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
            if attempt < SQL_WRITE_RETRIES:
                time.sleep(1.0)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    print("=" * 65)
    print(f"  RAPOR SQL SERVER'A YAZILAMADI: {target}")
    print(f"  {last_error}")
    print("=" * 65)


def print_report_summary(report):
    if not report or report.get("error"):
        return
    name = report["camera_name"]
    fps = report["fps"] or 25.0
    print(f"\n=== {name} ({report['camera_id']}) ===")
    print("--- BÖLGE DOLULUK ÖZETİ (Visit-Filtered) ---")
    for z in sorted(report["bolge_ids"]):
        dolu_kare = report["filtreli_zone_dolu"].get(z, 0)
        bos_kare = report["frame_sayaci"] - dolu_kare
        dolu_saniye = round(dolu_kare / fps, 1)
        bos_saniye = round(bos_kare / fps, 1)
        toplam = dolu_saniye + bos_saniye
        yuzde = round((dolu_saniye / toplam) * 100, 1) if toplam > 0 else 0
        print(f"  {report_zone_name(report, z)}: Dolu {dolu_saniye}sn | Boş {bos_saniye}sn | Doluluk %{yuzde}")

    print("--- İŞÇİ TOPLAM SÜRE ÖZETİ (Filtered) ---")
    hayalet_idler = report["hayalet_idler"]
    for isci_id in sorted(report["isci_toplam_sure"].keys()):
        if isci_id in hayalet_idler:
            continue
        toplam_sn = round(report["isci_toplam_sure"][isci_id] / fps, 1)
        ilk_sn = round(report["isci_ilk_gorunum"].get(isci_id, 0) / fps, 1)
        son_sn = round(report["isci_son_gorunum"].get(isci_id, 0) / fps, 1)
        bolge_dagilimi = ""
        if isci_id in report["filtreli_sureler"]:
            parcalar = [
                f"{report_zone_name(report, bn)}: {round(kare / fps, 1)}sn"
                for bn, kare in sorted(report["filtreli_sureler"][isci_id].items())
            ]
            bolge_dagilimi = " | ".join(parcalar)
        print(f"  ID:{isci_id} -> Toplam: {toplam_sn}sn | İlk: {ilk_sn}sn | Son: {son_sn}sn")
        if bolge_dagilimi:
            print(f"           {bolge_dagilimi}")

    _, _, filtrelenen_ziyaret = report["ziyaret_stats"]
    print(f"Toplam atanan ID sayısı      : {report['toplam_id']}")
    print(f"Hayalet ID filtrelendi       : {len(hayalet_idler)}")
    print(f"Kısa ziyaret filtrelendi     : {filtrelenen_ziyaret}")
    print(f"Başarılı geri tanımlama      : {report['geri_donus_sayaci']} kare")


def start_multi_camera(selected_cameras):
    global VIDEO_SAVE_ENABLED, HARD_FRAMES_ENABLED, MAX_KAYIP_SURESI_SN, KOPRU_SURESI_SN
    global HAYALET_ESIK_SN, MIN_BOLGE_KALMA_SN, CONFIDENCE, pipeline_running

    pipeline_finished.clear()
    pipeline_running = True
    VIDEO_SAVE_ENABLED = bool(CONFIG.get("VIDEO_SAVE_ENABLED", DEFAULT_CONFIG["VIDEO_SAVE_ENABLED"]))
    HARD_FRAMES_ENABLED = bool(CONFIG.get("HARD_FRAMES_ENABLED", DEFAULT_CONFIG["HARD_FRAMES_ENABLED"]))
    MAX_KAYIP_SURESI_SN = float(CONFIG.get("MAX_KAYIP_SURESI_SN", DEFAULT_CONFIG["MAX_KAYIP_SURESI_SN"]))
    KOPRU_SURESI_SN = float(CONFIG.get("KOPRU_SURESI_SN", DEFAULT_CONFIG["KOPRU_SURESI_SN"]))
    HAYALET_ESIK_SN = float(CONFIG.get("HAYALET_ESIK_SN", DEFAULT_CONFIG["HAYALET_ESIK_SN"]))
    MIN_BOLGE_KALMA_SN = float(CONFIG.get("MIN_BOLGE_KALMA_SN", DEFAULT_CONFIG["MIN_BOLGE_KALMA_SN"]))
    CONFIDENCE = float(CONFIG.get("CONFIDENCE", DEFAULT_CONFIG["CONFIDENCE"]))

    apply_rtsp_ffmpeg_options()
    model = load_yolo_model()
    if model is None:
        pipeline_running = False
        pipeline_finished.set()
        return

    infer_lock = threading.Lock()
    print("\nSistem arka planda çalışıyor. Görüntü ekrana yansıtılmayacak.")
    print(f"{len(selected_cameras)} kamera paralel işlenecek.")
    print("Çıkış: saat yanındaki P-HAS ikonuna sağ tıklayıp 'Çıkış'.")
    print("Bu siyah pencereyi kapatmayın — kapatırsanız videolar yine de kaydedilmeye çalışılır.\n")

    try:
        cfg = sql_config()
        print(f"Rapor veritabanı: {cfg['server']}/{cfg['database']}")

        reports = [None] * len(selected_cameras)

        def worker(index, camera):
            reports[index] = run_single_camera(
                camera["id"],
                camera["name"],
                camera["url"],
                camera.get("zones", {}),
                model,
                infer_lock,
            )

        threads = []
        for index, camera in enumerate(selected_cameras):
            thread = threading.Thread(target=worker, args=(index, camera), daemon=True)
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        valid_reports = [report for report in reports if report]
        write_reports_to_db(valid_reports)
        for report in valid_reports:
            print_report_summary(report)

        print("İşlem tamamlandı!")
    finally:
        pipeline_running = False
        pipeline_finished.set()
        os._exit(0)

if __name__ == "__main__":
    install_windows_console_handler()
    app = QApplication(sys.argv)
    apply_app_theme(app)
    app.setQuitOnLastWindowClosed(False)

    login = LoginWindow()
    if login.exec() != QDialog.DialogCode.Accepted or not login.is_authenticated:
        sys.exit(0)

    try:
        subprocess.run(["nvidia-smi"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        show_warning(
            None,
            "Uyarı",
            "Uyarı: Harici bir ekran kartı bulunamadı. Program sadece işlemci (CPU) ile çalışacağı için performans düşüklüğü yaşanabilir"
        )

    tray_icon = QSystemTrayIcon(QIcon(asset_path("logo.png")), app)
    tray_icon.setToolTip("P-HAS Personel Takip Sistemi")

    menu = QMenu()
    ayarlar_action = menu.addAction("Ayarlar")
    raporlar_action = menu.addAction("Raporlar")
    cikis_action = menu.addAction("Çıkış")

    settings_gui = SettingsGUI()

    def on_ayarlar():
        settings_gui.show()
        settings_gui.raise_()
        settings_gui.activateWindow()

    def on_raporlar():
        open_report_dialog(settings_gui)

    def on_cikis():
        trigger_shutdown()

    ayarlar_action.triggered.connect(on_ayarlar)
    raporlar_action.triggered.connect(on_raporlar)
    cikis_action.triggered.connect(on_cikis)

    tray_icon.setContextMenu(menu)
    tray_icon.show()
    settings_gui.show()

    app.exec()

