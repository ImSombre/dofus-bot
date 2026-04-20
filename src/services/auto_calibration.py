"""AutoCalibrationService — reduces manual config to near zero.

Two-phase calibration:
    Phase 1 (automatic):
        Locates fixed Dofus UI regions (HP bar, PA/PM, minimap, chat, etc.)
        using OpenCV heuristics on a captured frame. Shows Qt confirmation
        overlays to the user.

    Phase 2 (interactive, optional):
        Scans the current map for interactable resources via ColorShapeDetector,
        groups them by dominant colour, hovers a sample with OCR, and proposes
        adding them to the known_resources SQLite table.

Persistence: data/calibration/ui_regions.json (UIRegionsCalibration model).

Usage:
    svc = AutoCalibrationService(vision=vision_svc, settings=settings)
    calibration = svc.load_calibration()
    if calibration is None:
        calibration = svc.calibrate_ui_regions()  # Phase 1
        svc.save_calibration(calibration)

Notes on robustness:
    - Phase 1 uses relative positions typical of Dofus 2.x windowed mode.
      At non-standard resolutions the heuristics may misplace a region — the
      Qt confirmation dialog lets the user validate or re-draw.
    - Phase 2 OCR accuracy degrades at <720p; recommended minimum 1280x720.
    - Calibration is invalidated when the Dofus client is patched (UI layout
      changes). Use the GUI "Recalibrer" button to re-run.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from src.config.settings import Settings
    from src.models.detection import (
        Calibration,
        MapCalibration,
        UIRegion,
        UIRegionsCalibration,
    )
    from src.services.vision import MssVisionService


class AutoCalibrationService:
    """Orchestrates UI and map calibration with optional Qt confirmation dialogs."""

    # -----------------------------------------------------------------------
    # Dofus 2.x default relative positions (fraction of window size).
    # These match the standard UI layout in windowed 1024x768+ mode.
    # -----------------------------------------------------------------------
    _RELATIVE_UI: dict[str, dict[str, float]] = {
        "hp_bar":             {"rx": 0.02, "ry": 0.95, "rw": 0.10, "rh": 0.03},
        "pa_pm_bar":          {"rx": 0.02, "ry": 0.91, "rw": 0.10, "rh": 0.03},
        "minimap":            {"rx": 0.80, "ry": 0.02, "rw": 0.18, "rh": 0.18},
        "chat":               {"rx": 0.00, "ry": 0.75, "rw": 0.40, "rh": 0.24},
        "inventory_icon":     {"rx": 0.90, "ry": 0.93, "rw": 0.04, "rh": 0.06},
        "coordinate_display": {"rx": 0.81, "ry": 0.00, "rw": 0.10, "rh": 0.03},
        "map_name":           {"rx": 0.35, "ry": 0.00, "rw": 0.30, "rh": 0.03},
        "xp_bar":             {"rx": 0.00, "ry": 0.98, "rw": 1.00, "rh": 0.02},
    }

    def __init__(
        self,
        vision: "MssVisionService",
        settings: "Settings",
        db_path: Path | None = None,
    ) -> None:
        self._vision = vision
        self._settings = settings
        self._calibration_dir = settings.calibration_data_dir
        self._calibration_dir.mkdir(parents=True, exist_ok=True)
        self._ui_regions_path = self._calibration_dir / "ui_regions.json"
        self._calibration_path = self._calibration_dir / "calibration.json"
        self._db_path = db_path or settings.db_path

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def load_calibration(self) -> "Calibration | None":
        """Load persisted calibration from disk. Returns None if not found."""
        from src.models.detection import Calibration

        if not self._calibration_path.exists():
            return None
        try:
            data = json.loads(self._calibration_path.read_text(encoding="utf-8"))
            return Calibration.model_validate(data)
        except Exception as exc:
            logger.warning("Failed to load calibration ({}), will re-calibrate", exc)
            return None

    def save_calibration(self, calibration: "Calibration") -> None:
        """Persist calibration to disk."""
        self._calibration_path.write_text(
            calibration.model_dump_json(indent=2),
            encoding="utf-8",
        )
        logger.info("Calibration saved to {}", self._calibration_path)

    def calibrate_ui_regions(self, interactive: bool = True) -> "UIRegionsCalibration":
        """Phase 1 — Detect fixed UI regions and optionally confirm with user.

        Args:
            interactive: If True and a Qt application is running, shows
                         confirmation overlays. Set to False for headless/CI.

        Returns:
            UIRegionsCalibration with detected region positions.
        """
        from src.models.detection import UIRegion, UIRegionsCalibration

        logger.info("Phase 1: calibrating UI regions")
        frame = self._vision.capture()
        h, w = frame.shape[:2]
        logger.debug("Captured frame: {}x{}", w, h)

        regions: dict[str, UIRegion] = {}
        for name, rel in self._RELATIVE_UI.items():
            x = int(rel["rx"] * w)
            y = int(rel["ry"] * h)
            rw = max(10, int(rel["rw"] * w))
            rh = max(10, int(rel["rh"] * h))

            # Refine with OpenCV heuristics
            region = self._refine_region(frame, name, x, y, rw, rh)
            regions[name] = region
            logger.debug("  {} → ({}, {}, {}x{})", name, region.x, region.y, region.w, region.h)

        calibration = UIRegionsCalibration(
            hp_bar=regions.get("hp_bar"),
            pa_pm_bar=regions.get("pa_pm_bar"),
            minimap=regions.get("minimap"),
            chat=regions.get("chat"),
            inventory_icon=regions.get("inventory_icon"),
            coordinate_display=regions.get("coordinate_display"),
            map_name=regions.get("map_name"),
            xp_bar=regions.get("xp_bar"),
            calibrated_at=datetime.now(timezone.utc).isoformat(),
        )

        if interactive:
            calibration = self._confirm_with_user(frame, calibration)

        logger.info("Phase 1 complete: {} regions calibrated", len(regions))
        return calibration

    def calibrate_map(self, map_id: str, interactive: bool = True) -> "MapCalibration":
        """Phase 2 — Interactive map calibration: detect resources and store them.

        Scans current map via ColorShapeDetector, groups candidates by colour,
        hovers a sample per group with OCR, and proposes adding them to SQLite.

        Args:
            map_id: Identifier for this map (e.g. 'bonta_forest_7_3').
            interactive: Show Qt confirmation dialogs.

        Returns:
            MapCalibration with discovered resources.
        """
        from src.models.detection import MapCalibration

        logger.info("Phase 2: calibrating map '{}'", map_id)
        frame = self._vision.capture()

        # Detect candidates via color-shape
        candidates = self._vision.color_shape.detect(frame)
        logger.info("Found {} color-shape candidates on map '{}'", len(candidates), map_id)

        if not candidates:
            logger.warning("No candidates found — is the game window visible?")
            return MapCalibration(
                map_id=map_id,
                calibrated_at=datetime.now(timezone.utc).isoformat(),
            )

        # Group candidates by dominant colour
        groups = self._group_by_color(candidates)
        resources: list[dict[str, Any]] = []

        for color_key, group in groups.items():
            count = len(group)
            sample = group[0]
            cx, cy = sample.center

            # OCR the sample candidate
            tooltip = None
            if self._vision.tooltip_ocr and self._vision.tooltip_ocr.is_available():
                win = self._vision._get_window_region()
                tooltip = self._vision.tooltip_ocr.classify_candidate(
                    cx=win.x + cx,
                    cy=win.y + cy,
                    capture_fn=self._vision.capture,
                )

            name = tooltip.name if tooltip else f"unknown_{color_key}"
            level = tooltip.level if tooltip else None
            resource_entry: dict[str, Any] = {
                "name": name,
                "level": level,
                "color_signature": color_key,
                "count": count,
                "template_hash": None,
            }

            if interactive:
                confirmed = self._confirm_resource(name, count, map_id)
            else:
                confirmed = True  # headless: accept all

            if confirmed:
                resources.append(resource_entry)
                self._persist_resource(map_id, resource_entry, frame, sample)
                logger.info("  Added resource '{}' (level={}, count={})", name, level, count)
            else:
                logger.debug("  Skipped resource '{}'", name)

        map_cal = MapCalibration(
            map_id=map_id,
            resources=resources,
            calibrated_at=datetime.now(timezone.utc).isoformat(),
        )
        logger.info("Phase 2 complete: {} resource types saved for map '{}'", len(resources), map_id)
        return map_cal

    # -----------------------------------------------------------------------
    # Region refinement
    # -----------------------------------------------------------------------

    def _refine_region(
        self,
        frame: Any,
        name: str,
        x: int,
        y: int,
        w: int,
        h: int,
    ) -> "UIRegion":
        """Attempt to tighten the bounding box using edge detection.

        Falls back to the raw relative position if refinement fails.
        """
        import cv2

        from src.models.detection import UIRegion

        descriptions = {
            "hp_bar": "Barre de vie (rouge)",
            "pa_pm_bar": "Barre PA/PM",
            "minimap": "Minimap",
            "chat": "Zone de chat",
            "inventory_icon": "Icône inventaire",
            "coordinate_display": "Coordonnées de map",
            "map_name": "Nom de la map",
            "xp_bar": "Barre d'XP",
        }

        try:
            fh, fw = frame.shape[:2]
            # Expand search area by 20% for robustness
            sx = max(0, x - w // 5)
            sy = max(0, y - h // 5)
            sw = min(fw - sx, w + w // 2)
            sh = min(fh - sy, h + h // 2)
            crop = frame[sy : sy + sh, sx : sx + sw]

            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            edges = cv2.Canny(gray, 50, 150)
            contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            if contours:
                # Pick the largest contour near the expected position
                best = max(contours, key=cv2.contourArea)
                bx, by, bw, bh = cv2.boundingRect(best)
                # Only use refinement if contour is reasonably sized
                if bw * bh > 50:
                    return UIRegion(
                        name=name,
                        x=sx + bx,
                        y=sy + by,
                        w=bw,
                        h=bh,
                        description=descriptions.get(name, name),
                    )
        except Exception as exc:
            logger.debug("Region refinement failed for '{}': {}", name, exc)

        return UIRegion(
            name=name,
            x=x,
            y=y,
            w=w,
            h=h,
            description=descriptions.get(name, name),
        )

    # -----------------------------------------------------------------------
    # Grouping helpers
    # -----------------------------------------------------------------------

    def _group_by_color(
        self,
        candidates: list[Any],
    ) -> dict[str, list[Any]]:
        """Group DetectedObject candidates by their dominant_color_hsv."""
        groups: dict[str, list[Any]] = {}
        for c in candidates:
            if c.dominant_color_hsv:
                key = f"{c.dominant_color_hsv[0]}_{c.dominant_color_hsv[1]}"
            else:
                key = "unknown"
            groups.setdefault(key, []).append(c)
        return groups

    # -----------------------------------------------------------------------
    # Persistence helpers
    # -----------------------------------------------------------------------

    def _persist_resource(
        self,
        map_id: str,
        resource: dict[str, Any],
        frame: Any,
        sample: Any,
    ) -> None:
        """Save resource to SQLite known_resources table and optionally crop template."""
        import sqlite3

        import cv2

        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.execute("""
                CREATE TABLE IF NOT EXISTS known_resources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    map_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    level INTEGER,
                    color_signature TEXT,
                    template_hash TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute(
                "INSERT INTO known_resources (map_id, name, level, color_signature, template_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    map_id,
                    resource["name"],
                    resource.get("level"),
                    resource.get("color_signature"),
                    resource.get("template_hash"),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.warning("Could not persist resource to DB: {}", exc)

        # Save 32x32 template crop for future speed-ups
        try:
            templates_dir = self._calibration_dir / "templates"
            templates_dir.mkdir(exist_ok=True)
            box = sample.box
            cx, cy = box.x + box.w // 2, box.y + box.h // 2
            size = 16  # half-side
            crop = frame[
                max(0, cy - size) : cy + size,
                max(0, cx - size) : cx + size,
            ]
            if crop.size > 0:
                safe_name = resource["name"].replace(" ", "_").replace("/", "_")
                tpl_path = templates_dir / f"{safe_name}.png"
                cv2.imwrite(str(tpl_path), crop)
                logger.debug("Saved template crop to {}", tpl_path)
        except Exception as exc:
            logger.debug("Could not save template crop: {}", exc)

    # -----------------------------------------------------------------------
    # Qt confirmation dialogs (no-ops if Qt unavailable)
    # -----------------------------------------------------------------------

    def _confirm_with_user(
        self,
        frame: Any,
        calibration: "UIRegionsCalibration",
    ) -> "UIRegionsCalibration":
        """Show Qt overlay dialog for Phase 1 confirmation.

        If PyQt6 is not available or no QApplication is running, returns
        calibration unchanged (assumes confirmed).
        """
        try:
            from PyQt6.QtWidgets import QApplication  # noqa: PLC0415

            if QApplication.instance() is None:
                logger.debug("No QApplication — skipping interactive Phase 1 confirmation")
                return calibration

            from src.ui.calibration_dialog import CalibrationConfirmDialog  # noqa: PLC0415

            dialog = CalibrationConfirmDialog(frame=frame, calibration=calibration)
            if dialog.exec():
                return dialog.result_calibration
        except ImportError:
            logger.debug("Qt or calibration dialog not available — accepting calibration as-is")
        except Exception as exc:
            logger.warning("Phase 1 confirmation dialog error: {}", exc)
        return calibration

    def _confirm_resource(self, name: str, count: int, map_id: str) -> bool:
        """Show a simple Qt question dialog for Phase 2 resource confirmation."""
        try:
            from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: PLC0415

            if QApplication.instance() is None:
                return True  # headless: auto-accept

            msg = QMessageBox()
            msg.setWindowTitle("Calibration — ressource détectée")
            msg.setText(
                f"J'ai détecté {count} objets '{name}' sur la map '{map_id}'.\n"
                f"Les ajouter comme ressource farmable ?"
            )
            msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            return msg.exec() == QMessageBox.StandardButton.Yes
        except ImportError:
            return True
        except Exception as exc:
            logger.debug("Resource confirmation dialog error: {}", exc)
            return True
