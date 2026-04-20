"""Combat runner — PvM solo scripted rotations.

MVP: one character class, one hardcoded rotation per map tier.
Next: YAML-driven rotations with conditions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.services.input_service import InputService
    from src.services.vision import VisionService


@dataclass
class CombatTickResult:
    in_combat: bool
    ended: bool = False
    victory: bool = False
    notes: str = ""


class CombatRunner:
    def __init__(self, vision: VisionService, input_svc: InputService) -> None:
        self._vision = vision
        self._input = input_svc

    def tick(self) -> CombatTickResult:
        raise NotImplementedError(
            "Implement me: "
            "(1) detect turn start via template on turn bar; "
            "(2) OCR HP/PA/PM; "
            "(3) pick next spell per rotation; "
            "(4) cast; "
            "(5) detect turn end (template 'End turn' highlighted or timer)."
        )

    def flee_if_low_hp(self, current_hp: int, max_hp: int, threshold: float = 0.25) -> bool:
        raise NotImplementedError("Implement me: if HP <= threshold, press flee hotkey.")
