"""Map / pathfinding models."""

from __future__ import annotations

from typing import NewType

from pydantic import BaseModel, Field

MapId = NewType("MapId", str)


class CellCoord(BaseModel):
    """Logical Dofus map cell index (0..559)."""

    cell: int = Field(ge=0, le=559)


class MapNode(BaseModel):
    """A node in the maps graph."""

    id: MapId
    x: int
    y: int
    zone: str | None = None
    neighbors: dict[str, MapId] = Field(default_factory=dict)
    # ^ key = direction ("north", "south", ...) or "teleport:zaap_bonta"
    # value = destination map id

    model_config = {"frozen": False}


class MoveInstruction(BaseModel):
    """A single step in a path."""

    to_map: MapId
    exit_cell: int  # cell on current map to click for the transition
    direction: str  # for logging
