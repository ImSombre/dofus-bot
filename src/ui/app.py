"""Qt application builder.

Wires the `BotStateMachine` to the Qt UI via a `QThread` and signals.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, QThread, pyqtSignal

if TYPE_CHECKING:
    from src.config.settings import Settings
    from src.handlers.state_machine import BotStateMachine
    from src.models.enums import BotState
    from src.models.game_state import GameState
    from src.services.persistence import PersistenceService
    from src.services.vision import MssVisionService


class BotWorker(QObject):
    """QObject living in a QThread that drives the BotStateMachine."""

    state_changed = pyqtSignal(object, object)  # (old_state, new_state) — BotState enums
    stats_updated = pyqtSignal(object)  # GameState

    def __init__(self, state_machine: "BotStateMachine") -> None:
        super().__init__()
        self._sm = state_machine
        self._sm.add_state_listener(self._emit_state)
        self._sm.add_stats_listener(self._emit_stats)

    def _emit_state(self, old: "BotState", new: "BotState") -> None:
        self.state_changed.emit(old, new)

    def _emit_stats(self, gs: "GameState") -> None:
        self.stats_updated.emit(gs)

    def run(self) -> None:
        self._sm.run_forever()


def build_app(
    state_machine: "BotStateMachine",
    persistence: "PersistenceService",
    settings: "Settings",
    vision: "MssVisionService | None" = None,
) -> "MainWindow":
    """Create main window + launch worker thread.

    Args:
        state_machine: The bot state machine instance.
        persistence: SQLite persistence service.
        settings: Application settings.
        vision: Optional MssVisionService injected for the Debug tab.

    Returns:
        The constructed (but not yet shown) MainWindow.
    """
    from src.ui.main_window import MainWindow  # noqa: PLC0415
    from src.ui.styles import DARK_QSS  # noqa: PLC0415

    from PyQt6.QtWidgets import QApplication  # noqa: PLC0415

    app = QApplication.instance()
    if app is not None:
        app.setStyleSheet(DARK_QSS)

    window = MainWindow(
        state_machine=state_machine,
        persistence=persistence,
        settings=settings,
        vision=vision,
    )

    # Worker thread for the bot
    thread = QThread(parent=window)
    worker = BotWorker(state_machine)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)

    # Wire signals → main window slots
    worker.state_changed.connect(window.on_state_changed)
    worker.stats_updated.connect(window.on_stats_updated)

    # Keep refs alive on the window
    window._thread = thread  # type: ignore[attr-defined]
    window._worker = worker  # type: ignore[attr-defined]

    thread.start()
    return window
