"""Tests combat_profiles."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.combat_profiles import (  # noqa: E402
    CombatProfile, list_available_profiles,
)


def test_profile_from_dict():
    data = {
        "name": "Test",
        "class": "iop",
        "spell_shortcuts": {"1": "spell_a", "2": "spell_b"},
        "rules": [{"name": "r1", "priority": 1}],
        "config": {"starting_pa": 8},
        "description": "desc",
        "author": "me",
    }
    p = CombatProfile.from_dict(data)
    assert p.name == "Test"
    assert p.class_name == "iop"
    assert p.spell_shortcuts == {"1": "spell_a", "2": "spell_b"}
    assert len(p.rules) == 1
    assert p.config["starting_pa"] == 8


def test_profile_spell_shortcuts_as_ints():
    p = CombatProfile(spell_shortcuts={"1": "a", "3": "b", "invalid": "x"})
    result = p.spell_shortcuts_as_ints()
    assert result == {1: "a", 3: "b"}


def test_profile_save_load_roundtrip():
    p = CombatProfile(
        name="Test Profile",
        class_name="pandawa",
        spell_shortcuts={"2": "gueule_de_bois"},
        config={"starting_pa": 10},
        description="Test",
    )
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8",
    ) as f:
        path = f.name
    try:
        p.save(path)
        loaded = CombatProfile.from_file(path)
        assert loaded is not None
        assert loaded.name == p.name
        assert loaded.class_name == p.class_name
        assert loaded.spell_shortcuts == p.spell_shortcuts
    finally:
        Path(path).unlink(missing_ok=True)


def test_profile_from_invalid_file():
    assert CombatProfile.from_file("/path/does/not/exist.json") is None


def test_list_available_profiles_finds_predefined():
    profiles = list_available_profiles()
    names = [p.name for p in profiles]
    # On a créé 3 profils exemples : pandawa, iop, cra
    assert any("Pandawa" in n for n in names), f"Pas de profil Pandawa parmi {names}"
    assert any("Iop" in n for n in names)
    assert any("Cra" in n for n in names)


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
