"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator
from unittest.mock import MagicMock

import pytest

from src.config.settings import Settings
from src.services.persistence import PersistenceService


@pytest.fixture
def tmp_db(tmp_path: Path) -> Iterator[Path]:
    """Temporary SQLite file."""
    path = tmp_path / "test.sqlite3"
    yield path
    if path.exists():
        path.unlink()


@pytest.fixture
def persistence(tmp_db: Path) -> PersistenceService:
    svc = PersistenceService(db_path=tmp_db)
    svc.initialize()
    return svc


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """A Settings instance pointed at tmp_path for persistence."""
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        db_path=tmp_path / "bot.sqlite3",
        maps_graph_path=tmp_path / "maps.json",
        log_dir=tmp_path / "logs",
        screenshots_dir=tmp_path / "screens",
        discord_enabled=False,
    )


@pytest.fixture
def mock_vision() -> MagicMock:
    """A VisionService mock."""
    m = MagicMock()
    m.capture.return_value = None
    m.find_templates.return_value = []
    m.read_text.return_value = ""
    m.detect_popup.return_value = None
    return m


@pytest.fixture
def mock_input() -> MagicMock:
    """An InputService mock."""
    return MagicMock()
