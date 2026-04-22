"""Tests du générateur de règles depuis replay."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.rule_generator import (  # noqa: E402
    _bucket_dist, _bucket_pa, _extract_turns,
    generate_profile_from_replay,
)


def _write_replay(events: list[dict]) -> Path:
    """Crée un fichier JSONL temporaire avec les events."""
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8",
    )
    for ev in events:
        f.write(json.dumps(ev) + "\n")
    f.close()
    return Path(f.name)


def test_bucket_pa():
    assert _bucket_pa(None) is None
    assert _bucket_pa(1) is None
    assert _bucket_pa(2) == 2
    assert _bucket_pa(5) == 4
    assert _bucket_pa(10) == 6


def test_bucket_dist():
    assert _bucket_dist(None) is None
    assert _bucket_dist(1.0) == 1
    assert _bucket_dist(2.5) == 3
    assert _bucket_dist(4.0) == 5
    assert _bucket_dist(20.0) == 10


def test_extract_turns_by_f1():
    events = [
        {"t": 0.0, "type": "frame", "perso_xy": [500, 500]},
        {"t": 0.5, "type": "key", "key": "é"},
        {"t": 1.0, "type": "click", "x": 600, "y": 500, "button": "left"},
        {"t": 1.5, "type": "key", "key": "Key.f1"},  # fin tour 1
        {"t": 10.0, "type": "frame", "perso_xy": [520, 500]},
        {"t": 10.5, "type": "key", "key": "é"},
        {"t": 11.0, "type": "key", "key": "Key.f1"},  # fin tour 2
    ]
    turns = _extract_turns(events)
    assert len(turns) == 2


def test_extract_turns_by_pause():
    """Pause >8s entre events = tour ennemi = nouveau tour."""
    events = [
        {"t": 0.0, "type": "frame"},
        {"t": 0.5, "type": "key", "key": "é"},
        {"t": 20.0, "type": "frame"},  # gap 19.5s
        {"t": 20.5, "type": "key", "key": "é"},
    ]
    turns = _extract_turns(events)
    assert len(turns) >= 2


def test_generate_profile_simple():
    """Replay avec 3 casts slot 2 sur mob → profil avec 1 règle cast_spell slot 2."""
    events = [
        {"t": 0.0, "type": "session_start"},
        {"t": 1.0, "type": "frame", "perso_xy": [500, 500],
         "enemies": [[700, 500]], "pa_visible": 10},
        {"t": 1.2, "type": "key", "key": "é"},
        {"t": 1.4, "type": "click", "x": 700, "y": 500, "button": "left"},
        {"t": 3.0, "type": "frame", "perso_xy": [500, 500],
         "enemies": [[700, 500]], "pa_visible": 7},
        {"t": 3.2, "type": "key", "key": "é"},
        {"t": 5.0, "type": "frame", "perso_xy": [500, 500],
         "enemies": [[700, 500]], "pa_visible": 4},
        {"t": 5.2, "type": "key", "key": "é"},
        {"t": 7.0, "type": "key", "key": "Key.f1"},
    ]
    path = _write_replay(events)
    try:
        profile = generate_profile_from_replay(
            path,
            class_name="ecaflip",
            spell_shortcuts={2: "pile_ou_face"},
            profile_name="Test",
        )
        assert profile is not None
        assert profile.name == "Test"
        assert profile.class_name == "ecaflip"
        assert len(profile.rules) >= 1
        # Au moins une règle doit cibler slot 2
        has_slot2 = any(
            r.get("action", {}).get("slot") == 2 for r in profile.rules
        )
        assert has_slot2, f"Aucune règle slot 2 dans {profile.rules}"
    finally:
        path.unlink(missing_ok=True)


def test_generate_from_empty_replay():
    path = _write_replay([])
    try:
        profile = generate_profile_from_replay(path, class_name="iop")
        assert profile is None
    finally:
        path.unlink(missing_ok=True)


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
