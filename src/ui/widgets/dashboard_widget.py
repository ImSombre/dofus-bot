"""Dashboard tab — bot control + real-time stats."""

from __future__ import annotations

import time
from datetime import datetime
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.models.enums import BotState, JobType
from src.ui.styles import (
    ACCENT_BLUE,
    ACCENT_GREEN,
    ACCENT_ORANGE,
    ACCENT_RED,
    BG_CARD,
    BORDER,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from src.ui.widgets.common import StatCard, StateIndicator, make_card

if TYPE_CHECKING:
    from src.config.settings import Settings
    from src.handlers.state_machine import BotStateMachine
    from src.models.game_state import GameState


class DashboardWidget(QWidget):
    """Main dashboard: state indicator, control buttons, live stats, current action."""

    def __init__(
        self,
        state_machine: "BotStateMachine",
        settings: "Settings",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._sm = state_machine
        self._settings = settings
        self._start_timestamp: float | None = None
        self._log_lines: list[str] = []

        self._build_ui()
        self._connect_timer()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # Zone 1 — state + controls
        root.addWidget(self._build_control_zone())

        # Zone 2 — stats grid
        root.addWidget(self._build_stats_zone())

        # Zone 3 — current action
        root.addWidget(self._build_action_zone())

    def _build_control_zone(self) -> QFrame:
        card = make_card(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        # State indicator
        self._state_indicator = StateIndicator(card)
        layout.addWidget(self._state_indicator)

        # Controls row
        controls = QHBoxLayout()
        controls.setSpacing(10)

        # Start / Pause / Stop buttons
        self._btn_start = QPushButton("▶  Démarrer", card)
        self._btn_start.setObjectName("btn_start")
        self._btn_start.setToolTip("Démarrer le bot (F5)")
        self._btn_start.clicked.connect(self._on_start)

        self._btn_pause = QPushButton("⏸  Pause", card)
        self._btn_pause.setObjectName("btn_pause")
        self._btn_pause.setToolTip("Mettre en pause (F7)")
        self._btn_pause.clicked.connect(self._on_pause)
        self._btn_pause.setEnabled(False)

        self._btn_stop = QPushButton("⏹  Arrêter", card)
        self._btn_stop.setObjectName("btn_stop")
        self._btn_stop.setToolTip("Arrêter le bot (F6)")
        self._btn_stop.clicked.connect(self._on_stop)
        self._btn_stop.setEnabled(False)

        controls.addWidget(self._btn_start, stretch=2)
        controls.addWidget(self._btn_pause, stretch=1)
        controls.addWidget(self._btn_stop, stretch=1)

        # Separator
        sep = QFrame(card)
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(f"color: {BORDER};")
        controls.addWidget(sep)

        # Job dropdown
        job_container = QVBoxLayout()
        job_container.setSpacing(3)
        job_label = QLabel("Métier :", card)
        job_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9pt;")
        self._combo_job = QComboBox(card)
        for jt in JobType:
            self._combo_job.addItem(jt.value.capitalize(), jt.value)
        job_container.addWidget(job_label)
        job_container.addWidget(self._combo_job)
        controls.addLayout(job_container)

        # Zone dropdown
        zone_container = QVBoxLayout()
        zone_container.setSpacing(3)
        zone_label = QLabel("Zone :", card)
        zone_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9pt;")
        self._combo_zone = QComboBox(card)
        # Populate with default zone + common zones
        zones = [
            self._settings.default_zone,
            "bonta_forest_nord",
            "bonta_fields",
            "amakna_forest",
            "incarnam_fields",
        ]
        seen: set[str] = set()
        for z in zones:
            if z not in seen:
                self._combo_zone.addItem(z, z)
                seen.add(z)
        zone_container.addWidget(zone_label)
        zone_container.addWidget(self._combo_zone)
        controls.addLayout(zone_container)

        # Recalibrate button
        self._btn_calibrate = QPushButton("⚙  Recalibrer", card)
        self._btn_calibrate.setToolTip("Forcer une recalibration de l'interface")
        self._btn_calibrate.clicked.connect(self._on_calibrate)
        controls.addWidget(self._btn_calibrate)

        layout.addLayout(controls)
        return card

    def _build_stats_zone(self) -> QWidget:
        container = QWidget(self)
        grid = QGridLayout(container)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(12)

        self._card_runtime = StatCard("Runtime", unit="", color=ACCENT_BLUE, with_sparkline=False)
        self._card_xp = StatCard("XP / heure", unit="xp/h", color=ACCENT_GREEN, with_sparkline=True)
        self._card_kamas = StatCard("Kamas / heure", unit="k/h", color=ACCENT_ORANGE, with_sparkline=True)
        self._card_actions = StatCard("Actions totales", unit="", color=ACCENT_BLUE, with_sparkline=False)

        grid.addWidget(self._card_runtime, 0, 0)
        grid.addWidget(self._card_xp, 0, 1)
        grid.addWidget(self._card_kamas, 1, 0)
        grid.addWidget(self._card_actions, 1, 1)

        return container

    def _build_action_zone(self) -> QFrame:
        card = make_card(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        header = QLabel("Action en cours", card)
        header.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9pt; font-weight: 600;")

        self._action_label = QLabel("En attente...", card)
        self._action_label.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 11pt; font-weight: 500;")
        self._action_label.setWordWrap(True)

        self._progress_bar = QProgressBar(card)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(False)

        self._last_log_label = QLabel("", card)
        self._last_log_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 9pt; font-family: Consolas, monospace;"
        )
        self._last_log_label.setWordWrap(True)

        layout.addWidget(header)
        layout.addWidget(self._action_label)
        layout.addWidget(self._progress_bar)
        layout.addWidget(self._last_log_label)

        return card

    # ------------------------------------------------------------------
    # Timer + stats refresh
    # ------------------------------------------------------------------

    def _connect_timer(self) -> None:
        self._ui_timer = QTimer(self)
        self._ui_timer.setInterval(1000)
        self._ui_timer.timeout.connect(self._refresh_runtime)
        self._ui_timer.start()

    def _refresh_runtime(self) -> None:
        """Update the runtime clock every second without needing a signal."""
        if self._start_timestamp is not None:
            elapsed = int(time.monotonic() - self._start_timestamp)
            h = elapsed // 3600
            m = (elapsed % 3600) // 60
            s = elapsed % 60
            self._card_runtime.set_value(f"{h:02d}:{m:02d}:{s:02d}", push_history=False)

    # ------------------------------------------------------------------
    # Public slot — called from BotWorker signal
    # ------------------------------------------------------------------

    def on_stats_updated(self, gs: "GameState") -> None:
        """Receive a fresh GameState snapshot and update all stat cards."""
        state_name = gs.state.name
        self._state_indicator.set_state(state_name)
        self._update_buttons(gs.state)

        # Runtime: start clock on first non-idle state
        if gs.state not in (BotState.IDLE, BotState.STOPPING) and self._start_timestamp is None:
            self._start_timestamp = time.monotonic() - gs.runtime_seconds
        elif gs.state == BotState.IDLE:
            self._start_timestamp = None
            self._card_runtime.set_value("00:00:00", push_history=False)

        # XP / Kamas (derive per-hour from runtime)
        runtime_h = max(gs.runtime_seconds / 3600.0, 0.001)
        xp_h = int(gs.xp_gained / runtime_h) if gs.runtime_seconds > 0 else 0
        # Kamas not tracked in GameState yet — placeholder 0
        kamas_h = int(gs.scratch.get("kamas_gained", 0) / runtime_h) if gs.runtime_seconds > 0 else 0

        self._card_xp.set_value(f"{xp_h:,}")
        self._card_kamas.set_value(f"{kamas_h:,}")
        self._card_actions.set_value(str(gs.actions_count), push_history=False)

        # Current action
        current_action = gs.scratch.get("current_action", "")
        if current_action:
            self._action_label.setText(str(current_action))
        else:
            _action_map = {
                BotState.IDLE: "En attente...",
                BotState.SCANNING: "Scan de la carte...",
                BotState.MOVING: "Déplacement en cours...",
                BotState.ACTING: "Action en cours...",
                BotState.COMBAT: "Combat en cours...",
                BotState.BANKING: "Banque...",
                BotState.CALIBRATING: "Calibration de l'interface...",
                BotState.PAUSED: "Pause",
                BotState.ERROR: "Erreur détectée",
            }
            self._action_label.setText(_action_map.get(gs.state, state_name))

        # Log line
        ts = datetime.now().strftime("%H:%M:%S")
        log_line = f"[{ts}] state={state_name} | actions={gs.actions_count} | xp={gs.xp_gained}"
        self._last_log_label.setText(log_line)

    def on_state_changed(self, _old: BotState, new: BotState) -> None:
        self._state_indicator.set_state(new.name)
        self._update_buttons(new)

    # ------------------------------------------------------------------
    # Button state management
    # ------------------------------------------------------------------

    def _update_buttons(self, state: BotState) -> None:
        running_states = {
            BotState.STARTING, BotState.SCANNING, BotState.MOVING,
            BotState.ACTING, BotState.COMBAT, BotState.BANKING,
            BotState.CHECKING_INVENTORY, BotState.CALIBRATING, BotState.RECONNECTING,
        }
        is_running = state in running_states
        is_paused = state == BotState.PAUSED
        is_idle = state == BotState.IDLE

        self._btn_start.setEnabled(is_idle)
        self._btn_pause.setEnabled(is_running or is_paused)
        self._btn_stop.setEnabled(not is_idle)

        if is_paused:
            self._btn_pause.setText("▶  Reprendre")
        else:
            self._btn_pause.setText("⏸  Pause")

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    def _on_start(self) -> None:
        from src.models.enums import BotMode  # noqa: PLC0415

        job_or_zone = self._combo_zone.currentData() or self._settings.default_zone
        self._sm.request_start(BotMode.FARM, job_or_zone)
        self._start_timestamp = time.monotonic()

    def _on_pause(self) -> None:
        if self._sm.state == BotState.PAUSED:
            self._sm.request_resume()
        else:
            self._sm.request_pause()

    def _on_stop(self) -> None:
        self._sm.request_stop()
        self._start_timestamp = None
        self._card_runtime.set_value("00:00:00", push_history=False)

    def _on_calibrate(self) -> None:
        self._sm.request_calibration()
