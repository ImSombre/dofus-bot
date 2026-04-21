"""Tests du movement_planner."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from src.services.movement_planner import MovementPlan, plan_movement  # noqa: E402


def _make_frame(w: int = 1920, h: int = 1080) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


def _paint_green_square(frame: np.ndarray, cx: int, cy: int, size: int = 60) -> None:
    x1, y1 = cx - size // 2, cy - size // 2
    x2, y2 = cx + size // 2, cy + size // 2
    hsv_green = np.full((1, 1, 3), [60, 200, 200], dtype=np.uint8)
    bgr_green = cv2.cvtColor(hsv_green, cv2.COLOR_HSV2BGR)[0, 0]
    frame[y1:y2, x1:x2] = bgr_green


def test_cast_no_move_when_already_in_range_and_los_clear():
    frame = _make_frame()
    # Perso à (500, 500), mob à (700, 500) = 200px = ~2.3 cases
    # Portée 1-5 → OK. Frame toute noire donc pas d'obstacle (HSV).
    plan = plan_movement(
        frame_bgr=frame,
        perso_xy=(500, 500),
        target_xy=(700, 500),
        spell_po_min=1,
        spell_po_max=5,
        spell_needs_los=True,
        strategy="cast_from_here",
        use_pixel_los=True,
    )
    assert plan.action == "cast_no_move", f"got {plan.action}: {plan.reason}"


def test_move_then_cast_when_out_of_range():
    frame = _make_frame()
    # Mob à 10 cases (860px) → hors portée 1-5
    # On peint des cases vertes entre les 2
    _paint_green_square(frame, 700, 500, size=60)
    _paint_green_square(frame, 800, 500, size=60)
    _paint_green_square(frame, 900, 500, size=60)
    plan = plan_movement(
        frame_bgr=frame,
        perso_xy=(500, 500),
        target_xy=(1360, 500),
        spell_po_min=1,
        spell_po_max=5,
        spell_needs_los=True,
        strategy="cast_from_here",
        use_pixel_los=True,
    )
    # Doit bouger
    assert plan.action in ("move_then_cast", "move_approach"), f"got {plan.action}"
    assert plan.move_target_xy is not None


def test_engage_melee_picks_closest_to_mob():
    frame = _make_frame()
    # Cases vertes à différentes distances du mob (target à 800, 500)
    _paint_green_square(frame, 400, 500, size=60)  # loin
    _paint_green_square(frame, 600, 500, size=60)  # moyen
    _paint_green_square(frame, 750, 500, size=60)  # proche
    plan = plan_movement(
        frame_bgr=frame,
        perso_xy=(500, 500),
        target_xy=(800, 500),
        spell_po_min=1,
        spell_po_max=1,
        spell_needs_los=True,
        strategy="engage_melee",
        use_pixel_los=False,
    )
    # La plus proche du mob
    assert plan.action == "move_approach"
    assert abs(plan.move_target_xy[0] - 750) < 30


def test_flee_picks_furthest_from_mob():
    frame = _make_frame()
    _paint_green_square(frame, 400, 500, size=60)  # proche
    _paint_green_square(frame, 100, 100, size=60)  # loin
    plan = plan_movement(
        frame_bgr=frame,
        perso_xy=(500, 500),
        target_xy=(450, 500),
        spell_po_min=1,
        spell_po_max=5,
        spell_needs_los=False,
        strategy="flee",
        use_pixel_los=False,
    )
    assert plan.action == "move_flee"
    # La plus éloignée du mob (tolérance pixel pour centroid calculé)
    assert plan.move_target_xy is not None
    tx, ty = plan.move_target_xy
    assert abs(tx - 100) < 10 and abs(ty - 100) < 10, f"got {plan.move_target_xy}"


def test_fallback_when_no_pm_cells_detected():
    frame = _make_frame()  # frame noire, pas de cases vertes
    plan = plan_movement(
        frame_bgr=frame,
        perso_xy=(500, 500),
        target_xy=(1500, 500),
        spell_po_min=1,
        spell_po_max=5,
        spell_needs_los=True,
        strategy="cast_from_here",
        use_pixel_los=True,
    )
    # Doit tomber sur fallback (approche linéaire)
    assert plan.action in ("move_approach", "cast_no_move", "end_turn")


def test_flee_strategy_endturn_when_no_cells():
    frame = _make_frame()
    plan = plan_movement(
        frame_bgr=frame,
        perso_xy=(500, 500),
        target_xy=(550, 500),
        strategy="flee",
        use_pixel_los=False,
    )
    # Pas de cases détectées → end_turn (ne peut pas fuir)
    assert plan.action == "end_turn"


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
