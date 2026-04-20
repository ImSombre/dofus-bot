"""Unit tests for the pathfinding service."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.models.map import MapId, MapNode
from src.services.pathfinding import PathfindingService


def _build_service() -> PathfindingService:
    # Linear: a -> b -> c
    nodes: dict[MapId, MapNode] = {
        MapId("a"): MapNode(id=MapId("a"), x=0, y=0, neighbors={"east": MapId("b")}),
        MapId("b"): MapNode(id=MapId("b"), x=1, y=0, neighbors={"east": MapId("c"), "west": MapId("a")}),
        MapId("c"): MapNode(id=MapId("c"), x=2, y=0, neighbors={"west": MapId("b")}),
    }
    return PathfindingService(nodes=nodes)


def test_same_map_returns_empty_path() -> None:
    svc = _build_service()
    assert svc.shortest_path(MapId("a"), MapId("a")) == []


def test_shortest_path_a_to_c() -> None:
    svc = _build_service()
    path = svc.shortest_path(MapId("a"), MapId("c"))
    assert [step.to_map for step in path] == [MapId("b"), MapId("c")]


def test_unknown_map_raises() -> None:
    svc = _build_service()
    with pytest.raises(KeyError):
        svc.shortest_path(MapId("a"), MapId("zzz"))


def test_load_from_missing_file_returns_empty(tmp_path: Path) -> None:
    svc = PathfindingService.load_from_json(tmp_path / "nope.json")
    assert svc.node(MapId("anything")) is None


def test_load_from_json(tmp_path: Path) -> None:
    graph = {
        "nodes": [
            {"id": "m1", "x": 0, "y": 0, "neighbors": {"east": "m2"}},
            {"id": "m2", "x": 1, "y": 0, "neighbors": {"west": "m1"}},
        ]
    }
    p = tmp_path / "g.json"
    p.write_text(json.dumps(graph), encoding="utf-8")
    svc = PathfindingService.load_from_json(p)
    assert svc.node(MapId("m1")) is not None
    assert svc.shortest_path(MapId("m1"), MapId("m2"))[0].to_map == MapId("m2")
