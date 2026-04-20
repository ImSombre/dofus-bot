"""Job runners — per-profession automation loops.

Each runner implements the loop:
    locate_resource  →  move_to  →  interact  →  wait_harvest  →  validate  →  loop
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.models.detection import Detection
    from src.models.job import ZoneSpec
    from src.services.input_service import InputService
    from src.services.vision import VisionService


@dataclass
class JobTickResult:
    """Outcome of a single runner tick."""

    action_taken: bool
    harvested: int = 0
    notes: str = ""


class JobRunner(ABC):
    """Base class for all job runners."""

    def __init__(self, vision: VisionService, input_svc: InputService, zone: ZoneSpec) -> None:
        self._vision = vision
        self._input = input_svc
        self._zone = zone

    @abstractmethod
    def tick(self) -> JobTickResult:
        """Do one unit of work; return what happened."""

    # Shared helpers (to be filled in when we have real captures):

    def _scan_for_resources(self) -> list[Detection]:
        raise NotImplementedError

    def _go_interact(self, detection: Detection) -> bool:
        raise NotImplementedError


class LumberjackRunner(JobRunner):
    """Bûcheron — farme des arbres (frêne, châtaignier, chêne, ...)."""

    def tick(self) -> JobTickResult:
        raise NotImplementedError(
            "Implement me: scan for tree templates, pick closest reachable, click, "
            "wait harvest animation, validate XP gain via OCR bottom banner."
        )


class FarmerRunner(JobRunner):
    """Paysan — moissonne blé, orge, houblon."""

    def tick(self) -> JobTickResult:
        raise NotImplementedError("Implement me: same loop as Lumberjack with crop templates.")
