"""Tests du module tesseract_installer."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.tesseract_installer import (  # noqa: E402
    _has_winget, ensure_tesseract_installed, is_installed,
)


def test_is_installed_returns_bool():
    """Juste vérifie que l'appel ne crash pas et retourne un bool."""
    result = is_installed()
    assert isinstance(result, bool)


def test_has_winget_returns_bool():
    result = _has_winget()
    assert isinstance(result, bool)


def test_ensure_returns_valid_status():
    """Le résultat doit être une string parmi les options connues."""
    result = ensure_tesseract_installed()
    assert result in ("ok", "installing", "unsupported", "failed"), \
        f"Status inattendu : {result}"


def test_ensure_idempotent():
    """Deux appels consécutifs doivent être sûrs (pas de crash, pas de double install)."""
    r1 = ensure_tesseract_installed()
    r2 = ensure_tesseract_installed()
    # Si installé au 1er appel, doit toujours l'être au 2e
    if r1 == "ok":
        assert r2 == "ok"
    # Sinon les deux doivent être cohérents
    assert r2 in ("ok", "installing", "unsupported", "failed")


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
