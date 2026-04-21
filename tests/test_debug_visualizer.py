"""Tests du debug visualizer."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from src.services.debug_visualizer import (  # noqa: E402
    DebugSnapshot, annotate_frame, cleanup_old_debug_images, save_debug_image,
)


def _make_frame(w: int = 800, h: int = 600) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


def test_annotate_empty_frame_returns_none():
    dbg = DebugSnapshot(frame_bgr=None)
    out = annotate_frame(dbg)
    assert out is None


def test_annotate_basic_scene():
    frame = _make_frame()
    dbg = DebugSnapshot(
        frame_bgr=frame,
        perso_xy=(200, 300),
        enemies=[
            {"x": 400, "y": 300, "hp_pct": 60},
            {"x": 500, "y": 400, "hp_pct": 30},
        ],
        pm_cells=[(250, 300), (300, 300)],
        action_type="cast_spell",
        action_reason="test reason",
        turn_number=2,
        pa_remaining=15,
        phase="mon_tour",
    )
    out = annotate_frame(dbg)
    assert out is not None
    assert out.shape == frame.shape
    # La sortie doit être différente de l'entrée (annotations dessinées)
    assert not np.array_equal(out, frame)


def test_save_debug_image():
    frame = _make_frame()
    dbg = DebugSnapshot(frame_bgr=frame, action_type="test")
    with tempfile.TemporaryDirectory() as tmpdir:
        path = save_debug_image(dbg, directory=tmpdir)
        assert path is not None
        assert path.exists()
        assert path.suffix == ".jpg"


def test_cleanup_old_images():
    with tempfile.TemporaryDirectory() as tmpdir:
        d = Path(tmpdir)
        # Crée 10 fichiers
        for i in range(10):
            (d / f"tick_{i:03d}.jpg").touch()
        deleted = cleanup_old_debug_images(d, keep_last=3)
        remaining = list(d.glob("tick_*.jpg"))
        assert deleted == 7
        assert len(remaining) == 3


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
