"""Vision-related models — unified detection DTOs.

Pydantic v2 is used for DTOs that cross service boundaries.
Pure dataclasses are kept for internal hot-path objects (Region, Detection legacy).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Legacy dataclasses (kept for backward-compat with job_runner / combat_runner)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Region:
    """Rectangular screen region (pixels)."""

    x: int
    y: int
    w: int
    h: int

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2

    def as_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}


@dataclass(frozen=True, slots=True)
class Detection:
    """A single template-matching detection (legacy — kept for compatibility)."""

    box: Region
    label: str
    confidence: float
    template_id: str

    @property
    def center(self) -> tuple[int, int]:
        return self.box.center


# ---------------------------------------------------------------------------
# New unified models (Pydantic v2)
# ---------------------------------------------------------------------------


class DetectionConfidence(str, Enum):
    """Coarse confidence tier used across all detectors."""

    LOW = "low"       # < 0.5  — noisy, use with caution
    MEDIUM = "medium" # 0.5–0.8
    HIGH = "high"     # > 0.8  — reliable


class UIRegion(BaseModel):
    """Named screen region for fixed UI elements."""

    name: str
    x: int
    y: int
    w: int
    h: int
    description: str = ""

    @property
    def region(self) -> Region:
        return Region(x=self.x, y=self.y, w=self.w, h=self.h)

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2


class DetectedObject(BaseModel):
    """Unified detection result produced by any detector strategy.

    Fields are a superset — optional ones are filled by specific strategies.
    """

    box: Region
    label: str
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_tier: DetectionConfidence = DetectionConfidence.MEDIUM

    # strategy that produced this detection
    source: str = "unknown"  # "template" | "color_shape" | "ocr_tooltip" | "yolo"

    # extra data from each strategy
    dominant_color_hsv: tuple[int, int, int] | None = None  # ColorShapeDetector
    tooltip_text: str | None = None                          # TooltipOCRDetector
    yolo_class_id: int | None = None                         # YoloDetector

    model_config = {"arbitrary_types_allowed": True}

    @property
    def center(self) -> tuple[int, int]:
        return self.box.center

    def to_legacy_detection(self) -> Detection:
        """Downcast to legacy Detection for backward-compatible code paths."""
        return Detection(
            box=self.box,
            label=self.label,
            confidence=self.confidence,
            template_id=self.source,
        )


class Tooltip(BaseModel):
    """Parsed in-game tooltip displayed on mouse hover."""

    raw_text: str
    name: str = ""
    level: int | None = None
    resource_type: str | None = None  # e.g. "Frêne", "Blé", "Minerai de fer"
    extra: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def parse(cls, raw: str) -> "Tooltip":
        """Best-effort parser for 'Nom (Niveau X)' patterns from Dofus tooltips.

        OCR output is noisy — strip aggressively and fall back gracefully.
        """
        import re

        text = raw.strip()
        # Pattern: "Frêne (Niveau 15)" or "Frêne 15" or just "Frêne"
        match = re.search(r"([^\(]+?)\s*\(?[Nn]iveau\s*(\d+)\)?", text)
        if match:
            return cls(
                raw_text=raw,
                name=match.group(1).strip(),
                level=int(match.group(2)),
            )
        # Fallback: treat whole line as name
        return cls(raw_text=raw, name=text)


class Popup(BaseModel):
    """Detected game popup (captcha, modération, trade request, reconnect…)."""

    popup_type: str  # "captcha" | "moderation" | "trade_request" | "reconnect" | "unknown"
    raw_text: str = ""
    requires_human: bool = False


# ---------------------------------------------------------------------------
# Calibration models
# ---------------------------------------------------------------------------


class UIRegionsCalibration(BaseModel):
    """Result of Phase 1 calibration: fixed UI zone positions."""

    hp_bar: UIRegion | None = None
    pa_pm_bar: UIRegion | None = None
    minimap: UIRegion | None = None
    chat: UIRegion | None = None
    inventory_icon: UIRegion | None = None
    coordinate_display: UIRegion | None = None
    map_name: UIRegion | None = None
    xp_bar: UIRegion | None = None
    # generic extra regions keyed by name
    extra: dict[str, UIRegion] = Field(default_factory=dict)
    calibrated_at: str = ""  # ISO 8601


class MapCalibration(BaseModel):
    """Result of Phase 2 calibration: known interactables on a specific map."""

    map_id: str
    resources: list[dict[str, Any]] = Field(default_factory=list)
    # each entry: {name, level, color_signature, template_hash, count}
    calibrated_at: str = ""


class Calibration(BaseModel):
    """Top-level calibration bundle persisted to data/calibration/."""

    ui_regions: UIRegionsCalibration | None = None
    maps: dict[str, MapCalibration] = Field(default_factory=dict)
    schema_version: int = 1
