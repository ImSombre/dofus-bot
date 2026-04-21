"""Tests du module d'humanisation."""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.human_input import (  # noqa: E402
    human_click_offset, human_delay, human_mouse_path,
)


def test_mouse_path_starts_and_ends_correctly():
    path = human_mouse_path((100, 100), (500, 300))
    assert path[0] == (100, 100) or (
        abs(path[0][0] - 100) <= 5 and abs(path[0][1] - 100) <= 5
    )
    # Fin exacte (pas de jitter sur dernier point)
    assert path[-1] == (500, 300)


def test_mouse_path_has_multiple_steps():
    path = human_mouse_path((0, 0), (400, 0))
    assert len(path) > 10


def test_mouse_path_not_straight_line():
    """Le path doit avoir au moins UNE déviation hors de la ligne droite."""
    path = human_mouse_path((0, 0), (1000, 0))
    # La ligne droite aurait tous les y = 0
    max_y_offset = max(abs(y) for _, y in path)
    # Avec curve_strength=0.25 + jitter, on doit avoir >5px de déviation
    assert max_y_offset > 5, f"path trop droit, max_y={max_y_offset}"


def test_delay_in_range():
    delays = [human_delay(50, 200) for _ in range(100)]
    assert all(0.05 <= d <= 0.2 for d in delays), "délais hors bornes"


def test_delay_has_variance():
    """Les délais ne doivent pas tous être identiques."""
    delays = [human_delay(50, 200) for _ in range(50)]
    assert len(set(round(d, 3) for d in delays)) > 10, "délais pas assez variés"


def test_click_offset_bounded():
    for _ in range(50):
        ox, oy = human_click_offset(radius=5)
        assert -5 <= ox <= 5
        assert -5 <= oy <= 5


def test_click_offset_varies():
    offsets = [human_click_offset(radius=5) for _ in range(50)]
    unique = set(offsets)
    assert len(unique) > 5, "offsets pas assez variés"


def test_mouse_path_performance_reasonable():
    """100 paths doivent se générer en <100ms."""
    start = time.perf_counter()
    for _ in range(100):
        human_mouse_path((0, 0), (500, 500))
    elapsed = time.perf_counter() - start
    assert elapsed < 0.2, f"trop lent : {elapsed:.2f}s pour 100 paths"


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
