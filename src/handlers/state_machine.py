"""Top-level bot state machine.

Responsibilities:
    - Orchestrate transitions between `BotState` values.
    - Dispatch ticks to the appropriate runner (JobRunner / CombatRunner).
    - Expose signals (via plain callbacks here — Qt wraps them in `ui/app.py`).
    - Handle stop / pause / resume from UI or Discord.

Thread model:
    Runs in a dedicated worker thread (QThread wraps `run_forever`).
    Communicates to UI via callbacks.

State CALIBRATING:
    Inserted between STARTING and IDLE on the very first launch (or when
    the user triggers "Recalibrer" from the GUI).
    If a valid Calibration is found on disk, CALIBRATING is skipped.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from loguru import logger

from src.models.enums import BotMode, BotState, EndReason
from src.models.game_state import GameState

if TYPE_CHECKING:
    from src.config.settings import Settings
    from src.services.auto_calibration import AutoCalibrationService
    from src.services.input_service import InputService
    from src.services.pathfinding import PathfindingService
    from src.services.persistence import PersistenceService
    from src.services.vision import VisionService


StateChangeCallback = Callable[[BotState, BotState], None]
StatsCallback = Callable[[GameState], None]


class BotStateMachine:
    """Main controller. Not Qt-aware — Qt wrapping happens in `ui/app.py`."""

    def __init__(
        self,
        vision: "VisionService",
        input_svc: "InputService",
        pathfinder: "PathfindingService",
        persistence: "PersistenceService",
        settings: "Settings",
        calibration_svc: "AutoCalibrationService | None" = None,
    ) -> None:
        self._vision = vision
        self._input = input_svc
        self._pathfinder = pathfinder
        self._persistence = persistence
        self._settings = settings
        self._calibration_svc = calibration_svc

        self._state = BotState.IDLE
        self._game_state = GameState()
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()  # set = running, clear = paused
        self._pause_event.set()
        self._tick_hz = 10

        # Callbacks (plain — Qt signals wired in app.py)
        self._on_state_change: list[StateChangeCallback] = []
        self._on_stats_update: list[StatsCallback] = []

    # ---------- public API ----------

    @property
    def state(self) -> BotState:
        return self._state

    @property
    def game_state(self) -> GameState:
        return self._game_state

    def add_state_listener(self, cb: StateChangeCallback) -> None:
        self._on_state_change.append(cb)

    def add_stats_listener(self, cb: StatsCallback) -> None:
        self._on_stats_update.append(cb)

    def request_start(self, mode: BotMode, job_or_zone: str) -> None:
        if self._state is not BotState.IDLE:
            logger.warning("Cannot start while state={}", self._state)
            return
        self._game_state.mode = mode
        session_id = self._persistence.start_session(mode.value, job_or_zone)
        self._game_state.session_id = session_id
        self._transition(BotState.STARTING)

    def request_stop(self) -> None:
        self._stop_event.set()
        self._pause_event.set()  # unblock if paused so loop can exit

    def request_pause(self) -> None:
        self._pause_event.clear()
        self._transition(BotState.PAUSED)

    def request_resume(self) -> None:
        self._pause_event.set()

    def request_calibration(self) -> None:
        """Force a re-calibration from the GUI 'Recalibrer' button."""
        logger.info("Manual calibration requested")
        self._transition(BotState.CALIBRATING)

    # ---------- run loop ----------

    def run_forever(self) -> None:
        """Main tick loop. Exits when `_stop_event` is set."""
        logger.info("State machine loop starting")
        tick_interval = 1.0 / self._tick_hz
        try:
            while not self._stop_event.is_set():
                self._pause_event.wait()  # blocks while paused
                if self._stop_event.is_set():
                    break
                try:
                    self._tick()
                except Exception as exc:
                    logger.exception("Tick error: {}", exc)
                    self._transition(BotState.ERROR)
                    if self._game_state.session_id is not None:
                        self._persistence.record_error(
                            session_id=self._game_state.session_id,
                            level="error",
                            message=str(exc),
                            traceback_text=None,
                        )
                time.sleep(tick_interval)
        finally:
            if self._game_state.session_id is not None:
                self._persistence.end_session(self._game_state.session_id, EndReason.USER_STOP.value)
            logger.info("State machine loop stopped")

    # ---------- private ----------

    def _tick(self) -> None:
        """Single tick — dispatches based on current state."""
        logger.trace("tick state={} mode={}", self._state, self._game_state.mode)

        if self._state is BotState.STARTING:
            self._handle_starting()
        elif self._state is BotState.CALIBRATING:
            self._handle_calibrating()
        # Other states (MOVING, SCANNING, ACTING, COMBAT, …) — plugged in later.

        # Notify UI every tick with fresh stats
        for cb in self._on_stats_update:
            cb(self._game_state)

    def _handle_starting(self) -> None:
        """Check for calibration; route to CALIBRATING or SCANNING."""
        if self._calibration_svc is not None:
            cal = self._calibration_svc.load_calibration()
            if cal is None or cal.ui_regions is None:
                logger.info("No calibration found — entering CALIBRATING state")
                self._transition(BotState.CALIBRATING)
                return
            logger.info("Calibration loaded — skipping CALIBRATING")
        # No calibration service or calibration exists: proceed
        self._transition(BotState.SCANNING)

    def _handle_calibrating(self) -> None:
        """Run Phase 1 calibration, save, then proceed to SCANNING."""
        if self._calibration_svc is None:
            logger.warning("CALIBRATING state reached but no AutoCalibrationService injected")
            self._transition(BotState.SCANNING)
            return

        try:
            logger.info("Running Phase 1 UI calibration…")
            ui_cal = self._calibration_svc.calibrate_ui_regions(interactive=True)

            from src.models.detection import Calibration

            cal = Calibration(ui_regions=ui_cal)
            self._calibration_svc.save_calibration(cal)
            logger.info("Calibration complete — transitioning to SCANNING")
        except Exception as exc:
            logger.error("Calibration failed: {} — continuing without it", exc)

        self._transition(BotState.SCANNING)

    def _transition(self, new_state: BotState) -> None:
        if new_state is self._state:
            return
        old = self._state
        self._state = new_state
        self._game_state.state = new_state
        logger.info("State transition: {} -> {}", old.name, new_state.name)
        for cb in self._on_state_change:
            cb(old, new_state)
