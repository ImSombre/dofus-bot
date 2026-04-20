"""Optional YOLO detector — plug-and-play with VisionDetector Protocol.

Dependency: ultralytics (optional).
    pip install "ultralytics>=8.3.0"

If ultralytics is not installed OR yolo_model_path is not set in settings,
YoloDetector.is_available() returns False and the bot runs without it.

Lazy import strategy: ultralytics is never imported at module load time.
Import happens only inside methods that need inference. This keeps startup
time short and avoids ImportError crashes for users who haven't installed it.

Usage:
    detector = YoloDetector(model_path=settings.yolo_model_path,
                            confidence_threshold=settings.yolo_confidence_threshold)
    if detector.is_available():
        results = detector.detect(frame)

Classes recognised by the Dofus model (once trained):
    0: tree
    1: wheat
    2: ore
    3: fish
    4: monster
    5: npc
    6: resource_generic
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from loguru import logger

if TYPE_CHECKING:
    from src.models.detection import DetectedObject


# YOLO class id → human label mapping (must match data.yaml used at training time)
YOLO_CLASS_LABELS: dict[int, str] = {
    0: "tree",
    1: "wheat",
    2: "ore",
    3: "fish",
    4: "monster",
    5: "npc",
    6: "resource_generic",
}


class YoloDetector:
    """YOLOv8 inference wrapper.

    Implements VisionDetector Protocol — compatible with MssVisionService.yolo slot.

    Notes:
        - CPU inference by default; set device='cuda:0' for GPU if available.
        - YOLOv8n (~6 MB) targets ~30 fps on CPU at 640x640 input.
        - Model path is validated lazily on first call to detect().
    """

    def __init__(
        self,
        model_path: Path | str | None = None,
        confidence_threshold: float = 0.5,
        device: str = "cpu",
        imgsz: int = 640,
    ) -> None:
        self._model_path = Path(model_path) if model_path else None
        self._confidence_threshold = confidence_threshold
        self._device = device
        self._imgsz = imgsz
        self._model: Any | None = None  # ultralytics YOLO instance
        self._load_attempted = False

    # ---------- VisionDetector Protocol ----------

    def is_available(self) -> bool:
        """Return True only if ultralytics is installed AND model file exists."""
        if self._model_path is None:
            return False
        if not self._model_path.exists():
            return False
        try:
            import importlib.util  # noqa: PLC0415

            return importlib.util.find_spec("ultralytics") is not None
        except Exception:
            return False

    def detect(self, frame: np.ndarray) -> list["DetectedObject"]:
        """Run YOLOv8 inference on a BGR numpy frame.

        Returns an empty list (gracefully) if the model is unavailable.
        """
        if not self.is_available():
            return []

        model = self._load_model()
        if model is None:
            return []

        return self._run_inference(model, frame)

    # ---------- internals ----------

    def _load_model(self) -> Any | None:
        """Lazy-load the YOLO model. Cached after first successful load."""
        if self._model is not None:
            return self._model
        if self._load_attempted:
            return None
        self._load_attempted = True

        try:
            from ultralytics import YOLO  # noqa: PLC0415 — intentional lazy import

            logger.info("Loading YOLO model from {}", self._model_path)
            self._model = YOLO(str(self._model_path))
            logger.info("YOLO model loaded successfully")
            return self._model
        except ImportError:
            logger.warning("ultralytics not installed — YOLO detector disabled")
            return None
        except Exception as exc:
            logger.error("Failed to load YOLO model: {}", exc)
            return None

    def _run_inference(self, model: Any, frame: np.ndarray) -> list["DetectedObject"]:
        """Execute inference and convert ultralytics Results → DetectedObject list."""
        from src.models.detection import DetectedObject, DetectionConfidence, Region

        try:
            results = model.predict(
                source=frame,
                conf=self._confidence_threshold,
                device=self._device,
                imgsz=self._imgsz,
                verbose=False,
            )
        except Exception as exc:
            logger.error("YOLO inference error: {}", exc)
            return []

        detections: list[DetectedObject] = []
        for result in results:
            if result.boxes is None:
                continue
            for box in result.boxes:
                xyxy = box.xyxy[0].cpu().numpy().astype(int)
                x1, y1, x2, y2 = int(xyxy[0]), int(xyxy[1]), int(xyxy[2]), int(xyxy[3])
                conf = float(box.conf[0].cpu().numpy())
                cls_id = int(box.cls[0].cpu().numpy())
                label = YOLO_CLASS_LABELS.get(cls_id, f"class_{cls_id}")

                tier = (
                    DetectionConfidence.HIGH
                    if conf >= 0.8
                    else DetectionConfidence.MEDIUM
                    if conf >= 0.5
                    else DetectionConfidence.LOW
                )

                detections.append(
                    DetectedObject(
                        box=Region(x=x1, y=y1, w=x2 - x1, h=y2 - y1),
                        label=label,
                        confidence=conf,
                        confidence_tier=tier,
                        source="yolo",
                        yolo_class_id=cls_id,
                    )
                )

        logger.debug("YoloDetector: {} detections (conf≥{})", len(detections), self._confidence_threshold)
        return detections

    # ---------- convenience ----------

    def set_model_path(self, path: Path) -> None:
        """Hot-swap the model — resets the cached instance."""
        self._model_path = path
        self._model = None
        self._load_attempted = False

    def warmup(self) -> bool:
        """Run a dummy inference to pre-load the model weights. Returns True on success."""
        if not self.is_available():
            return False
        dummy = np.zeros((640, 640, 3), dtype=np.uint8)
        try:
            self.detect(dummy)
            return True
        except Exception:
            return False
