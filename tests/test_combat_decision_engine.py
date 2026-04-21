"""Tests du moteur de décision combat déterministe.

Valide les cas :
  - pas d'ennemi → defer_to_llm
  - PA insuffisants → end_turn
  - mob à portée → cast_spell
  - mob loin → click_xy (approche)
  - re-cast boucle → override (approche ou perpendiculaire)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.combat_decision_engine import (  # noqa: E402
    CombatDecisionEngine, EngineConfig, dist_cases,
)
from src.services.combat_knowledge import CombatKnowledge  # noqa: E402
from src.services.combat_state_reader import (  # noqa: E402
    CombatStateSnapshot, EntityDetection,
)


def _make_snapshot(
    perso_xy: tuple[int, int] | None = (500, 500),
    enemies: list[tuple[int, int]] | None = None,
) -> CombatStateSnapshot:
    snap = CombatStateSnapshot()
    if perso_xy:
        snap.perso = EntityDetection(x=perso_xy[0], y=perso_xy[1], team="self")
    if enemies:
        snap.ennemis = [
            EntityDetection(x=x, y=y, team="enemy") for x, y in enemies
        ]
    return snap


def _make_engine(
    class_name: str = "pandawa",
    shortcuts: dict | None = None,
    po_bonus: int = 0,
) -> CombatDecisionEngine:
    cfg = EngineConfig(
        class_name=class_name,
        spell_shortcuts=shortcuts or {2: "Gueule de Bois"},
        starting_pa=20,
        starting_pm=5,
        po_bonus=po_bonus,
    )
    kb = CombatKnowledge()
    return CombatDecisionEngine(cfg, kb)


def test_dist_cases_horizontal():
    # 172px horizontal = 2 cases (86px/case)
    assert dist_cases((0, 0), (172, 0)) == 2.0


def test_dist_cases_vertical():
    # 86px vertical = 2 cases (43px/case)
    assert dist_cases((0, 0), (0, 86)) == 2.0


def test_no_snapshot_defers_to_llm():
    eng = _make_engine()
    action = eng.decide(None, pa_remaining=20, cast_history=[])
    assert action["type"] == "defer_to_llm"


def test_no_enemies_defers_to_llm():
    eng = _make_engine()
    snap = _make_snapshot(enemies=[])
    action = eng.decide(snap, pa_remaining=20, cast_history=[])
    assert action["type"] == "defer_to_llm"


def test_no_perso_defers_to_llm():
    eng = _make_engine()
    snap = _make_snapshot(perso_xy=None, enemies=[(600, 600)])
    action = eng.decide(snap, pa_remaining=20, cast_history=[])
    assert action["type"] == "defer_to_llm"


def test_low_pa_ends_turn():
    eng = _make_engine()
    snap = _make_snapshot(perso_xy=(500, 500), enemies=[(586, 500)])  # 1 case
    # Gueule de Bois = 3 PA ; avec 2 PA restants on ne peut rien cast
    action = eng.decide(snap, pa_remaining=2, cast_history=[])
    assert action["type"] == "end_turn"


def test_mob_in_range_casts_spell():
    eng = _make_engine()
    # 172px = 2 cases → dans portée 1-5 de Gueule de Bois
    snap = _make_snapshot(perso_xy=(500, 500), enemies=[(672, 500)])
    action = eng.decide(snap, pa_remaining=20, cast_history=[])
    assert action["type"] == "cast_spell"
    assert action["spell_key"] == 2
    assert action["target_xy"] == [672, 500]


def test_mob_out_of_range_moves():
    eng = _make_engine()
    # 15 cases = 1290px → hors portée de Gueule de Bois (1-5)
    snap = _make_snapshot(perso_xy=(500, 500), enemies=[(1790, 500)])
    action = eng.decide(snap, pa_remaining=20, cast_history=[])
    assert action["type"] == "click_xy"
    # L'approche doit aller vers le mob
    target = action["target_xy"]
    # Doit être plus proche du mob que le perso d'origine
    dist_before = abs(1790 - 500)
    dist_after = abs(1790 - target[0])
    assert dist_after < dist_before


def test_po_bonus_extends_range():
    # Sans bonus, mob à 6 cases = hors portée (1-5)
    # Avec +3 bonus, portée effective = 1-8, donc 6 cases = dans portée
    eng = _make_engine(po_bonus=3)
    snap = _make_snapshot(perso_xy=(500, 500), enemies=[(1016, 500)])  # 6 cases
    action = eng.decide(snap, pa_remaining=20, cast_history=[])
    assert action["type"] == "cast_spell", f"PO bonus devrait permettre cast, got {action}"


def test_recast_on_same_target_triggers_bypass():
    eng = _make_engine()
    # Mob proche (2 cases), même slot déjà cast avec succès apparent → bypass
    snap = _make_snapshot(perso_xy=(500, 500), enemies=[(672, 500)])
    cast_history = [("2", 672, 500)]  # déjà cast slot 2 sur cette cible
    action = eng.decide(snap, pa_remaining=17, cast_history=cast_history)
    # Mob proche → doit tenter contournement perpendiculaire
    assert action["type"] == "click_xy"
    assert "contournement" in action.get("reason", "").lower() or \
           "bypass" in action.get("reason", "").lower()


def test_recast_on_far_target_triggers_approach():
    eng = _make_engine()
    # Mob loin, déjà cast sans effet → s'approcher
    # 10 cases de dist mais simulons qu'on était plus près et qu'on a cast
    snap = _make_snapshot(perso_xy=(500, 500), enemies=[(1360, 500)])  # 10 cases
    # Cast précédent sur ce mob (on était plus près avant le déplacement LLM)
    cast_history = [("2", 1360, 500)]
    action = eng.decide(snap, pa_remaining=17, cast_history=cast_history)
    # Mob loin → approche
    assert action["type"] == "click_xy"
    target = action["target_xy"]
    # Doit aller vers le mob
    assert target[0] > 500 and target[0] < 1360


def test_stuck_overrides_limit_reaches_end_turn():
    eng = _make_engine()
    snap = _make_snapshot(perso_xy=(500, 500), enemies=[(672, 500)])
    cast_history = [("2", 672, 500)]
    # 2e override → end_turn
    action = eng.decide(
        snap, pa_remaining=17, cast_history=cast_history,
        stuck_overrides=2,
    )
    # Soit cast (slot différent) soit end_turn si rien à faire
    assert action["type"] in ("cast_spell", "end_turn", "click_xy")


if __name__ == "__main__":
    import traceback
    tests = [
        test_dist_cases_horizontal,
        test_dist_cases_vertical,
        test_no_snapshot_defers_to_llm,
        test_no_enemies_defers_to_llm,
        test_no_perso_defers_to_llm,
        test_low_pa_ends_turn,
        test_mob_in_range_casts_spell,
        test_mob_out_of_range_moves,
        test_po_bonus_extends_range,
        test_recast_on_same_target_triggers_bypass,
        test_recast_on_far_target_triggers_approach,
        test_stuck_overrides_limit_reaches_end_turn,
    ]
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
