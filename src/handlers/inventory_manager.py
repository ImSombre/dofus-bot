"""Inventory fullness tracking + auto-banking trigger."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class InventoryState:
    estimated_fill: float  # 0..1
    items_since_last_bank: int
    max_slots: int = 100


class InventoryManager:
    """Estimate inventory fullness and decide when to bank.

    Strategy:
        - Maintain a counter incremented each successful harvest / kill loot.
        - Periodically (every N min) confirm visually by opening inventory + OCR.
        - Trigger BANKING when estimated_fill >= threshold.
    """

    def __init__(self, threshold: float = 0.9, max_slots: int = 100) -> None:
        self._threshold = threshold
        self._max_slots = max_slots
        self._counter = 0

    @property
    def state(self) -> InventoryState:
        fill = min(1.0, self._counter / self._max_slots)
        return InventoryState(estimated_fill=fill, items_since_last_bank=self._counter, max_slots=self._max_slots)

    def record_gain(self, slots: int = 1) -> None:
        self._counter += slots

    def reset(self) -> None:
        self._counter = 0

    def should_bank(self) -> bool:
        return self.state.estimated_fill >= self._threshold

    def visually_confirm(self) -> float:
        """Open inventory + OCR to confirm fill. Returns 0..1. Not implemented in MVP."""
        raise NotImplementedError("Implement me: open inventory, OCR slot count, return fill ratio.")
