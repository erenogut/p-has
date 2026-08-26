import re
from datetime import datetime
from pathlib import Path

import cv2

MIN_FRAMES = 8
MAX_FRAMES = 2000
DEFAULT_FRAMES = 300


def folder_slug(text):
    text = (text or "kamera").strip()
    text = re.sub(r'[<>:"/\\|?*]+', "_", text)
    text = re.sub(r"\s+", "_", text)
    text = text.strip("._")
    return (text[:48] or "kamera")


def default_output_dir(app_dir, camera_name, stamp=None):
    stamp = stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(app_dir) / "datasets" / "etiket" / f"{folder_slug(camera_name)}_{stamp}"


def sample_indices(frame_count, want):
    frame_count = int(frame_count or 0)
    if frame_count <= 0:
        return []
    want = max(1, min(int(want), frame_count))
    if want == 1:
        return [0]
    raw = [int(round(i * (frame_count - 1) / (want - 1))) for i in range(want)]
    out = []
    seen = set()
    for index in raw:
        index = max(0, min(index, frame_count - 1))
        if index not in seen:
            seen.add(index)
            out.append(index)
    return out


def probe_video(video_path):
    path = Path(video_path)
    if not path.is_file():
        raise ValueError("Video dosyası bulunamadı.")
    cap = cv2.VideoCapture(str(path))
    try:
        if cap is None or not cap.isOpened():
            raise ValueError("Video açılamadı. Dosya bozuk veya kodek eksik olabilir.")
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        duration = (frame_count / fps) if fps > 0.1 and frame_count > 0 else 0.0
        return {
            "path": str(path),
            "fps": fps,
            "frame_count": frame_count,
            "width": width,
            "height": height,
            "duration_sec": duration,
        }
    finally:
        if cap is not None:
            cap.release()


def _write_frame(dest_dir, number, frame):
    name = dest_dir / f"frame_{number:05d}.jpg"
    ok = cv2.imwrite(str(name), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    if not ok:
        raise ValueError(f"{name.name} yazılamadı.")
    return name


def _cancelled(should_cancel):
    return bool(should_cancel and should_cancel())


def extract_frames(video_path, dest_dir, count, progress=None, should_cancel=None):
    count = max(1, min(int(count), MAX_FRAMES))
    info = probe_video(video_path)
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(video_path))
    saved = 0
    scanned = 0
    try:
        if cap is None or not cap.isOpened():
            raise ValueError("Video açılamadı.")
        frame_count = info["frame_count"]
        targets = sample_indices(frame_count, count) if frame_count >= 2 else []
        if targets:
            wanted = set(targets)
            last = max(targets)
            total = last + 1
            while scanned <= last:
                if _cancelled(should_cancel):
                    break
                ok, frame = cap.read()
                if not ok or frame is None:
                    break
                if scanned in wanted:
                    saved += 1
                    _write_frame(dest, saved, frame)
                scanned += 1
                if progress and (scanned == 1 or scanned % 25 == 0 or scanned in wanted or scanned > last):
                    progress(scanned, total, saved)
        else:
            step = 30
            total = count
            while saved < count:
                if _cancelled(should_cancel):
                    break
                ok, frame = cap.read()
                if not ok or frame is None:
                    break
                if scanned % step == 0:
                    saved += 1
                    _write_frame(dest, saved, frame)
                    if progress:
                        progress(saved, total, saved)
                scanned += 1
    finally:
        if cap is not None:
            cap.release()

    if saved <= 0:
        if _cancelled(should_cancel):
            raise ValueError("Kare çıkarma iptal edildi.")
        raise ValueError("Videodan kare alınamadı. Başka bir kayıt deneyin.")
    if progress:
        progress(scanned or saved, scanned or saved, saved)
    info["saved"] = saved
    info["requested"] = count
    info["folder"] = str(dest)
    info["cancelled"] = _cancelled(should_cancel)
    return info
