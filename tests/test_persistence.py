"""Persistence smoke tests."""

from __future__ import annotations

import sqlite3

from src.services.persistence import PersistenceService


def test_initialize_creates_tables(persistence: PersistenceService) -> None:
    # Inspect schema via a fresh connection
    conn = sqlite3.connect(persistence._db_path)  # type: ignore[attr-defined]  # noqa: SLF001
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    finally:
        conn.close()
    names = {r[0] for r in rows}
    assert {"sessions", "events", "stats_hourly", "known_maps", "errors"}.issubset(names)


def test_start_and_end_session(persistence: PersistenceService) -> None:
    session_id = persistence.start_session(mode="farm", job_or_zone="bonta_forest_sud")
    assert session_id > 0
    persistence.record_event(session_id, kind="harvest", payload={"resource": "frene", "xp": 42})
    persistence.end_session(session_id, end_reason="user_stop")
