"""Tests du détecteur de cases PM."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from src.services.pm_cell_detector import (  # noqa: E402
    PmCell, detect_pm_cells, pick_closest_pm_cell_to_target,
    pick_furthest_pm_cell_from_target,
)


def _make_frame(w: int = 1920, h: int = 1080) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


def _paint_green_square(frame: np.ndarray, cx: int, cy: int, size: int = 40) -> None:
    """Peint une case verte (couleur PM Dofus) au centre (cx, cy)."""
    x1, y1 = cx - size // 2, cy - size // 2
    x2, y2 = cx + size // 2, cy + size // 2
    # Vert PM HSV (60, 200, 200) → BGR approx
    # On utilise la conversion inverse : HSV → BGR
    hsv_green = np.full((1, 1, 3), [60, 200, 200], dtype=np.uint8)
    bgr_green = cv2.cvtColor(hsv_green, cv2.COLOR_HSV2BGR)[0, 0]
    frame[y1:y2, x1:x2] = bgr_green


def test_no_green_no_cells():
    frame = _make_frame()
    cells = detect_pm_cells(frame)
    assert cells == []


def test_detect_single_green_cell():
    frame = _make_frame()
    _paint_green_square(frame, 400, 400, size=60)
    cells = detect_pm_cells(frame)
    assert len(cells) >= 1
    # Le centre doit être proche de (400, 400)
    c = cells[0]
    assert abs(c.x - 400) < 10
    assert abs(c.y - 400) < 10


def test_detect_multiple_cells():
    frame = _make_frame()
    _paint_green_square(frame, 300, 300, size=50)
    _paint_green_square(frame, 500, 300, size=50)
    _paint_green_square(frame, 300, 500, size=50)
    cells = detect_pm_cells(frame)
    assert len(cells) == 3


def test_excludes_ui_regions():
    frame = _make_frame()
    h, w = frame.shape[:2]
    # Peint du vert dans la zone UI basse (doit être ignoré)
    _paint_green_square(frame, w // 2, int(0.95 * h), size=60)
    # Peint du vert dans la zone timeline haut-droite (ignoré)
    _paint_green_square(frame, int(0.9 * w), int(0.10 * h), size=60)
    # Peint du vert dans la zone de jeu (doit être détecté)
    _paint_green_square(frame, w // 2, h // 2, size=60)
    cells = detect_pm_cells(frame, exclude_ui_regions=True)
    # Seule la case centrale doit être détectée
    assert len(cells) == 1
    assert abs(cells[0].y - h // 2) < 30


def test_pick_closest_to_target():
    cells = [
        PmCell(x=100, y=100, area=1000),
        PmCell(x=500, y=500, area=1000),
        PmCell(x=1000, y=1000, area=1000),
    ]
    target = (600, 600)
    closest = pick_closest_pm_cell_to_target(cells, target)
    assert closest.x == 500


def test_pick_furthest_from_target():
    cells = [
        PmCell(x=100, y=100, area=1000),
        PmCell(x=500, y=500, area=1000),
        PmCell(x=1000, y=1000, area=1000),
    ]
    target = (100, 100)
    furthest = pick_furthest_pm_cell_from_target(cells, target)
    assert furthest.x == 1000


def test_filters_too_small_areas():
    frame = _make_frame()
    # Carré trop petit (5x5 = 25 px)
    _paint_green_square(frame, 400, 400, size=5)
    cells = detect_pm_cells(frame)
    # Doit être filtré (MIN_CELL_AREA = 500)
    assert cells == []


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
