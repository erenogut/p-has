import os
import random
import shutil
from pathlib import Path

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
REQUIRED_CLASS = "insan"
FORBIDDEN_CLASSES = {
    "person",
    "human",
    "people",
    "pedestrian",
    "man",
    "woman",
    "i̇nsan",
    "ınsan",
}


def _is_image(path):
    return path.is_file() and path.suffix.lower() in IMAGE_EXTS


def _read_yaml(path):
    try:
        import yaml
    except ImportError:
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _class_names_from_yaml(data):
    names = data.get("names")
    if isinstance(names, dict):
        return [str(names[key]).strip() for key in sorted(names, key=lambda k: int(k) if str(k).isdigit() else str(k))]
    if isinstance(names, list):
        return [str(item).strip() for item in names]
    return []


def _norm(name):
    return (name or "").strip().lower().replace("ı", "i").replace("İ", "i")


def _find_image_roots(folder):
    folder = Path(folder)
    candidates = [
        folder / "images" / "train",
        folder / "train" / "images",
        folder / "images",
        folder,
    ]
    roots = []
    seen = set()
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        key = str(candidate.resolve())
        if key in seen:
            continue
        if any(_is_image(path) for path in candidate.rglob("*") if path.is_file()):
            seen.add(key)
            roots.append(candidate)
            break
    val_roots = []
    for candidate in (folder / "images" / "val", folder / "valid" / "images", folder / "val" / "images"):
        if candidate.is_dir() and any(_is_image(path) for path in candidate.rglob("*") if path.is_file()):
            val_roots.append(candidate)
            break
    return roots, val_roots


def _label_root_for(image_root):
    name = image_root.name.lower()
    parent = image_root.parent
    if name == "images":
        sibling = parent / "labels"
        if sibling.is_dir():
            return sibling
    if parent.name.lower() == "images":
        sibling = parent.parent / "labels" / image_root.name
        if sibling.is_dir():
            return sibling
    if name == "train" or name == "val" or name == "valid":
        sibling = parent.parent / "labels" / ("val" if name == "valid" else name)
        if sibling.is_dir():
            return sibling
    return None


def _label_for_image(image_path, image_root, label_root):
    if label_root is not None:
        try:
            rel = image_path.relative_to(image_root)
            candidate = (label_root / rel).with_suffix(".txt")
            if candidate.is_file():
                return candidate
        except ValueError:
            pass
    same_dir = image_path.with_suffix(".txt")
    if same_dir.is_file():
        return same_dir
    return None


def _iter_images(image_root):
    files = [path for path in image_root.rglob("*") if _is_image(path)]
    files.sort()
    return files


def _label_class_ids(label_path):
    ids = []
    text = Path(label_path).read_text(encoding="utf-8", errors="replace")
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        first = line.split()[0]
        try:
            ids.append(int(float(first)))
        except ValueError:
            ids.append(None)
    return ids


def inspect_dataset(folder):
    folder = Path(folder)
    result = {
        "ok": False,
        "folder": str(folder),
        "image_count": 0,
        "labeled_count": 0,
        "missing_labels": 0,
        "class_names": [],
        "errors": [],
        "warnings": [],
        "pairs": [],
    }
    if not folder.is_dir():
        result["errors"].append("Seçilen yol bir klasör değil. Fotoğrafların durduğu klasörü seçin.")
        return result

    yaml_names = []
    yaml_path = folder / "data.yaml"
    if yaml_path.is_file():
        data = _read_yaml(yaml_path)
        if data:
            yaml_names = _class_names_from_yaml(data)
            result["class_names"] = yaml_names
            bad = [name for name in yaml_names if _norm(name) != REQUIRED_CLASS]
            if bad:
                result["errors"].append(
                    "Klasördeki sınıf adı uygun değil: "
                    + ", ".join(bad)
                    + f". Bu programda sınıf adı tam olarak «{REQUIRED_CLASS}» olmalı. "
                    "person, human veya İnsan yazmayın."
                )
                return result

    image_roots, val_roots = _find_image_roots(folder)
    if not image_roots:
        result["errors"].append(
            "Bu klasörde fotoğraf bulunamadı. jpg/png dosyalarını klasörün içine "
            "veya images / train/images altına koyun."
        )
        return result

    pairs = []
    missing = 0
    bad_ids = set()
    for image_root in image_roots + val_roots:
        label_root = _label_root_for(image_root)
        for image_path in _iter_images(image_root):
            label_path = _label_for_image(image_path, image_root, label_root)
            if label_path is None:
                missing += 1
                continue
            ids = _label_class_ids(label_path)
            if any(item is None for item in ids):
                result["errors"].append(
                    f"{label_path.name} dosyası okunamadı. Etiket programından YOLO .txt olarak dışa aktarın."
                )
                return result
            extra = [item for item in ids if item not in (0,)]
            if extra:
                bad_ids.update(extra)
                continue
            pairs.append((image_path, label_path))

    result["image_count"] = len(pairs) + missing
    result["labeled_count"] = len(pairs)
    result["missing_labels"] = missing
    result["pairs"] = [(str(img), str(lbl)) for img, lbl in pairs]

    if bad_ids:
        result["errors"].append(
            "Etiketlerde «insan» dışında sınıf numarası var. "
            f"Sadece sınıf 0 / {REQUIRED_CLASS} olmalı."
        )
        return result

    if yaml_names and any(_norm(name) in FORBIDDEN_CLASSES for name in yaml_names):
        result["errors"].append(
            f"Sınıf adı «{REQUIRED_CLASS}» olmalı. person veya human kullanmayın."
        )
        return result

    if not pairs:
        result["errors"].append(
            "Kutulu (etiketli) fotoğraf yok. Her fotoğrafın yanında aynı isimli .txt "
            "olmalı, örneğin kapı.jpg ve kapı.txt."
        )
        return result

    if missing:
        result["warnings"].append(
            f"{missing} fotoğrafın etiketi yok; bunlar eğitime alınmayacak. "
            "İsterseniz aynı isimli boş .txt de bırakabilirsiniz."
        )
    if len(pairs) < 8:
        result["errors"].append(
            f"Sadece {len(pairs)} etiketli fotoğraf var. En az 8 gerekli; 80–200 çeşit kare önerilir."
        )
        return result
    if len(pairs) < 80:
        result["warnings"].append(
            f"{len(pairs)} etiketli fotoğraf var. Çalışır ama 80–200 çeşit kare daha iyi sonuç verir."
        )

    if not result["class_names"]:
        result["class_names"] = [REQUIRED_CLASS]
    result["ok"] = True
    return result


def prepare_dataset(folder, dest_dir, seed=42):
    info = inspect_dataset(folder)
    if not info["ok"]:
        raise ValueError(" ".join(info["errors"]))

    dest = Path(dest_dir)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    train_img = dest / "images" / "train"
    train_lbl = dest / "labels" / "train"
    val_img = dest / "images" / "val"
    val_lbl = dest / "labels" / "val"
    for path in (train_img, train_lbl, val_img, val_lbl):
        path.mkdir(parents=True, exist_ok=True)

    pairs = [(Path(img), Path(lbl)) for img, lbl in info["pairs"]]
    random.Random(seed).shuffle(pairs)
    val_count = max(1, min(len(pairs) // 10, 40))
    if len(pairs) - val_count < 4:
        val_count = 1
    val_pairs = pairs[:val_count]
    train_pairs = pairs[val_count:] or pairs

    def _copy(pairs_list, img_dir, lbl_dir):
        for index, (image_path, label_path) in enumerate(pairs_list, start=1):
            suffix = image_path.suffix.lower()
            name = f"{index:05d}{suffix}"
            shutil.copy2(image_path, img_dir / name)
            shutil.copy2(label_path, lbl_dir / f"{index:05d}.txt")

    _copy(train_pairs, train_img, train_lbl)
    _copy(val_pairs, val_img, val_lbl)

    yaml_path = dest / "data.yaml"
    yaml_path.write_text(
        "\n".join(
            [
                f"path: {dest.resolve().as_posix()}",
                "train: images/train",
                "val: images/val",
                "nc: 1",
                "names:",
                f"  0: {REQUIRED_CLASS}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    info["yaml_path"] = str(yaml_path)
    info["train_count"] = len(train_pairs)
    info["val_count"] = len(val_pairs)
    return info
