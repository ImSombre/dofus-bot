"""Tests du système de règles combat configurables."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.combat_rules import (  # noqa: E402
    RuleContext, context_from_snap, evaluate_condition, evaluate_rule,
    find_matching_rule, rule_to_action,
)
from src.services.combat_state_reader import (  # noqa: E402
    CombatStateSnapshot, EntityDetection,
)


def _ctx(**kwargs) -> RuleContext:
    return RuleContext(**kwargs)


# ---------- evaluate_condition ----------

def test_condition_turn_number_eq():
    ctx = _ctx(turn_number=1)
    assert evaluate_condition({"type": "turn_number", "op": "==", "value": 1}, ctx)
    assert not evaluate_condition({"type": "turn_number", "op": "==", "value": 2}, ctx)


def test_condition_pa_remaining_gte():
    ctx = _ctx(pa_remaining=5)
    assert evaluate_condition({"type": "pa_remaining", "op": ">=", "value": 3}, ctx)
    assert not evaluate_condition({"type": "pa_remaining", "op": ">=", "value": 10}, ctx)


def test_condition_enemy_at_range():
    ctx = _ctx(enemies_at_ranges={5: 2, 3: 1})
    assert evaluate_condition(
        {"type": "enemy_at_range", "op": ">=", "value": 1, "range": 5}, ctx,
    )
    assert not evaluate_condition(
        {"type": "enemy_at_range", "op": ">=", "value": 3, "range": 3}, ctx,
    )


def test_condition_unknown_type():
    ctx = _ctx()
    assert not evaluate_condition({"type": "foo", "op": "==", "value": 1}, ctx)


# ---------- evaluate_rule ----------

def test_rule_all_conditions_must_match():
    ctx = _ctx(turn_number=1, pa_remaining=5)
    rule = {
        "name": "test",
        "conditions": [
            {"type": "turn_number", "op": "==", "value": 1},
            {"type": "pa_remaining", "op": ">=", "value": 3},
        ],
    }
    assert evaluate_rule(rule, ctx)


def test_rule_fails_if_one_condition_fails():
    ctx = _ctx(turn_number=1, pa_remaining=2)
    rule = {
        "conditions": [
            {"type": "turn_number", "op": "==", "value": 1},
            {"type": "pa_remaining", "op": ">=", "value": 3},
        ],
    }
    assert not evaluate_rule(rule, ctx)


def test_rule_no_conditions_always_match():
    ctx = _ctx()
    assert evaluate_rule({"conditions": []}, ctx)


# ---------- find_matching_rule ----------

def test_find_matching_rule_uses_priority():
    ctx = _ctx(pa_remaining=10)
    rules = [
        {
            "name": "low",
            "priority": 10,
            "conditions": [{"type": "pa_remaining", "op": ">=", "value": 5}],
            "action": {"type": "wait"},
        },
        {
            "name": "high",
            "priority": 100,
            "conditions": [{"type": "pa_remaining", "op": ">=", "value": 5}],
            "action": {"type": "end_turn"},
        },
    ]
    match = find_matching_rule(rules, ctx)
    assert match["name"] == "high"


def test_find_matching_rule_returns_none_if_no_match():
    ctx = _ctx(pa_remaining=1)
    rules = [
        {"priority": 10, "conditions": [{"type": "pa_remaining", "op": ">=", "value": 10}]},
    ]
    assert find_matching_rule(rules, ctx) is None


# ---------- rule_to_action ----------

def test_rule_to_action_cast_self():
    snap = CombatStateSnapshot()
    snap.perso = EntityDetection(x=500, y=500, team="self")
    rule = {
        "name": "buff",
        "action": {"type": "cast_spell", "slot": 5, "target": "self"},
    }
    ctx = _ctx()
    action = rule_to_action(rule, ctx, snap)
    assert action["type"] == "cast_spell"
    assert action["spell_key"] == 5
    assert action["target_xy"] == [500, 500]


def test_rule_to_action_lowest_hp():
    snap = CombatStateSnapshot()
    snap.perso = EntityDetection(x=500, y=500, team="self")
    snap.ennemis = [
        EntityDetection(x=600, y=500, team="enemy", hp_pct=80),
        EntityDetection(x=700, y=500, team="enemy", hp_pct=20),
    ]
    rule = {
        "action": {"type": "cast_spell", "slot": 2, "target": "lowest_hp"},
    }
    action = rule_to_action(rule, _ctx(), snap)
    # Cible devrait être le mob avec 20% HP
    assert action["target_xy"] == [700, 500]


# ---------- context_from_snap ----------

def test_context_from_snap_computes_distances():
    snap = CombatStateSnapshot()
    snap.perso = EntityDetection(x=500, y=500, team="self")
    snap.ennemis = [
        EntityDetection(x=672, y=500, team="enemy"),  # 2 cases
        EntityDetection(x=1020, y=500, team="enemy"),  # 6 cases
    ]
    ctx = context_from_snap(snap, turn_number=2, pa_remaining=10, pm_remaining=5, buffs_cast=set())
    assert ctx.enemy_count == 2
    assert ctx.nearest_enemy_dist_cases == 2.0
    assert ctx.melee_enemy == 0  # pas CaC
    assert ctx.enemies_at_ranges.get(5) >= 1  # au moins 1 à portée 5


def test_context_melee_detected():
    snap = CombatStateSnapshot()
    snap.perso = EntityDetection(x=500, y=500, team="self")
    snap.ennemis = [EntityDetection(x=580, y=500, team="enemy")]  # <1.5 cases
    ctx = context_from_snap(snap, turn_number=1, pa_remaining=6, pm_remaining=3, buffs_cast=set())
    assert ctx.melee_enemy == 1


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
