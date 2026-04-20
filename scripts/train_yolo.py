"""YOLO training script — scaffold for Dofus resource detection model.

Prerequisites:
    pip install "ultralytics>=8.3.0"

Dataset structure expected at data/yolo_dataset/:
    data/yolo_dataset/
    ├── data.yaml           # dataset config (see below)
    ├── images/
    │   ├── train/          # training screenshots (.png / .jpg)
    │   └── val/            # validation screenshots
    └── labels/
        ├── train/          # YOLO format .txt annotations
        └── val/

data.yaml format:
    path: ../data/yolo_dataset   # relative to this script or absolute
    train: images/train
    val:   images/val
    nc: 7                        # number of classes
    names:
      0: tree
      1: wheat
      2: ore
      3: fish
      4: monster
      5: npc
      6: resource_generic

Annotation workflow (choose one):
    A) LabelImg (free, local):
       pip install labelImg
       labelImg data/yolo_dataset/images/train
       Select "YOLO" format in the top-left dropdown.
       Draw bounding boxes around resources.

    B) Roboflow (web, easier):
       Upload screenshots to roboflow.com
       Annotate in browser, export as "YOLOv8" format.
       Download and unzip into data/yolo_dataset/.

Classes to annotate:
    tree             — frêne, châtaignier, chêne (whole tree silhouette)
    wheat            — blé, orge, houblon (crop patch)
    ore              — minerai fer, cuivre, etc. (rocky protrusion)
    fish             — spot pêche (surface shimmer)
    monster          — any hostile mob group (coloured nameplate area)
    npc              — NPC character (interact arrow above head)
    resource_generic — anything interactable not in above classes

Tips for annotation quality:
    - Aim for 200+ annotated images per class minimum.
    - Capture at various times of day (Dofus has lighting cycles).
    - Include partially occluded objects (other characters in front).
    - Balance classes: similar counts per class reduces bias.
    - Screenshot at 1920x1080 — model can downscale, upscaling hurts accuracy.

Active learning (improving the model over time):
    The bot logs uncertain detections (confidence between threshold and threshold+0.15)
    to data/uncertain_crops/. Review these periodically:
      1. Annotate the uncertain crops that are mis-labelled.
      2. Add them to data/yolo_dataset/images/train + labels/train.
      3. Re-run this script.
    This is the fastest way to improve recall on edge cases.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train YOLOv8n on Dofus screenshots")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("data/yolo_dataset/data.yaml"),
        help="Path to data.yaml (default: data/yolo_dataset/data.yaml)",
    )
    parser.add_argument(
        "--epochs",
        type=int,
        default=50,
        help="Training epochs (default: 50 — increase to 100+ for final model)",
    )
    parser.add_argument(
        "--imgsz",
        type=int,
        default=640,
        help="Input image size (default: 640)",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=16,
        help="Batch size (default: 16 — reduce to 8 if OOM on CPU)",
    )
    parser.add_argument(
        "--device",
        type=str,
        default="cpu",
        help="Device: 'cpu', 'cuda:0', 'mps' (default: cpu)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/models"),
        help="Directory for trained model (default: data/models/)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from last checkpoint",
    )
    return parser.parse_args()


def check_dataset(data_yaml: Path) -> None:
    if not data_yaml.exists():
        print(f"ERROR: Dataset not found at {data_yaml}")
        print("Run annotation first. See script docstring for instructions.")
        sys.exit(1)


def train(args: argparse.Namespace) -> Path:
    """Run YOLOv8n training via ultralytics API."""
    try:
        from ultralytics import YOLO  # noqa: PLC0415
    except ImportError:
        print("ERROR: ultralytics not installed.")
        print("  pip install 'ultralytics>=8.3.0'")
        sys.exit(1)

    check_dataset(args.dataset)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading YOLOv8n base model (will download ~6 MB on first run)...")
    model = YOLO("yolov8n.pt")

    print(f"Starting training: {args.epochs} epochs, imgsz={args.imgsz}, device={args.device}")
    results = model.train(
        data=str(args.dataset.resolve()),
        model="yolov8n.pt",
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        project=str(args.output_dir),
        name="dofus_yolo",
        resume=args.resume,
        # Augmentation — useful for small datasets
        flipud=0.0,       # no vertical flip (Dofus isometric perspective)
        fliplr=0.5,       # horizontal flip OK
        mosaic=1.0,       # mosaic augmentation
        hsv_h=0.015,      # hue jitter (lighting variation)
        hsv_s=0.7,
        hsv_v=0.4,
    )

    # Copy best weights to a stable path
    best_weights: Path = Path(results.save_dir) / "weights" / "best.pt"
    output_model = args.output_dir / "dofus_yolo.pt"
    if best_weights.exists():
        import shutil

        shutil.copy2(best_weights, output_model)
        print(f"\nModel saved to: {output_model.resolve()}")
        print(f"Set YOLO_MODEL_PATH={output_model.resolve()} in your .env")
    else:
        print(f"WARNING: best.pt not found at {best_weights} — check training logs")

    return output_model


def validate(model_path: Path, data_yaml: Path) -> None:
    """Quick validation pass on the val split."""
    try:
        from ultralytics import YOLO  # noqa: PLC0415
    except ImportError:
        return

    print(f"\nValidating model: {model_path}")
    model = YOLO(str(model_path))
    metrics = model.val(data=str(data_yaml.resolve()))
    print(f"mAP50:      {metrics.box.map50:.3f}")
    print(f"mAP50-95:   {metrics.box.map:.3f}")
    print(f"Precision:  {metrics.box.mp:.3f}")
    print(f"Recall:     {metrics.box.mr:.3f}")


if __name__ == "__main__":
    args = parse_args()
    output_model = train(args)
    if output_model.exists():
        validate(output_model, args.dataset)
