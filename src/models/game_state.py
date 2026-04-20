"""Aggregate runtime state of the bot."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from src.models.enums import BotMode, BotState
from src.models.map import MapId


class GameState(BaseModel):
    """Mutable state, snapshot-able for UI / persistence."""

    state: BotState = BotState.IDLE
    mode: BotMode = BotMode.IDLE
    current_map: MapId | None = None
    session_id: int | None = None
    started_at: datetime | None = None
    runtime_seconds: int = 0
    xp_gained: int = 0
    actions_count: int = 0
    errors_count: int = 0
    inventory_fill: float = 0.0  # 0..1
    last_bank_at: datetime | None = None
    # Free-form debug scratch
    scratch: dict[str, Any] = Field(default_factory=dict)
