"""Tests du moteur de décision v0.6.0.

Couvre :
  - Règles de base (pas de snap / pas d'ennemi / PA faibles)
  - Scoring cibles multi-critères
  - Choix de sort selon distance/PA
  - Bonus PO étend la portée
  - Anti-boucle avec stuck count
  - Buffs en début de combat
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.combat_decision_engine import (  # noqa: E402
    CombatDecisionEngine, DecisionContext, EngineConfig, dist_cases,
)
from src.services.combat_knowledge import CombatKnowledge  # noqa: E402
from src.services.combat_state_reader import (  # noqa: E402
    CombatStateSnapshot, EntityDetection,
)


def _make_snapshot(
    perso_xy: tuple[int, int] | None = (500, 500),
    enemies: list[tuple[int, int]] | None = None,
    hp_perso: int | None = None,
    hp_perso_max: int | None = None,
    enemy_hps: list[int] | None = None,
) -> CombatStateSnapshot:
    snap = CombatStateSnapshot()
    if perso_xy:
        snap.perso = EntityDetection(x=perso_xy[0], y=perso_xy[1], team="self")
    if hp_perso is not None:
        snap.hp_perso = hp_perso
    if hp_perso_max is not None:
        snap.hp_perso_max = hp_perso_max
    if enemies:
        snap.ennemis = [
            EntityDetection(
                x=x, y=y, team="enemy",
                hp_pct=enemy_hps[i] if enemy_hps and i < len(enemy_hps) else None,
            )
            for i, (x, y) in enumerate(enemies)
        ]
    return snap


def _make_engine(
    class_name: str = "pandawa",
    shortcuts: dict | None = None,
    po_bonus: int = 0,
    use_pixel_los: bool = False,  # OFF par défaut pour les tests
) -> CombatDecisionEngine:
    cfg = EngineConfig(
        class_name=class_name,
        spell_shortcuts=shortcuts or {2: "Gueule de Bois"},
        starting_pa=20,
        starting_pm=5,
        po_bonus=po_bonus,
        use_pixel_los=use_pixel_los,
    )
    kb = CombatKnowledge()
    return CombatDecisionEngine(cfg, kb)


def _ctx(
    snap: CombatStateSnapshot | None,
    pa: int = 20,
    history: list | None = None,
    stuck: int = 0,
    turn: int = 1,
) -> DecisionContext:
    return DecisionContext(
        snap=snap,
        pa_remaining=pa,
        cast_history=history or [],
        stuck_overrides=stuck,
        turn_number=turn,
    )


# ---------- Distance ----------

def test_dist_cases_horizontal():
    assert dist_cases((0, 0), (172, 0)) == 2.0


def test_dist_cases_vertical():
    assert dist_cases((0, 0), (0, 86)) == 2.0


# ---------- Defaults LLM ----------

def test_no_snapshot_defers_to_llm():
    eng = _make_engine()
    action = eng.decide(_ctx(None))
    assert action["type"] == "defer_to_llm"


def test_no_enemies_defers_to_llm():
    eng = _make_engine()
    snap = _make_snapshot(enemies=[])
    action = eng.decide(_ctx(snap))
    assert action["type"] == "defer_to_llm"


def test_no_perso_defers_to_llm():
    eng = _make_engine()
    snap = _make_snapshot(perso_xy=None, enemies=[(600, 600)])
    action = eng.decide(_ctx(snap))
    assert action["type"] == "defer_to_llm"


# ---------- PA ----------

def test_low_pa_ends_turn():
    eng = _make_engine()
    snap = _make_snapshot(perso_xy=(500, 500), enemies=[(586, 500)])
    action = eng.decide(_ctx(snap, pa=2))
    assert action["type"] == "end_turn"


# ---------- Cast en portée ----------

def test_mob_in_range_casts_spell():
    eng = _make_engine()
    snap = _make_snapshot(perso_xy=(500, 500), enemies=[(672, 500)])
    action = eng.decide(_ctx(snap, turn=2))  # turn=2 pour skip les buffs
    assert action["type"] == "cast_spell"
    assert action["spell_key"] == 2
    assert action["target_xy"] == [672, 500]


# ---------- Mob hors portée ----------

def test_mob_out_of_range_moves_toward():
    eng = _make_engine()
    snap = _make_snapshot(perso_xy=(500, 500), enemies=[(1790, 500)])
    action = eng.decide(_ctx(snap))
    assert action["type"] == "click_xy"
    target = action["target_xy"]
    # Doit aller vers le mob
    assert target[0] > 500


# ---------- Bonus PO ----------

def test_po_bonus_extends_range():
    eng = _make_engine(po_bonus=3)
    # 6 cases = dans portée (1-5)+3 = (1-8)
    snap = _make_snapshot(perso_xy=(500, 500), enemies=[(1016, 500)])
    action = eng.decide(_ctx(snap, turn=2))
    assert action["type"] == "cast_spell"


# ---------- Anti-boucle ----------

def test_recast_same_target_close_triggers_bypass():
    eng = _make_engine()
    snap = _make_snapshot(perso_xy=(500, 500), enemies=[(672, 500)])
    history = [("2", 672, 500)]
    action = eng.decide(_ctx(snap, pa=17, history=history, turn=2))
    assert action["type"] == "click_xy"


def test_recast_same_target_far_triggers_approach():
    eng = _make_engine()
    snap = _make_snapshot(perso_xy=(500, 500), enemies=[(1360, 500)])
    history = [("2", 1360, 500)]
    action = eng.decide(_ctx(snap, pa=17, history=history, turn=2))
    # Hors portée → approche (pas bypass)
    assert action["type"] == "click_xy"
    target = action["target_xy"]
    assert target[0] > 500


def test_stuck_2_times_ends_turn():
    eng = _make_engine()
    snap = _make_snapshot(perso_xy=(500, 500), enemies=[(672, 500)])
    history = [("2", 672, 500)]
    action = eng.decide(_ctx(snap, pa=17, history=history, stuck=2, turn=2))
    assert action["type"] == "end_turn"


# ---------- Priorisation cibles ----------

def test_targeting_prioritizes_low_hp():
    eng = _make_engine()
    # 2 mobs à distance égale, l'un à 15% HP, l'autre à 80%
    snap = _make_snapshot(
        perso_xy=(500, 500),
        enemies=[(672, 500), (500, 672)],
        enemy_hps=[15, 80],  # premier à 15%, deuxième à 80%
    )
    action = eng.decide(_ctx(snap, turn=2))
    # Doit viser le 1er (15% HP = finish kill)
    assert action["type"] == "cast_spell"
    assert action["target_xy"] == [672, 500]


def test_targeting_prefers_closer():
    eng = _make_engine()
    # Pas d'info HP, donc priorise le plus proche
    snap = _make_snapshot(
        perso_xy=(500, 500),
        enemies=[(1500, 500), (600, 500)],  # 2e est à 1.2 cases, 1er à 12
    )
    action = eng.decide(_ctx(snap, turn=2))
    assert action["type"] == "cast_spell"
    # Cible le plus proche
    assert action["target_xy"] == [600, 500]


# ---------- Fuite HP critique ----------

def test_critical_hp_melee_flees():
    eng = _make_engine()
    # HP 15%, mob au CaC → fuite
    snap = _make_snapshot(
        perso_xy=(500, 500),
        enemies=[(570, 500)],  # <1.5 cases = CaC
        hp_perso=15, hp_perso_max=100,
    )
    action = eng.decide(_ctx(snap))
    # Doit fuir (click_xy en direction opposée)
    assert action["type"] == "click_xy"
    target = action["target_xy"]
    # S'éloigne du mob (mob à droite du perso → perso doit aller à gauche)
    assert target[0] < 500


def test_high_hp_melee_attacks_or_repositions():
    eng = _make_engine()
    # HP 80%, mob au CaC (dist 0.8c < portée_min 1) → pas de fuite,
    # soit cast si possible, soit repositionnement (PAS end_turn)
    snap = _make_snapshot(
        perso_xy=(500, 500),
        enemies=[(570, 500)],
        hp_perso=80, hp_perso_max=100,
    )
    action = eng.decide(_ctx(snap, turn=2))
    # Pas de fuite ni end_turn : soit cast, soit repositionnement offensif
    assert action["type"] in ("cast_spell", "click_xy")
    if action["type"] == "click_xy":
        # Si on repositionne, ça doit être vers le mob (pas fuite)
        target = action["target_xy"]
        # Le mob est à droite → repositionnement doit rester dans cette zone
        assert target[0] >= 400, f"Pas une fuite : target={target}"


# ---------- Runner ----------

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
