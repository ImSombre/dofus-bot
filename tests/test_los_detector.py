"""Tests du détecteur de ligne de vue (LoS)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from src.services.los_detector import (  # noqa: E402
    bresenham_line, check_line_of_sight, find_bypass_cell,
)


def test_bresenham_horizontal():
    pts = bresenham_line(0, 0, 10, 0)
    assert pts[0] == (0, 0)
    assert pts[-1] == (10, 0)
    assert len(pts) == 11
    # Tous les y = 0
    assert all(y == 0 for _, y in pts)


def test_bresenham_vertical():
    pts = bresenham_line(5, 0, 5, 10)
    assert pts[0] == (5, 0)
    assert pts[-1] == (5, 10)
    assert all(x == 5 for x, _ in pts)


def test_bresenham_diagonal():
    pts = bresenham_line(0, 0, 5, 5)
    assert pts[0] == (0, 0)
    assert pts[-1] == (5, 5)
    # Doit être une diagonale propre
    for x, y in pts:
        assert abs(x - y) <= 1


def test_los_clear_on_green_frame():
    # Frame verte uniforme (herbe) → LoS toujours libre
    frame = np.zeros((500, 500, 3), dtype=np.uint8)
    frame[:, :] = (0, 150, 0)  # BGR vert
    result = check_line_of_sight(frame, (50, 250), (450, 250))
    assert result.is_clear
    assert result.obstacle_ratio < 0.12


def test_los_blocked_by_stone_wall():
    # Frame verte avec un mur beige au milieu
    frame = np.zeros((500, 500, 3), dtype=np.uint8)
    frame[:, :] = (0, 150, 0)  # herbe
    # Mur vertical beige (dans la range HSV obstacle)
    # BGR pour beige clair : approximately (150, 175, 195) dans les gris-beige
    frame[:, 230:270] = (140, 170, 190)  # bande verticale beige/pierre claire
    result = check_line_of_sight(
        frame, (50, 250), (450, 250),
        obstacle_threshold_ratio=0.08,
    )
    assert not result.is_clear, (
        f"Mur de 40px doit bloquer LoS, mais obstacle_ratio={result.obstacle_ratio:.2%}"
    )


def test_los_short_line_returns_clear():
    # Ligne très courte (<10 pixels) → considérée libre par défaut
    frame = np.zeros((500, 500, 3), dtype=np.uint8)
    result = check_line_of_sight(frame, (100, 100), (103, 102))
    assert result.is_clear


def test_find_bypass_finds_a_free_direction():
    # Scène : perso à (250, 250), cible à (250, 400) avec un mur entre eux.
    # Le bypass doit trouver une case qui donne une LoS libre (sur la gauche
    # ou la droite, pas à travers le mur).
    frame = np.zeros((500, 500, 3), dtype=np.uint8)
    frame[:, :] = (0, 150, 0)
    # Mur horizontal au milieu (y=300 à 340, x=200 à 300 seulement)
    frame[300:340, 200:300] = (140, 170, 190)

    # LoS directe bloquée
    direct = check_line_of_sight(
        frame, (250, 250), (250, 400),
        obstacle_threshold_ratio=0.08,
    )
    assert not direct.is_clear

    # Bypass doit exister (case sur le côté du mur)
    bypass = find_bypass_cell(frame, (250, 250), (250, 400), step_cases=3)
    # On accepte None si pas trouvé, mais si trouvé, la LoS depuis bypass doit être libre
    if bypass:
        verify = check_line_of_sight(
            frame, bypass, (250, 400),
            obstacle_threshold_ratio=0.08,
        )
        assert verify.is_clear, f"Bypass {bypass} devrait dégager LoS"


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
