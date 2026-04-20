"""Vision service — screen capture, template matching, OCR, and multi-strategy detection.

Architecture: chain of responsibility with 3 composable detector strategies.

    TooltipOCRDetector    — hover mouse, wait 300 ms, OCR tooltip, parse name/level.
                            Slowest (~0.5–1 s per candidate) but most accurate label.
    ColorShapeDetector    — HSV segmentation + contour filtering.
                            Fast (<10 ms per frame) but returns unlabelled bounding boxes.
    TemplateMatchingDetector — classic cv2.matchTemplate fallback when templates exist.
                            Fastest when templates are up to date; fragile on UI patches.

    YoloDetector          — optional YOLO inference (see yolo_detector.py).

Scan pipeline:
    VisionService.scan_interactables()
        → ColorShapeDetector  (candidates)
        → TooltipOCRDetector  (classify each candidate via hover + OCR)
        → merge into list[DetectedObject]

Fixed UI text is read via read_ui_text(region) — no hover needed.

OCR limitations:
    - Tesseract accuracy degrades below 720p or with non-standard DPI scaling.
    - Each tooltip hover costs ~400–600 ms of bot time (mouse movement + wait).
    - French language pack ('fra') must be installed; fallback to 'eng' silently.
    - Tooltip OCR fails on transparent/animated backgrounds — use template fallback.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import numpy as np
from loguru import logger

if TYPE_CHECKING:
    from src.models.detection import (
        DetectedObject,
        Detection,
        Popup,
        Region,
        Tooltip,
        UIRegion,
    )
    from src.services.input_service import InputService


Frame = np.ndarray  # H x W x 3 uint8 BGR


# ---------------------------------------------------------------------------
# Protocol — the public interface consumed by handlers
# ---------------------------------------------------------------------------


@runtime_checkable
class VisionDetector(Protocol):
    """Single-strategy detector interface — plug-and-play."""

    def detect(self, frame: Frame) -> list["DetectedObject"]:
        """Run detection on a pre-captured frame."""
        ...

    def is_available(self) -> bool:
        """Return False if the detector cannot run (missing deps / model)."""
        ...


@runtime_checkable
class VisionService(Protocol):
    """Composite vision interface consumed by BotStateMachine, JobRunner, CombatRunner."""

    def capture(self, region: "Region | None" = None) -> Frame:
        """Take a screenshot. None means the full Dofus window region."""
        ...

    # --- legacy ---
    def find_templates(
        self,
        frame: Frame,
        templates: dict[str, Frame],
        threshold: float = 0.75,
    ) -> list["Detection"]:
        """Template-match a set against a frame (backward compat)."""
        ...

    def read_text(self, frame: Frame, region: "Region | None" = None, lang: str = "fra") -> str:
        """OCR a region and return decoded text (backward compat)."""
        ...

    def detect_popup(self, frame: Frame) -> str | None:
        """Return popup type string or None (backward compat)."""
        ...

    # --- new ---
    def scan_interactables(self, frame: Frame | None = None) -> list["DetectedObject"]:
        """Zero-template scan: color-shape candidates + OCR tooltip classification."""
        ...

    def read_ui_text(self, region: "UIRegion") -> str:
        """OCR a fixed UI region (HP, PA/PM, coords, map name…)."""
        ...

    def detect_tooltip(self, frame: Frame | None = None) -> "Tooltip | None":
        """Detect and parse any currently visible tooltip."""
        ...

    def detect_popup_typed(self, frame: Frame | None = None) -> "Popup | None":
        """Detect and classify a popup dialog."""
        ...


# ---------------------------------------------------------------------------
# Strategy 1 — ColorShapeDetector
# ---------------------------------------------------------------------------


class ColorShapeDetector:
    """Fast candidate finder via HSV segmentation + contour analysis.

    Returns DetectedObject instances with source='color_shape' and label='candidate'.
    Confidence is based on how cleanly the contour matches a typical interactable shape.

    Tuning parameters are intentionally exposed so AutoCalibrationService can adjust them.
    """

    # Default HSV ranges targeting Dofus resource colours (trees, crops, ores).
    # Each entry: (lower_hsv, upper_hsv, name_hint)
    DEFAULT_RANGES: list[tuple[tuple[int, int, int], tuple[int, int, int], str]] = [
        ((35, 40, 40), (85, 255, 255), "green"),     # trees / bushes
        ((15, 60, 60), (35, 255, 200), "brown"),     # trunks / ore
        ((20, 80, 100), (30, 255, 255), "yellow"),   # wheat / crops
        ((90, 50, 50), (130, 255, 200), "blue"),     # water / fish spots
    ]

    def __init__(
        self,
        min_area: int = 200,
        max_area: int = 8000,
        min_aspect: float = 0.2,
        max_aspect: float = 5.0,
        hsv_ranges: list[tuple[tuple[int, int, int], tuple[int, int, int], str]] | None = None,
    ) -> None:
        self._min_area = min_area
        self._max_area = max_area
        self._min_aspect = min_aspect
        self._max_aspect = max_aspect
        self._hsv_ranges = hsv_ranges or self.DEFAULT_RANGES

    def is_available(self) -> bool:
        return True

    def detect(self, frame: Frame) -> list["DetectedObject"]:
        import cv2

        from src.models.detection import DetectedObject, DetectionConfidence, Region

        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        results: list[DetectedObject] = []

        for lower, upper, color_name in self._hsv_ranges:
            lower_np = np.array(lower, dtype=np.uint8)
            upper_np = np.array(upper, dtype=np.uint8)
            mask = cv2.inRange(hsv, lower_np, upper_np)

            # Morphological cleanup
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for cnt in contours:
                area = cv2.contourArea(cnt)
                if not (self._min_area <= area <= self._max_area):
                    continue
                x, y, w, h = cv2.boundingRect(cnt)
                aspect = w / h if h > 0 else 0
                if not (self._min_aspect <= aspect <= self._max_aspect):
                    continue

                # Rough confidence: normalized area fit
                area_score = min(area / self._max_area, 1.0)
                confidence = 0.3 + 0.4 * area_score  # range [0.3, 0.7]

                results.append(
                    DetectedObject(
                        box=Region(x=x, y=y, w=w, h=h),
                        label="candidate",
                        confidence=confidence,
                        confidence_tier=DetectionConfidence.LOW,
                        source="color_shape",
                        dominant_color_hsv=lower,
                    )
                )

        logger.debug("ColorShapeDetector: {} candidates", len(results))
        return results


# ---------------------------------------------------------------------------
# Strategy 2 — TooltipOCRDetector
# ---------------------------------------------------------------------------


class TooltipOCRDetector:
    """Classify candidates by hovering the mouse and reading the tooltip via OCR.

    This detector does NOT run on a pre-captured frame — it controls the mouse
    and waits for the tooltip to appear. For this reason it is used AFTER
    ColorShapeDetector identifies candidate positions.

    OCR limitations (honest):
        - Tesseract accuracy ~85–90% on clean Dofus tooltips at 1920x1080.
        - At 1280x720 accuracy drops to ~70% — pre-scale image 2x before OCR.
        - Italic / stylised fonts (item names) degrade precision; crops filtered.
        - Hover wait of 300 ms is a minimum — slow machines may need 500 ms.
    """

    def __init__(
        self,
        input_svc: "InputService",
        tesseract_path: Path,
        lang: str = "fra",
        hover_wait_ms: int = 300,
        tooltip_scale_factor: float = 2.0,
        tessdata_dir: Path | None = None,
    ) -> None:
        self._input = input_svc
        self._tesseract_path = tesseract_path
        self._lang = lang
        self._hover_wait_ms = hover_wait_ms
        self._scale = tooltip_scale_factor
        self._tessdata_dir = tessdata_dir
        self._tess_ready = False

    def _init_tesseract(self) -> None:
        if self._tess_ready:
            return
        import os  # noqa: PLC0415
        import pytesseract  # noqa: PLC0415

        if self._tesseract_path.exists():
            pytesseract.pytesseract.tesseract_cmd = str(self._tesseract_path)
        else:
            logger.warning("Tesseract not found at {} — OCR may fail", self._tesseract_path)
        if self._tessdata_dir is not None:
            os.environ["TESSDATA_PREFIX"] = str(self._tessdata_dir)
        self._tess_ready = True

    def _tess_cfg(self, extra: str = "") -> str:
        # TESSDATA_PREFIX env var is set in _init_tesseract; no --tessdata-dir flag needed.
        return extra

    def is_available(self) -> bool:
        try:
            import pytesseract  # noqa: PLC0415, F401

            return self._tesseract_path.exists()
        except ImportError:
            return False

    def classify_candidate(self, cx: int, cy: int, capture_fn: "callable[[], Frame]") -> "Tooltip | None":
        """Move mouse to (cx, cy), wait for tooltip, capture, OCR, parse.

        Returns None if no tooltip text is found or OCR confidence is too low.
        """
        import cv2
        import pytesseract

        from src.models.detection import Tooltip

        self._init_tesseract()

        self._input.move_mouse(cx, cy)
        time.sleep(self._hover_wait_ms / 1000.0)

        frame = capture_fn()
        # Crop a tooltip-likely region below/above cursor (heuristic: 200x80 px)
        h, w = frame.shape[:2]
        tx = max(0, cx - 10)
        ty = max(0, cy - 90)
        tw = min(w - tx, 220)
        th = min(h - ty, 90)
        crop = frame[ty : ty + th, tx : tx + tw]

        if crop.size == 0:
            return None

        # Upscale for better OCR accuracy
        if self._scale != 1.0:
            crop = cv2.resize(
                crop,
                (int(crop.shape[1] * self._scale), int(crop.shape[0] * self._scale)),
                interpolation=cv2.INTER_CUBIC,
            )

        # Preprocess: grayscale + threshold
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        try:
            lang = self._lang if self._tesseract_path.exists() else "eng"
            raw = pytesseract.image_to_string(binary, lang=lang, config=self._tess_cfg("--psm 7 --oem 3"))
        except Exception as exc:
            logger.debug("OCR failed on tooltip crop: {}", exc)
            return None

        raw = raw.strip()
        if len(raw) < 2:
            return None

        tooltip = Tooltip.parse(raw)
        logger.debug("TooltipOCR at ({},{}) → '{}'", cx, cy, tooltip.name)
        return tooltip

    def detect(self, frame: Frame) -> list["DetectedObject"]:
        """Not meaningful without mouse control — returns empty list.

        Use classify_candidate() directly in scan_interactables().
        """
        return []


# ---------------------------------------------------------------------------
# Strategy 3 — TemplateMatchingDetector (existing logic, now a strategy)
# ---------------------------------------------------------------------------


class TemplateMatchingDetector:
    """Classic template matching via cv2.matchTemplate.

    Kept as fastest fallback when calibrated templates exist.
    """

    def __init__(
        self,
        templates: dict[str, Frame] | None = None,
        threshold: float = 0.75,
        nms_overlap: float = 0.3,
    ) -> None:
        self._templates: dict[str, Frame] = templates or {}
        self._threshold = threshold
        self._nms_overlap = nms_overlap

    def is_available(self) -> bool:
        return len(self._templates) > 0

    def load_templates_from_dir(self, directory: Path) -> int:
        """Load all PNG/JPG templates from a directory. Returns count loaded."""
        import cv2

        count = 0
        for p in directory.glob("*.png"):
            tpl = cv2.imread(str(p))
            if tpl is not None:
                self._templates[p.stem] = tpl
                count += 1
        for p in directory.glob("*.jpg"):
            tpl = cv2.imread(str(p))
            if tpl is not None:
                self._templates[p.stem] = tpl
                count += 1
        logger.info("TemplateMatchingDetector: loaded {} templates from {}", count, directory)
        return count

    def detect(self, frame: Frame) -> list["DetectedObject"]:
        import cv2

        from src.models.detection import DetectedObject, DetectionConfidence, Region

        results: list[DetectedObject] = []
        for label, template in self._templates.items():
            th, tw = template.shape[:2]
            fh, fw = frame.shape[:2]
            # Template must be strictly smaller than frame
            if th >= fh or tw >= fw:
                continue
            res = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
            # Clamp to [-1, 1] to avoid float artefacts on uniform backgrounds
            res = np.clip(res, -1.0, 1.0)
            locs = np.where(res >= self._threshold)
            for y, x in zip(*locs):
                conf = float(res[y, x])
                tier = (
                    DetectionConfidence.HIGH
                    if conf >= 0.85
                    else DetectionConfidence.MEDIUM
                    if conf >= 0.6
                    else DetectionConfidence.LOW
                )
                results.append(
                    DetectedObject(
                        box=Region(x=int(x), y=int(y), w=tw, h=th),
                        label=label,
                        confidence=conf,
                        confidence_tier=tier,
                        source="template",
                    )
                )

        results = self._nms(results)
        logger.debug("TemplateMatchingDetector: {} detections", len(results))
        return results

    def _nms(self, detections: list["DetectedObject"]) -> list["DetectedObject"]:
        """Simple greedy NMS — keeps highest confidence among overlapping boxes."""
        if not detections:
            return []
        detections = sorted(detections, key=lambda d: d.confidence, reverse=True)
        kept: list["DetectedObject"] = []
        for det in detections:
            overlaps = False
            for k in kept:
                if self._iou(det.box, k.box) > self._nms_overlap:
                    overlaps = True
                    break
            if not overlaps:
                kept.append(det)
        return kept

    @staticmethod
    def _iou(a: "Region", b: "Region") -> float:
        from src.models.detection import Region  # local import avoids circular

        ax1, ay1 = a.x, a.y
        ax2, ay2 = a.x + a.w, a.y + a.h
        bx1, by1 = b.x, b.y
        bx2, by2 = b.x + b.w, b.y + b.h
        ix = max(0, min(ax2, bx2) - max(ax1, bx1))
        iy = max(0, min(ay2, by2) - max(ay1, by1))
        inter = ix * iy
        union = a.w * a.h + b.w * b.h - inter
        return inter / union if union > 0 else 0.0


# ---------------------------------------------------------------------------
# Concrete MssVisionService — composite implementation
# ---------------------------------------------------------------------------


class MssVisionService:
    """Concrete implementation: mss + OpenCV + pytesseract + composable detectors.

    Backward-compatible with all VisionService Protocol methods consumed by
    job_runner.py and combat_runner.py.
    """

    def __init__(
        self,
        tesseract_path: Path,
        lang: str = "fra",
        input_svc: "InputService | None" = None,
        window_title: str = "Dofus 2.64",
        tessdata_dir: Path | None = None,
    ) -> None:
        self._tesseract_path = tesseract_path
        self._lang = lang
        self._input = input_svc
        self._window_title = window_title
        self._tessdata_dir = tessdata_dir
        self._tess_ready = False

        # Composable detectors
        self.color_shape = ColorShapeDetector()
        self.template_matching = TemplateMatchingDetector()
        self.tooltip_ocr = (
            TooltipOCRDetector(
                input_svc=input_svc,
                tesseract_path=tesseract_path,
                lang=lang,
                tessdata_dir=tessdata_dir,
            )
            if input_svc is not None
            else None
        )

        # Optional YOLO detector — injected externally if available
        self.yolo: "VisionDetector | None" = None

        # Cached window region (if capture is explicitly scoped via set_target_*)
        self._window_region: "Region | None" = None
        # Target window title — when set, capture() will locate it fresh every call
        self._target_window_title: str | None = None
        # Région de la dernière capture (écrite par capture(); pour offset clics)
        self._last_capture_region: "Region | None" = None
        # Mode plein écran par défaut (capture tout l'écran physique, pas juste la fenêtre Dofus)
        # Sert quand Dofus tourne en 1920x1080 sur un écran 2560x1440 par exemple.
        self._fullscreen_mode: bool = True

    @property
    def last_capture_region(self) -> "Region | None":
        """Région de la dernière capture en coords écran absolues."""
        return self._last_capture_region

    def set_fullscreen_mode(self, enabled: bool) -> None:
        """Force la capture sur l'écran primaire entier (bypass fenêtre Dofus)."""
        self._fullscreen_mode = enabled
        if enabled:
            logger.info("Vision : mode ÉCRAN ENTIER activé (bypass fenêtre Dofus)")
        else:
            logger.info("Vision : mode fenêtre Dofus (par défaut)")

    def is_fullscreen_mode(self) -> bool:
        return getattr(self, "_fullscreen_mode", False)

    # --- capture ---

    def capture(self, region: "Region | None" = None) -> Frame:
        import mss
        import mss.tools

        with mss.mss() as sct:
            target = region or self._get_window_region()
            # Expose la région utilisée pour la dernière capture (utile pour
            # convertir les coords frame → coords écran lors d'un clic)
            self._last_capture_region = target
            monitor = {
                "left": target.x,
                "top": target.y,
                "width": target.w,
                "height": target.h,
            }
            raw = sct.grab(monitor)
        import cv2

        # mss returns BGRA; convert to BGR
        bgra = np.array(raw)
        return cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)

    # --- target window management ---

    def set_target_window(self, window) -> None:
        """Indique au service quelle fenêtre capturer.

        Accepte :
          - un `DofusWindow` (objet du service window_detector)
          - un titre (str) : la position sera résolue à chaque capture
          - None : réinitialise → fallback écran primaire complet
        """
        from src.models.detection import Region

        if window is None:
            self._target_window_title = None
            self._window_region = None
            logger.info("Vision : cible réinitialisée → écran primaire complet")
            return

        # Duck-typing : DofusWindow ou objet similaire
        title = getattr(window, "title", None)
        if title:
            self._target_window_title = title
            # Snapshot immédiat pour que le 1er capture soit déjà scopé
            self._window_region = Region(
                x=getattr(window, "left", 0),
                y=getattr(window, "top", 0),
                w=getattr(window, "width", 1920),
                h=getattr(window, "height", 1080),
            )
            logger.info(
                "Vision : cible = '{}' ({}, {}) {}x{}",
                title, self._window_region.x, self._window_region.y,
                self._window_region.w, self._window_region.h,
            )
        elif isinstance(window, str):
            self._target_window_title = window
            self._window_region = None
            logger.info("Vision : cible par titre = '{}' (résolution à la volée)", window)

    def _resolve_window_region(self, title: str) -> "Region | None":
        """Résout en direct la position d'une fenêtre par son titre.

        Utilise l'API Win32 GetClientRect quand possible pour avoir la ZONE CLIENT
        (sans bordure ni titre). `pygetwindow` retourne la fenêtre OUTER ce qui
        inclut ~30-40 px de titre Windows en haut qui polluent les captures.
        """
        from src.models.detection import Region

        try:
            import pygetwindow as gw  # noqa: PLC0415
            matches = gw.getWindowsWithTitle(title)
            if not matches:
                return None
            w = matches[0]
            if getattr(w, "width", 0) <= 0 or getattr(w, "height", 0) <= 0:
                return None

            # Tente de récupérer la zone CLIENT (sans titre Windows) via Win32
            try:
                import ctypes  # noqa: PLC0415
                from ctypes import wintypes  # noqa: PLC0415

                hwnd = getattr(w, "_hWnd", None)
                if hwnd:
                    client = wintypes.RECT()
                    if ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(client)):
                        # Convertit le coin top-left du client en coords écran
                        point = wintypes.POINT(0, 0)
                        if ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(point)):
                            cw = client.right - client.left
                            ch = client.bottom - client.top
                            if cw > 0 and ch > 0:
                                # Log one-shot pour que l'utilisateur voie les vraies dims
                                if not getattr(self, "_logged_window_dims", False):
                                    logger.info(
                                        "🎯 Dofus fenêtre : client={}×{} à ({},{}) | outer={}×{}",
                                        cw, ch, point.x, point.y,
                                        int(w.width), int(w.height),
                                    )
                                    self._logged_window_dims = True
                                return Region(
                                    x=int(point.x), y=int(point.y),
                                    w=int(cw), h=int(ch),
                                )
            except Exception:
                pass

            # Fallback : bounding box outer (inclut le titre Windows)
            if not getattr(self, "_logged_window_dims", False):
                logger.warning(
                    "🎯 Dofus fenêtre (outer seulement) : {}×{} à ({},{})",
                    int(w.width), int(w.height), int(w.left), int(w.top),
                )
                self._logged_window_dims = True
            return Region(
                x=int(w.left), y=int(w.top),
                w=int(w.width), h=int(w.height),
            )
        except Exception as exc:
            logger.debug("Résolution fenêtre '{}' échouée : {}", title, exc)
            return None

    def _get_window_region(self) -> "Region":
        """Retourne la région à capturer.

        Priorité :
          0. Si mode ÉCRAN ENTIER activé → écran primaire complet toujours
          1. Si `_target_window_title` est set → résout en live + auto-check fullscreen
          2. Sinon si `_window_region` en cache → l'utilise
          3. Sinon → écran primaire complet
        """
        # 0. Override mode écran entier
        if getattr(self, "_fullscreen_mode", False):
            return self._full_screen_region()

        if self._target_window_title:
            live = self._resolve_window_region(self._target_window_title)
            if live is not None:
                # AUTO-DÉTECTION : si Dofus est significativement plus petit que
                # l'écran physique, on active automatiquement le mode écran entier
                # (ex: Dofus 1920×1080 sur un écran 2560×1440).
                if not getattr(self, "_auto_fullscreen_checked", False):
                    self._auto_fullscreen_checked = True
                    self._maybe_auto_enable_fullscreen(live)
                    if getattr(self, "_fullscreen_mode", False):
                        return self._full_screen_region()
                self._window_region = live
                return live
            logger.debug("Fenêtre '{}' introuvable → fallback cache ou écran", self._target_window_title)

        if self._window_region is not None:
            return self._window_region

        return self._full_screen_region()

    def _maybe_auto_enable_fullscreen(self, window_region: "Region") -> None:
        """Active automatiquement le mode écran entier si Dofus << écran physique."""
        try:
            import ctypes  # noqa: PLC0415
            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32
            dc = user32.GetDC(0)
            screen_w_phys = gdi32.GetDeviceCaps(dc, 118)  # DESKTOPHORZRES
            screen_h_phys = gdi32.GetDeviceCaps(dc, 117)  # DESKTOPVERTRES
            user32.ReleaseDC(0, dc)
            # Si la fenêtre Dofus couvre < 90% de l'écran physique, c'est qu'elle
            # est windowed ou scaled. Dans les 2 cas, mieux vaut capturer l'écran.
            if (
                screen_w_phys > 0 and screen_h_phys > 0
                and (window_region.w * 1.1 < screen_w_phys
                     or window_region.h * 1.1 < screen_h_phys)
            ):
                logger.warning(
                    "⚙ Auto-activation mode ÉCRAN ENTIER : Dofus={}×{} << écran={}×{}",
                    window_region.w, window_region.h,
                    screen_w_phys, screen_h_phys,
                )
                self._fullscreen_mode = True
        except Exception as exc:
            logger.debug("Auto-détection fullscreen échouée : {}", exc)

    def _full_screen_region(self) -> "Region":
        """Récupère la région plein-écran (écran primaire) en pixels PHYSIQUES.

        Tente Win32 DESKTOPHORZRES/DESKTOPVERTRES d'abord (pixels physiques garantis,
        même si DPI awareness pas active). Fallback sur mss.monitors sinon.
        """
        from src.models.detection import Region

        # Tentative 1 : Win32 DESKTOPHORZRES (ignore DPI scaling Windows)
        try:
            import ctypes  # noqa: PLC0415
            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32
            dc = user32.GetDC(0)
            # DESKTOPHORZRES=118, DESKTOPVERTRES=117 donnent les vraies dims physiques
            w_phys = gdi32.GetDeviceCaps(dc, 118)
            h_phys = gdi32.GetDeviceCaps(dc, 117)
            user32.ReleaseDC(0, dc)
            if w_phys > 0 and h_phys > 0:
                region = Region(x=0, y=0, w=int(w_phys), h=int(h_phys))
                self._window_region = region
                return region
        except Exception:
            pass

        # Tentative 2 : mss monitors (fallback)
        import mss

        with mss.mss() as sct:
            m = sct.monitors[1]
            region = Region(x=m["left"], y=m["top"], w=m["width"], h=m["height"])
        self._window_region = region
        return region

    # --- legacy backward-compat methods ---

    def find_templates(
        self,
        frame: Frame,
        templates: dict[str, Frame],
        threshold: float = 0.75,
    ) -> list["Detection"]:
        """Backward-compat wrapper — delegates to TemplateMatchingDetector."""
        self.template_matching._templates = templates
        self.template_matching._threshold = threshold
        detected = self.template_matching.detect(frame)
        return [d.to_legacy_detection() for d in detected]

    def _tess_cfg(self, extra: str = "") -> str:
        # TESSDATA_PREFIX env var is set in _init_tesseract; no --tessdata-dir flag needed.
        return extra

    def read_text(self, frame: Frame, region: "Region | None" = None, lang: str = "fra") -> str:
        """OCR arbitrary frame region (legacy path)."""
        import cv2
        import pytesseract

        self._init_tesseract()
        if region is not None:
            frame = frame[region.y : region.y + region.h, region.x : region.x + region.w]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        try:
            return pytesseract.image_to_string(gray, lang=lang or self._lang, config=self._tess_cfg()).strip()
        except Exception as exc:
            logger.debug("read_text OCR error: {}", exc)
            return ""

    def detect_popup(self, frame: Frame) -> str | None:
        """Legacy popup detection — delegates to detect_popup_typed."""
        popup = self.detect_popup_typed(frame)
        return popup.popup_type if popup else None

    # --- new API ---

    def scan_interactables(self, frame: Frame | None = None) -> list["DetectedObject"]:
        """Zero-template scan: ColorShape candidates → optional YOLO → optional OCR tooltip.

        Pipeline:
        1. Capture frame if not provided.
        2. Run YOLO if available (classified + located in one pass).
        3. Run ColorShape on remaining area.
        4. For each candidate: if TooltipOCRDetector available, hover + classify.
        5. Merge and deduplicate results.

        Note: with tooltip OCR enabled, this method moves the mouse and takes
        ~0.5 s per candidate. Disable for fast scans.
        """
        if frame is None:
            frame = self.capture()

        results: list["DetectedObject"] = []

        # YOLO pass (fast, classified)
        if self.yolo and self.yolo.is_available():
            yolo_results = self.yolo.detect(frame)
            results.extend(yolo_results)
            logger.debug("scan_interactables: YOLO found {} objects", len(yolo_results))

        # Color-shape pass
        cs_candidates = self.color_shape.detect(frame)

        if self.tooltip_ocr and self.tooltip_ocr.is_available():
            # Classify each candidate via hover OCR
            for candidate in cs_candidates:
                cx, cy = candidate.center
                # Offset to window coordinates if needed
                win = self._get_window_region()
                screen_x = win.x + cx
                screen_y = win.y + cy
                tooltip = self.tooltip_ocr.classify_candidate(
                    cx=screen_x,
                    cy=screen_y,
                    capture_fn=self.capture,
                )
                if tooltip and tooltip.name:
                    from src.models.detection import DetectionConfidence

                    classified = candidate.model_copy(
                        update={
                            "label": tooltip.name,
                            "tooltip_text": tooltip.raw_text,
                            "confidence": 0.75,
                            "confidence_tier": DetectionConfidence.MEDIUM,
                            "source": "ocr_tooltip",
                        }
                    )
                    results.append(classified)
                else:
                    # Keep unclassified candidate
                    results.append(candidate)
        else:
            # No OCR — just return color-shape candidates
            results.extend(cs_candidates)

        # Template matching fallback for labelled objects
        if self.template_matching.is_available():
            tpl_results = self.template_matching.detect(frame)
            results.extend(tpl_results)

        logger.info("scan_interactables: {} total detected objects", len(results))
        return results

    def read_ui_text(self, region: "UIRegion") -> str:
        """OCR a fixed named UI region. Faster than scan (no hover needed)."""
        frame = self.capture(region.region)
        return self.read_text(frame, lang=self._lang)

    def detect_tooltip(self, frame: Frame | None = None) -> "Tooltip | None":
        """Detect a tooltip that is already visible in the current frame."""
        import cv2
        import pytesseract

        from src.models.detection import Tooltip

        self._init_tesseract()
        if frame is None:
            frame = self.capture()

        # Heuristic: look for a semi-transparent dark rectangle in the upper portion
        # of the screen — Dofus tooltips appear as dark rounded boxes.
        # Simple approach: OCR a 300x100 strip near the cursor position.
        h, w = frame.shape[:2]
        # Default: center-top band
        strip = frame[max(0, h // 4) : h // 2, w // 4 : 3 * w // 4]
        gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
        try:
            raw = pytesseract.image_to_string(gray, lang=self._lang, config=self._tess_cfg("--psm 6")).strip()
        except Exception:
            return None
        if len(raw) < 2:
            return None
        return Tooltip.parse(raw)

    def detect_popup_typed(self, frame: Frame | None = None) -> "Popup | None":
        """Detect and classify a popup dialog.

        Checks: captcha patterns, moderation dialog, trade request, reconnect screen.
        Falls back to OCR when templates are unavailable.
        """
        import cv2

        from src.models.detection import Popup

        self._init_tesseract()
        if frame is None:
            frame = self.capture()

        # Try template matching first if templates exist
        if self.template_matching.is_available():
            detected = self.template_matching.detect(frame)
            for d in detected:
                if "captcha" in d.label.lower():
                    return Popup(popup_type="captcha", requires_human=True)
                if "moderation" in d.label.lower():
                    return Popup(popup_type="moderation", requires_human=True)
                if "trade" in d.label.lower():
                    return Popup(popup_type="trade_request", raw_text=d.label)
                if "reconnect" in d.label.lower():
                    return Popup(popup_type="reconnect")

        # OCR fallback: scan full frame for known popup keywords
        try:
            import pytesseract

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            raw = pytesseract.image_to_string(gray, lang=self._lang, config=self._tess_cfg("--psm 6")).lower()
            if "captcha" in raw or "êtes-vous humain" in raw:
                return Popup(popup_type="captcha", raw_text=raw, requires_human=True)
            if "modération" in raw or "avertissement" in raw:
                return Popup(popup_type="moderation", raw_text=raw, requires_human=True)
            if "échange" in raw or "proposition" in raw:
                return Popup(popup_type="trade_request", raw_text=raw)
            if "connexion perdue" in raw or "déconnecté" in raw:
                return Popup(popup_type="reconnect", raw_text=raw)
        except Exception as exc:
            logger.debug("detect_popup_typed OCR error: {}", exc)

        return None

    # --- internals ---

    def _init_tesseract(self) -> None:
        if self._tess_ready:
            return
        import os  # noqa: PLC0415
        import pytesseract  # noqa: PLC0415

        if self._tesseract_path.exists():
            pytesseract.pytesseract.tesseract_cmd = str(self._tesseract_path)
        else:
            logger.warning("Tesseract not found at {} — OCR disabled", self._tesseract_path)
        if self._tessdata_dir is not None:
            os.environ["TESSDATA_PREFIX"] = str(self._tessdata_dir)
        self._tess_ready = True
