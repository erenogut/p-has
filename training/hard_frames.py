import queue
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from training.extract_frames import folder_slug
from training.yolo_label import save_boxes

HARD_DIR_NAME = "zor"
DEFAULT_DAILY_CAP = 200
MIN_SAVE_INTERVAL_SEC = 1.5
MAX_QUEUE = 8
LOST_MIN_SEC = 0.3
LOST_MAX_SEC = 3.0
SIMILAR_DIFF = 6.0


def hard_camera_dir(app_dir, camera_name):
    return Path(app_dir) / "datasets" / HARD_DIR_NAME / folder_slug(camera_name)


def hard_day_dir(app_dir, camera_name, day=None):
    day = day or datetime.now().strftime("%Y%m%d")
    return hard_camera_dir(app_dir, camera_name) / day


def find_latest_hard_dir(app_dir, camera_name):
    today = hard_day_dir(app_dir, camera_name)
    if _has_images(today):
        return today
    root = hard_camera_dir(app_dir, camera_name)
    if not root.is_dir():
        return None
    days = sorted((path for path in root.iterdir() if path.is_dir()), reverse=True)
    for path in days:
        if _has_images(path):
            return path
    return None


def _has_images(folder):
    if not folder.is_dir():
        return False
    return any(path.suffix.lower() in (".jpg", ".jpeg", ".png") for path in folder.iterdir())


def classify_hard_reason(detections, tracker, low_conf, high_conf, fps):
    for det in detections or []:
        if len(det) < 5:
            continue
        try:
            conf = float(det[4])
        except (TypeError, ValueError):
            continue
        if low_conf <= conf < high_conf:
            return "dusukconf"

    if tracker is None:
        return None
    traces = getattr(tracker, "aktif_izler", None) or {}
    rate = max(1.0, float(fps or 1.0))
    for iz in traces.values():
        if iz.get("gorunuyor"):
            continue
        lost_sec = float(iz.get("kayip_kare", 0)) / rate
        if LOST_MIN_SEC <= lost_sec <= LOST_MAX_SEC:
            return "kayip"
    return None


def draft_boxes(detections):
    boxes = []
    for det in detections or []:
        if len(det) < 4:
            continue
        try:
            boxes.append((float(det[0]), float(det[1]), float(det[2]), float(det[3])))
        except (TypeError, ValueError):
            continue
    return boxes


def _tiny_gray(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    return cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)


class HardFrameSink:
    def __init__(
        self,
        app_dir,
        camera_name,
        enabled=True,
        daily_cap=DEFAULT_DAILY_CAP,
        min_interval=MIN_SAVE_INTERVAL_SEC,
    ):
        self.app_dir = Path(app_dir)
        self.camera_name = camera_name or "kamera"
        self.enabled = bool(enabled)
        self.daily_cap = int(daily_cap)
        self.min_interval = float(min_interval)
        self._queue = queue.Queue(maxsize=MAX_QUEUE)
        self._stop = threading.Event()
        self._thread = None
        self._last_save = 0.0
        self._last_reason_at = {}
        self._saved_today = 0
        self._written = 0
        self._day = ""
        self._last_tiny = None
        if self.enabled:
            self._refresh_day()
            self._thread = threading.Thread(
                target=self._loop,
                name=f"hard-{folder_slug(self.camera_name)}",
                daemon=True,
            )
            self._thread.start()

    def folder(self):
        return hard_day_dir(self.app_dir, self.camera_name, self._day or None)

    def _refresh_day(self):
        today = datetime.now().strftime("%Y%m%d")
        if today == self._day:
            return
        self._day = today
        dest = self.folder()
        dest.mkdir(parents=True, exist_ok=True)
        self._saved_today = sum(1 for path in dest.glob("*.jpg"))

    def offer(self, frame, detections, reason):
        if not self.enabled or frame is None or not reason:
            return False
        now = time.time()
        if now - self._last_save < self.min_interval:
            return False
        if now - self._last_reason_at.get(reason, 0.0) < self.min_interval:
            return False
        self._refresh_day()
        if self._saved_today >= self.daily_cap:
            return False
        try:
            tiny = _tiny_gray(frame)
        except Exception:
            tiny = None
        if tiny is not None and self._last_tiny is not None:
            diff = float(np.mean(cv2.absdiff(tiny, self._last_tiny)))
            if diff < SIMILAR_DIFF:
                return False
        try:
            self._queue.put_nowait((frame.copy(), list(detections or []), reason, now))
        except queue.Full:
            return False
        self._last_save = now
        self._last_reason_at[reason] = now
        self._last_tiny = tiny
        self._saved_today += 1
        return True

    def _loop(self):
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if item is None:
                break
            try:
                self._write(*item)
            except Exception:
                pass

    def _write(self, frame, detections, reason, when):
        dest = self.folder()
        dest.mkdir(parents=True, exist_ok=True)
        stamp = datetime.fromtimestamp(when).strftime("%H%M%S")
        path = dest / f"{stamp}_{reason}.jpg"
        index = 2
        while path.exists():
            path = dest / f"{stamp}_{reason}_{index}.jpg"
            index += 1
        ok = cv2.imwrite(str(path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        if not ok:
            return
        height, width = frame.shape[:2]
        boxes = draft_boxes(detections)
        if boxes:
            save_boxes(path, boxes, width, height)
        self._written += 1

    def close(self):
        if not self.enabled:
            return self._written
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        return self._written
