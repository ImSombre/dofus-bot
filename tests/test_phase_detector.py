"""Tests du détecteur de phase."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from src.services.phase_detector import detect_phase  # noqa: E402


def _make_frame(w: int = 1920, h: int = 1080) -> np.ndarray:
    """Frame uniforme noire par défaut."""
    return np.zeros((h, w, 3), dtype=np.uint8)


def test_empty_frame():
    result = detect_phase(None)
    assert result.phase == "inconnu"


def test_dark_popup_detected():
    frame = _make_frame()
    # Crée une popup sombre au centre (zone 30%-70% x, 20%-60% y)
    # En BGR, tout noir = popup dominant
    # Les zones extérieures restent noires aussi, pour ne pas trigger mon_tour
    result = detect_phase(frame)
    # Frame tout noir → popup_victoire (dark_popup_ratio > 30%)
    assert result.phase == "popup_victoire"


def test_mon_tour_when_button_yellow_green():
    frame = _make_frame()
    h, w = frame.shape[:2]
    # Dessine un bouton jaune-vert vif en bas-droite (zone END_TURN_BTN_REGION)
    x1 = int(0.78 * w)
    y1 = int(0.87 * h)
    x2 = int(0.99 * w)
    y2 = int(0.95 * h)
    # Jaune-vert BGR : faible B, haut G, haut R → ex (40, 220, 230)
    frame[y1:y2, x1:x2] = (40, 220, 230)
    # Pour éviter que la popup sombre centrale ne soit détectée, on éclaire le centre
    frame[int(0.20 * h):int(0.60 * h), int(0.30 * w):int(0.70 * w)] = (100, 100, 100)
    result = detect_phase(frame)
    assert result.phase == "mon_tour", f"attendu mon_tour, got {result.phase} ({result.reason})"


def test_tour_ennemi_when_button_gray():
    frame = _make_frame()
    h, w = frame.shape[:2]
    # Bouton grisé en bas-droite
    x1 = int(0.78 * w)
    y1 = int(0.87 * h)
    x2 = int(0.99 * w)
    y2 = int(0.95 * h)
    frame[y1:y2, x1:x2] = (120, 120, 120)  # gris
    # Timeline initiative haut-droite : mettre du bruit/variance
    ix1 = int(0.80 * w)
    iy1 = int(0.02 * h)
    ix2 = int(0.99 * w)
    iy2 = int(0.20 * h)
    frame[iy1:iy2, ix1:ix2] = np.random.randint(0, 255, (iy2 - iy1, ix2 - ix1, 3), dtype=np.uint8)
    # Centre pas popup
    frame[int(0.20 * h):int(0.60 * h), int(0.30 * w):int(0.70 * w)] = (100, 100, 100)
    result = detect_phase(frame)
    assert result.phase == "tour_ennemi", f"attendu tour_ennemi, got {result.phase} ({result.reason})"


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
