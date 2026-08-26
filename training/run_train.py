import argparse
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--weights", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--name", default="fine-tune")
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--export-engine", action="store_true")
    args = parser.parse_args()

    from ultralytics import YOLO

    weights = Path(args.weights)
    if not weights.is_file():
        print("PHAS_ERROR Mevcut model dosyası (best.pt) bulunamadı.", flush=True)
        return 2

    print("PHAS_STATUS Model öğrenmeye başlıyor. Bu işlem 30-90 dakika sürebilir.", flush=True)
    model = YOLO(str(weights))

    def on_epoch_end(trainer):
        current = int(getattr(trainer, "epoch", 0)) + 1
        total = int(getattr(trainer, "epochs", args.epochs) or args.epochs)
        print(f"PHAS_EPOCH {current}/{total}", flush=True)

    model.add_callback("on_train_epoch_end", on_epoch_end)
    model.train(
        data=str(Path(args.data)),
        epochs=args.epochs,
        imgsz=args.imgsz,
        lr0=0.001,
        lrf=0.01,
        patience=10,
        batch=-1,
        device=0,
        project=str(Path(args.project)),
        name=args.name,
        exist_ok=True,
        verbose=True,
    )

    best_path = Path(args.project) / args.name / "weights" / "best.pt"
    if not best_path.is_file():
        print("PHAS_ERROR Eğitim bitti ama yeni model dosyası oluşmadı.", flush=True)
        return 3
    print(f"PHAS_WEIGHTS {best_path}", flush=True)

    if args.export_engine:
        print("PHAS_STATUS Hızlı model (best.engine) üretiliyor. 5-20 dakika sürebilir. Pencereyi kapatmayın.", flush=True)
        try:
            exported = YOLO(str(best_path)).export(format="engine", device=0)
            export_path = Path(str(exported))
            if not export_path.is_file():
                fallback = best_path.with_suffix(".engine")
                if fallback.is_file():
                    export_path = fallback
            if not export_path.is_file():
                print("PHAS_ENGINE_FAIL Hızlı model dosyası yazılamadı.", flush=True)
                return 0
            print(f"PHAS_ENGINE {export_path}", flush=True)
        except Exception as exc:
            print(f"PHAS_ENGINE_FAIL {exc}", flush=True)
            return 0

    print("PHAS_DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
