"""Pathfinding — BFS over a static graph of maps."""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path

from loguru import logger

from src.models.map import MapId, MapNode, MoveInstruction


class PathfindingService:
    """In-memory graph of maps with BFS shortest-path."""

    def __init__(self, nodes: dict[MapId, MapNode]) -> None:
        self._nodes = nodes

    # ---------- loading ----------

    @classmethod
    def load_from_json(cls, path: Path) -> PathfindingService:
        """Load graph from a JSON file. Missing file → empty graph (warned)."""
        if not Path(path).exists():
            logger.warning("Maps graph file {} not found — using empty graph.", path)
            return cls(nodes={})
        with Path(path).open("r", encoding="utf-8") as f:
            raw = json.load(f)
        nodes: dict[MapId, MapNode] = {}
        for item in raw.get("nodes", []):
            node = MapNode.model_validate(item)
            nodes[node.id] = node
        logger.info("Pathfinder loaded {} maps", len(nodes))
        return cls(nodes=nodes)

    # ---------- queries ----------

    def node(self, map_id: MapId) -> MapNode | None:
        return self._nodes.get(map_id)

    def shortest_path(self, from_map: MapId, to_map: MapId) -> list[MoveInstruction]:
        """BFS. Returns empty list if same map, or None if unreachable."""
        if from_map == to_map:
            return []
        if from_map not in self._nodes or to_map not in self._nodes:
            raise KeyError(f"Unknown map: {from_map} or {to_map}")

        # BFS tracking parent
        parent: dict[MapId, tuple[MapId, str]] = {}
        visited: set[MapId] = {from_map}
        queue: deque[MapId] = deque([from_map])

        while queue:
            current = queue.popleft()
            if current == to_map:
                break
            node = self._nodes[current]
            for direction, neighbor in node.neighbors.items():
                if neighbor in visited or neighbor not in self._nodes:
                    continue
                visited.add(neighbor)
                parent[neighbor] = (current, direction)
                queue.append(neighbor)

        if to_map not in parent and to_map != from_map:
            raise ValueError(f"No path from {from_map} to {to_map}")

        # Reconstruct
        path: list[MoveInstruction] = []
        current = to_map
        while current != from_map:
            prev, direction = parent[current]
            # exit_cell unknown here without richer graph; placeholder 0.
            # Real implementation stores cells on edges.
            path.append(MoveInstruction(to_map=current, exit_cell=0, direction=direction))
            current = prev
        path.reverse()
        return path
