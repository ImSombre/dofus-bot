"""UI tests — pytest-qt required (pip install pytest-qt)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.config.settings import Settings
from src.models.enums import BotState
from src.models.game_state import GameState
from src.services.persistence import PersistenceService


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        db_path=tmp_path / "bot.sqlite3",
        maps_graph_path=tmp_path / "maps.json",
        log_dir=tmp_path / "logs",
        screenshots_dir=tmp_path / "screens",
        discord_enabled=False,
    )


@pytest.fixture
def persistence(tmp_path: Path) -> PersistenceService:
    svc = PersistenceService(db_path=tmp_path / "test.sqlite3")
    svc.initialize()
    return svc


@pytest.fixture
def mock_state_machine() -> MagicMock:
    sm = MagicMock()
    sm.state = BotState.IDLE
    sm.game_state = GameState()
    sm.add_state_listener = MagicMock()
    sm.add_stats_listener = MagicMock()
    sm.request_start = MagicMock()
    sm.request_stop = MagicMock()
    sm.request_pause = MagicMock()
    sm.request_resume = MagicMock()
    sm.request_calibration = MagicMock()
    return sm


@pytest.fixture
def mock_vision() -> MagicMock:
    """Minimal MssVisionService mock for the Debug tab."""
    vis = MagicMock()
    # capture returns a small black BGR frame (100x100)
    vis.capture.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
    vis.color_shape = MagicMock()
    vis.color_shape.detect.return_value = []
    vis.template_matching = MagicMock()
    vis.template_matching.detect.return_value = []
    vis.yolo = None  # YOLO not available
    vis.read_text.return_value = "test"
    return vis


# ---------------------------------------------------------------------------
# Test 1 — MainWindow constructs without crash
# ---------------------------------------------------------------------------


def test_main_window_constructs(qtbot, settings, persistence, mock_state_machine):
    """MainWindow must instantiate with a mocked state machine without raising."""
    from src.ui.main_window import MainWindow

    window = MainWindow(
        state_machine=mock_state_machine,
        persistence=persistence,
        settings=settings,
        vision=None,
    )
    qtbot.addWidget(window)

    # Window is created with 7 tabs (Accueil en premier)
    assert window._tabs.count() == 7
    # L'onglet Accueil (SimpleDashboard) est en premier
    assert "Accueil" in window._tabs.tabText(0)


# ---------------------------------------------------------------------------
# Test 2 — Debug tab: clicking Capturer calls vision.capture()
# ---------------------------------------------------------------------------


def test_debug_tab_capture_button_calls_vision(qtbot, settings, persistence, mock_state_machine, mock_vision):
    """Clicking 'Capturer maintenant' in the Debug tab must call vision.capture()."""
    from src.ui.main_window import MainWindow
    from src.ui.widgets.debug_widget import DebugWidget
    from PyQt6.QtCore import QTimer

    window = MainWindow(
        state_machine=mock_state_machine,
        persistence=persistence,
        settings=settings,
        vision=mock_vision,
    )
    qtbot.addWidget(window)

    # Switch to debug tab (locate by type rather than hardcoded index)
    debug_idx = next(
        i for i in range(window._tabs.count()) if isinstance(window._tabs.widget(i), DebugWidget)
    )
    window._tabs.setCurrentIndex(debug_idx)
    debug_widget = window._tabs.widget(debug_idx)
    assert isinstance(debug_widget, DebugWidget)

    from PyQt6.QtCore import Qt  # noqa: PLC0415

    # Click capture button and wait for QThreadPool worker to finish
    with qtbot.waitSignal(debug_widget._btn_capture.clicked, timeout=500, raising=False):
        qtbot.mouseClick(debug_widget._btn_capture, Qt.MouseButton.LeftButton)

    # Give the thread pool a moment to execute
    qtbot.wait(400)

    # vision.capture() must have been called at least once
    mock_vision.capture.assert_called()


# ---------------------------------------------------------------------------
# Test 3 — DashboardWidget receives stats update without crash
# ---------------------------------------------------------------------------


def test_dashboard_stats_update(qtbot, settings, mock_state_machine):
    """DashboardWidget.on_stats_updated must not raise with a GameState snapshot."""
    from src.ui.widgets.dashboard_widget import DashboardWidget

    widget = DashboardWidget(
        state_machine=mock_state_machine,
        settings=settings,
    )
    qtbot.addWidget(widget)

    gs = GameState(
        state=BotState.SCANNING,
        xp_gained=1500,
        actions_count=42,
        runtime_seconds=3600,
    )
    # Must not raise
    widget.on_stats_updated(gs)

    # actions_count=42 → displayed as "42"
    assert widget._card_actions._value_label.text() == "42"
