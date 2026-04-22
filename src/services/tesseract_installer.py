"""Installation automatique de Tesseract OCR sur Windows.

Si Tesseract est absent de la machine, ce module lance l'installation via winget
en arrière-plan + téléchargement des traineddata fra+eng.

Usage :
    from src.services.tesseract_installer import ensure_tesseract_installed
    status = ensure_tesseract_installed(callback=print)
    if status == "ok":
        # Tesseract est opérationnel
    elif status == "installing":
        # Installation lancée en background, retry plus tard
    elif status == "failed":
        # Installation KO, user doit agir manuellement

Approche :
  - `ensure_tesseract_installed()` : retourne "ok" | "installing" | "failed"
  - `install_async()` : lance subprocess winget en background, non bloquant
  - `download_traineddata()` : télécharge fra.traineddata + eng.traineddata

Fonctionne uniquement sur Windows (winget).
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable

from loguru import logger


WINGET_ID = "UB-Mannheim.TesseractOCR"
DEFAULT_INSTALL_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
TESSDATA_DIR = Path("data/tessdata")
TRAINEDDATA_URL = "https://github.com/tesseract-ocr/tessdata/raw/main"


def is_installed() -> bool:
    """True si Tesseract est trouvable (PATH ou chemins standards)."""
    if shutil.which("tesseract"):
        return True
    candidates = [
        DEFAULT_INSTALL_PATH,
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        r"C:\Tesseract-OCR\tesseract.exe",
    ]
    return any(Path(c).exists() for c in candidates)


def _has_winget() -> bool:
    return shutil.which("winget") is not None


def install_via_winget(timeout_sec: int = 180) -> bool:
    """Lance l'installation via winget. Bloquant.
    Retourne True si succès (exit code 0 + binaire détecté)."""
    if not _has_winget():
        return False
    try:
        proc = subprocess.run(
            [
                "winget", "install", "--id", WINGET_ID,
                "--exact", "--silent",
                "--accept-source-agreements",
                "--accept-package-agreements",
            ],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        logger.info(
            "winget install Tesseract : exit={}, stdout={}, stderr={}",
            proc.returncode, proc.stdout[:300], proc.stderr[:300],
        )
        return is_installed()
    except Exception as exc:
        logger.warning("winget install Tesseract échec : {}", exc)
        return False


def download_traineddata(langs: list[str] | None = None) -> list[str]:
    """Télécharge les traineddata Tesseract dans data/tessdata.
    Retourne la liste des langues installées."""
    if langs is None:
        langs = ["fra", "eng"]
    TESSDATA_DIR.mkdir(parents=True, exist_ok=True)
    installed = []
    try:
        import urllib.request  # noqa: PLC0415
    except ImportError:
        return installed
    for lang in langs:
        dest = TESSDATA_DIR / f"{lang}.traineddata"
        if dest.exists() and dest.stat().st_size > 3 * 1024 * 1024:
            installed.append(lang)
            continue
        url = f"{TRAINEDDATA_URL}/{lang}.traineddata"
        try:
            logger.info("Téléchargement {} …", lang)
            urllib.request.urlretrieve(url, dest)
            installed.append(lang)
        except Exception as exc:
            logger.warning("Download {} échec : {}", lang, exc)
    # Set TESSDATA_PREFIX pour que pytesseract le trouve
    os.environ["TESSDATA_PREFIX"] = str(TESSDATA_DIR.absolute())
    return installed


# État global non bloquant (pour install_async)
_INSTALL_LOCK = threading.Lock()
_INSTALL_IN_PROGRESS = False
_INSTALL_LAST_RESULT: str | None = None


def install_async(callback: Callable[[str], None] | None = None) -> None:
    """Lance l'installation en arrière-plan. Non bloquant.

    Le callback est appelé avec 'ok' | 'failed' à la fin (si fourni).
    """
    global _INSTALL_IN_PROGRESS, _INSTALL_LAST_RESULT

    def _worker():
        global _INSTALL_IN_PROGRESS, _INSTALL_LAST_RESULT
        try:
            if install_via_winget():
                download_traineddata()
                _INSTALL_LAST_RESULT = "ok"
            else:
                _INSTALL_LAST_RESULT = "failed"
        finally:
            _INSTALL_IN_PROGRESS = False
            if callback:
                try:
                    callback(_INSTALL_LAST_RESULT or "failed")
                except Exception:
                    pass

    with _INSTALL_LOCK:
        if _INSTALL_IN_PROGRESS:
            return  # déjà en cours
        _INSTALL_IN_PROGRESS = True
        _INSTALL_LAST_RESULT = None
    t = threading.Thread(target=_worker, daemon=True)
    t.start()


def ensure_tesseract_installed(callback: Callable[[str], None] | None = None) -> str:
    """Vérifie Tesseract et lance install auto si absent.

    Retourne :
      - 'ok'         : Tesseract déjà disponible
      - 'installing' : installation lancée en arrière-plan (callback à la fin)
      - 'unsupported': OS non Windows ou winget absent
      - 'failed'     : échec immédiat (pas pu lancer)
    """
    if platform.system() != "Windows":
        return "unsupported"
    if is_installed():
        # Check traineddata
        tessdata = TESSDATA_DIR
        if not (tessdata / "fra.traineddata").exists():
            download_traineddata()
        return "ok"
    if not _has_winget():
        return "unsupported"
    if _INSTALL_IN_PROGRESS:
        return "installing"
    install_async(callback)
    return "installing"
