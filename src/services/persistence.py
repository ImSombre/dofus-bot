"""SQLite persistence — sessions, events, stats, errors."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from loguru import logger


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    mode TEXT NOT NULL,
    job_or_zone TEXT NOT NULL,
    total_xp INTEGER DEFAULT 0,
    total_actions INTEGER DEFAULT 0,
    total_errors INTEGER DEFAULT 0,
    end_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, ts);

CREATE TABLE IF NOT EXISTS stats_hourly (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    hour TEXT NOT NULL,
    xp_gained INTEGER DEFAULT 0,
    actions_count INTEGER DEFAULT 0,
    kamas_estimated INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS known_maps (
    id TEXT PRIMARY KEY,
    x INTEGER NOT NULL,
    y INTEGER NOT NULL,
    zone TEXT,
    last_visited TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS inventory_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    ts TEXT NOT NULL,
    kamas INTEGER,
    items_json TEXT
);

CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    ts TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    traceback TEXT,
    screenshot_path TEXT
);
"""

CURRENT_SCHEMA_VERSION = 1


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class PersistenceService:
    """Thin wrapper around sqlite3 with repository-style methods."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)

    # ---------- lifecycle ----------

    def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA_SQL)
            row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            if row is None:
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (CURRENT_SCHEMA_VERSION,))
            elif row[0] != CURRENT_SCHEMA_VERSION:
                logger.warning(
                    "Schema version mismatch: db={} code={}. Migrations not implemented yet.",
                    row[0],
                    CURRENT_SCHEMA_VERSION,
                )
        logger.info("Persistence initialized at {}", self._db_path)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ---------- sessions ----------

    def start_session(self, mode: str, job_or_zone: str) -> int:
        with self._conn() as conn:
            cursor = conn.execute(
                "INSERT INTO sessions (started_at, mode, job_or_zone) VALUES (?, ?, ?)",
                (_utcnow(), mode, job_or_zone),
            )
            session_id = cursor.lastrowid
            assert session_id is not None
            return int(session_id)

    def end_session(self, session_id: int, end_reason: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE sessions SET ended_at = ?, end_reason = ? WHERE id = ?",
                (_utcnow(), end_reason, session_id),
            )

    # ---------- events ----------

    def record_event(self, session_id: int, kind: str, payload: dict[str, Any] | None = None) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO events (session_id, ts, kind, payload) VALUES (?, ?, ?, ?)",
                (session_id, _utcnow(), kind, json.dumps(payload) if payload else None),
            )

    # ---------- errors ----------

    def record_error(
        self,
        session_id: int,
        level: str,
        message: str,
        traceback_text: str | None = None,
        screenshot_path: str | None = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO errors
                   (session_id, ts, level, message, traceback, screenshot_path)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (session_id, _utcnow(), level, message, traceback_text, screenshot_path),
            )
