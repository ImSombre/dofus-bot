"""Inventory manager logic tests (pure, no IO)."""

from __future__ import annotations

from src.handlers.inventory_manager import InventoryManager


def test_initial_state_is_empty() -> None:
    mgr = InventoryManager(threshold=0.9, max_slots=100)
    assert mgr.state.estimated_fill == 0.0
    assert not mgr.should_bank()


def test_triggers_banking_at_threshold() -> None:
    mgr = InventoryManager(threshold=0.9, max_slots=100)
    for _ in range(89):
        mgr.record_gain()
    assert not mgr.should_bank()
    mgr.record_gain()
    assert mgr.should_bank()


def test_reset_clears_counter() -> None:
    mgr = InventoryManager(threshold=0.5, max_slots=10)
    for _ in range(10):
        mgr.record_gain()
    assert mgr.should_bank()
    mgr.reset()
    assert not mgr.should_bank()
    assert mgr.state.estimated_fill == 0.0
