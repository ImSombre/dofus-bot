"""Tests FM OCR (parse_stats principalement, Tesseract optionnel)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.fm_ocr import parse_stats, is_available  # noqa: E402


def test_parse_simple_force():
    text = "+12 Force"
    stats = parse_stats(text)
    assert len(stats) == 1
    assert stats[0].name == "Force"
    assert stats[0].value == 12


def test_parse_multiple_stats():
    text = """
    +12 Force
    +45 Vitalité
    +8 Sagesse
    """
    stats = parse_stats(text)
    names = [s.name for s in stats]
    assert "Force" in names
    assert "Vitalité" in names
    assert "Sagesse" in names


def test_parse_with_percent():
    text = "+5 % Tacle"
    stats = parse_stats(text)
    assert len(stats) >= 1
    tacle = next((s for s in stats if s.name == "Tacle"), None)
    assert tacle is not None
    assert tacle.value == 5
    assert tacle.unit == "%"


def test_parse_negative_value():
    text = "- 3 Sagesse"
    stats = parse_stats(text)
    assert len(stats) == 1
    assert stats[0].value == -3


def test_parse_ocr_typo_no_accent():
    """Test tolérance OCR : 'Vitalite' sans accent doit être reconnu."""
    text = "+20 Vitalite"
    stats = parse_stats(text)
    assert len(stats) == 1
    assert stats[0].name == "Vitalité"


def test_parse_empty_text():
    assert parse_stats("") == []
    assert parse_stats(None) == []


def test_is_available_returns_bool():
    # Juste vérifie que ça ne crash pas
    result = is_available()
    assert isinstance(result, bool)


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
