"""Job configuration models."""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.models.enums import JobType, ResourceType
from src.models.map import MapId


class ResourceSpec(BaseModel):
    type: ResourceType
    cells: list[int] = Field(default_factory=list)


class MapSpec(BaseModel):
    id: MapId
    coords: tuple[int, int]
    resources: list[ResourceSpec] = Field(default_factory=list)


class BankSpec(BaseModel):
    map_id: MapId
    cell: int


class ZoneSpec(BaseModel):
    display_name: str
    job: JobType
    maps: list[MapSpec]
    nearest_bank: BankSpec


class JobConfig(BaseModel):
    """Top-level jobs/zones YAML model."""

    zones: dict[str, ZoneSpec]
