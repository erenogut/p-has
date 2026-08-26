from pathlib import Path

CLASS_ID = 0
CLASS_NAME = "insan"


def _clamp(value, low, high):
    return max(low, min(high, value))


def normalize_box(x1, y1, x2, y2, width, height):
    if width <= 0 or height <= 0:
        return None
    left = _clamp(min(x1, x2), 0.0, float(width))
    right = _clamp(max(x1, x2), 0.0, float(width))
    top = _clamp(min(y1, y2), 0.0, float(height))
    bottom = _clamp(max(y1, y2), 0.0, float(height))
    box_w = right - left
    box_h = bottom - top
    if box_w < 2 or box_h < 2:
        return None
    return (
        (left + right) / 2.0 / width,
        (top + bottom) / 2.0 / height,
        box_w / width,
        box_h / height,
    )


def denormalize_box(xc, yc, bw, bh, width, height):
    box_w = float(bw) * width
    box_h = float(bh) * height
    cx = float(xc) * width
    cy = float(yc) * height
    x1 = _clamp(cx - box_w / 2.0, 0.0, float(width))
    y1 = _clamp(cy - box_h / 2.0, 0.0, float(height))
    x2 = _clamp(cx + box_w / 2.0, 0.0, float(width))
    y2 = _clamp(cy + box_h / 2.0, 0.0, float(height))
    if x2 - x1 < 2 or y2 - y1 < 2:
        return None
    return (x1, y1, x2, y2)


def boxes_to_yolo(boxes, width, height):
    lines = []
    for box in boxes:
        if len(box) < 4:
            continue
        norm = normalize_box(box[0], box[1], box[2], box[3], width, height)
        if norm is None:
            continue
        xc, yc, bw, bh = norm
        lines.append(f"{CLASS_ID} {xc:.6f} {yc:.6f} {bw:.6f} {bh:.6f}")
    return lines


def yolo_to_boxes(text, width, height):
    boxes = []
    for raw in (text or "").splitlines():
        parts = raw.strip().split()
        if len(parts) < 5:
            continue
        try:
            class_id = int(float(parts[0]))
            xc, yc, bw, bh = (float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4]))
        except ValueError:
            continue
        if class_id != CLASS_ID:
            continue
        box = denormalize_box(xc, yc, bw, bh, width, height)
        if box is not None:
            boxes.append(box)
    return boxes


def label_path_for(image_path):
    return Path(image_path).with_suffix(".txt")


def load_boxes(image_path, width, height):
    path = label_path_for(image_path)
    if not path.is_file():
        return []
    return yolo_to_boxes(path.read_text(encoding="utf-8", errors="replace"), width, height)


def save_boxes(image_path, boxes, width, height):
    path = label_path_for(image_path)
    lines = boxes_to_yolo(boxes, width, height)
    if not lines:
        if path.is_file():
            path.unlink()
        return False
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def has_label(image_path):
    path = label_path_for(image_path)
    if not path.is_file():
        return False
    return any(line.strip() for line in path.read_text(encoding="utf-8", errors="replace").splitlines())
