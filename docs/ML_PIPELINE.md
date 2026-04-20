# ML Pipeline — YOLO detection for Dofus resources

**Date** : 2026-04-17
**Model** : YOLOv8n (ultralytics)
**Status** : scaffold — model not yet trained

---

## 1. Why YOLOv8n

| Model | Size | CPU speed (640px, i7) | mAP50 (COCO) |
|---|---|---|---|
| YOLOv8n | 6 MB | ~25–35 ms/frame | 37.3 |
| YOLOv8s | 22 MB | ~55 ms/frame | 44.9 |
| YOLOv8m | 52 MB | ~115 ms/frame | 50.2 |

YOLOv8n is the right trade-off for CPU-only inference on a game bot:
- Fast enough for 10 Hz tick rate with headroom.
- Small enough to distribute alongside the bot.
- Accurate enough for large, visually distinct Dofus resources.

---

## 2. Classes

| ID | Label | Examples |
|---|---|---|
| 0 | tree | Frêne, Châtaignier, Chêne, Orme |
| 1 | wheat | Blé, Orge, Houblon, Lin |
| 2 | ore | Fer, Cuivre, Manganèse |
| 3 | fish | Spot pêche (surface shimmer) |
| 4 | monster | Any hostile mob group |
| 5 | npc | Named NPCs with interact arrow |
| 6 | resource_generic | Interactables not in above |

---

## 3. Complete workflow

```
Screenshots  ──▶  Annotation  ──▶  Train  ──▶  Validate  ──▶  Deploy
    │                  │               │             │              │
  mss grab        LabelImg or       scripts/     mAP50,         .env
  debug mode      Roboflow web      train_yolo   Precision,    YOLO_MODEL_PATH
  ~200 img/class                    .py          Recall
```

### Step 1 — Collect screenshots

Run the bot in debug mode with `LOG_LEVEL=DEBUG`. Every frame captured is
saved to `screenshots/` (if enabled). Aim for:
- 200+ unique screenshots per class minimum.
- Various maps, lighting conditions, UI states.
- Both busy maps (many players) and empty ones.

### Step 2 — Annotate

**Option A — LabelImg (local, free)**

```bash
pip install labelImg
labelImg data/yolo_dataset/images/train
```

Select YOLO format (top-left dropdown). Draw boxes. Labels auto-saved as .txt.

**Option B — Roboflow (web, faster)**

1. Create a free account at roboflow.com.
2. Upload your screenshots.
3. Annotate in browser (smart polygon, auto-label suggestions).
4. Export → YOLOv8 format → Download ZIP.
5. Extract to `data/yolo_dataset/`.

### Step 3 — Train

```bash
python scripts/train_yolo.py --epochs 50 --device cpu
```

Output: `data/models/dofus_yolo.pt`

For GPU (NVIDIA):
```bash
python scripts/train_yolo.py --epochs 100 --device cuda:0
```

### Step 4 — Deploy

Add to `.env`:
```
YOLO_MODEL_PATH=./data/models/dofus_yolo.pt
YOLO_CONFIDENCE_THRESHOLD=0.5
```

The bot auto-detects the model on startup. No code changes needed.

---

## 4. Graceful fallback

```
YoloDetector.is_available() == False
        │
        ├── YOLO_MODEL_PATH not set → skip silently
        ├── ultralytics not installed → skip silently
        └── model file missing → log warning, skip

Fallback order:
  YOLO (if available) → ColorShape + OCR tooltip → TemplateMatching
```

The bot always runs. YOLO only adds accuracy on top of the base detectors.

---

## 5. Active learning — iterative improvement

The bot logs "uncertain" detections when:
    `threshold <= confidence < threshold + 0.15`

These are saved to `data/uncertain_crops/` with timestamp and predicted label.

**Monthly improvement cycle:**
1. Review `data/uncertain_crops/` — ~20 min.
2. Correct mis-labelled crops, add to training set.
3. Re-run `train_yolo.py --resume` (starts from last checkpoint).
4. Compare new mAP50 vs previous.

After 3–4 cycles, mAP50 typically plateaus at 85–92% for Dofus resources.

---

## 6. Known limitations

- **Night/dark maps** : low contrast reduces recall to ~60% without HSV augmentation.
- **Minimap overlay** : minimap corner can trigger false positives; mask it before inference.
- **Server-specific skins** : if the server uses custom resource skins, re-annotation required.
- **CPU latency** : 25–35 ms per frame means ~30 fps max throughput. At 10 Hz tick rate, this is not a bottleneck.
- **Model size on disk** : 6 MB — acceptable to bundle, but do not commit to git (add to .gitignore).
