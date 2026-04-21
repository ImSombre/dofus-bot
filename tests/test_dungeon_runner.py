"""Tests du dungeon runner (config + loading)."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.dungeon_runner_worker import (  # noqa: E402
    DungeonConfig, DungeonTransition, list_available_dungeons,
)


def test_dungeon_config_from_file():
    data = {
        "id": "test",
        "nom": "Test Dungeon",
        "niveau_min": 1,
        "niveau_max": 10,
        "nb_rooms": 3,
        "transitions": [
            {"from_room": 1, "to_room": 2, "direction": "east", "click_xy": None},
            {"from_room": 2, "to_room": 3, "direction": "east", "click_xy": [100, 200]},
        ],
        "boss": {"name": "TestBoss", "room": 3},
    }
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8",
    ) as f:
        json.dump(data, f)
        path = f.name
    try:
        cfg = DungeonConfig.from_file(path)
        assert cfg is not None
        assert cfg.id == "test"
        assert cfg.nb_rooms == 3
        assert len(cfg.transitions) == 2
        assert cfg.transitions[1].click_xy == (100, 200) or cfg.transitions[1].click_xy == [100, 200]
        assert cfg.boss_name == "TestBoss"
    finally:
        Path(path).unlink(missing_ok=True)


def test_dungeon_config_invalid_file():
    cfg = DungeonConfig.from_file("/path/does/not/exist.json")
    assert cfg is None


def test_list_available_dungeons_finds_predefined():
    """Vérifie que les donjons prédéfinis (incarnam, bouftous...) sont chargés."""
    dungeons = list_available_dungeons()
    ids = [d.id for d in dungeons]
    assert "incarnam" in ids, f"Incarnam non trouvé parmi {ids}"


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed, failed = 0, 0
    for t in tests:
        try:
            t()
            print(f"[OK] {t.__name__}")
            passed += 1
        except AssertionError as exc:
            print(f"[FAIL] {t.__name__} : {exc}")
            failed += 1
        except Exception as exc:
            print(f"[CRASH] {t.__name__} : {exc}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{len(tests)} passes, {failed} echecs")
    sys.exit(0 if failed == 0 else 1)
