"""OCR Tesseract pour lire stats d'item en forgemagie.

Objectif : extraire depuis une zone image (stats de l'item en cours de FM)
les valeurs numériques des stats : Force, Vitalité, Intelligence, etc.

Format typique dans Dofus :
    "+12 Force"
    "+45 Vitalité"
    "+8 % Tacle"

Dépendance optionnelle :
    pip install pytesseract
    + installation binaire Tesseract :
    https://github.com/UB-Mannheim/tesseract/wiki

Sans Tesseract, ce module retourne {} et le FM worker utilisera un mode manuel.

Ressource :
    https://tesseract-ocr.github.io/tessdoc/
    Inkybot utilise la même approche.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from loguru import logger


@dataclass
class StatReading:
    """Une stat lue sur l'item."""
    name: str
    """Nom normalisé : 'Force', 'Vitalité', 'Tacle', etc."""

    value: int
    """Valeur numérique."""

    unit: str = ""
    """'%' si pourcentage, sinon vide."""


# Dictionnaire des stats Dofus 2.64 attendues
# Accents ignorés dans la normalisation
STAT_ALIASES = {
    "force": "Force",
    "vitalite": "Vitalité",
    "intelligence": "Intelligence",
    "chance": "Chance",
    "agilite": "Agilité",
    "sagesse": "Sagesse",
    "tacle": "Tacle",
    "fuite": "Fuite",
    "puissance": "Puissance",
    "do neutre": "Do Neutre",
    "do terre": "Do Terre",
    "do feu": "Do Feu",
    "do air": "Do Air",
    "do eau": "Do Eau",
    "reduction dommages": "Réduction Dommages",
    "critiques": "Coups Critiques",
    "initiative": "Initiative",
    "prospection": "Prospection",
    "do pourcent mele": "Do % Mêlée",
    "do pourcent distance": "Do % Distance",
}

_ACCENT_MAP = str.maketrans("éèêàâîïôöùûüç", "eeeaaiioouuuc")


def _normalize(name: str) -> str:
    return name.lower().translate(_ACCENT_MAP).strip()


# Chemins standards Windows où Tesseract s'installe
_TESSERACT_STANDARD_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    r"C:\Tesseract-OCR\tesseract.exe",
]


def _find_tesseract_binary() -> str | None:
    """Cherche tesseract.exe dans PATH + chemins standards Windows."""
    # 1. PATH système
    import shutil  # noqa: PLC0415
    in_path = shutil.which("tesseract")
    if in_path:
        return in_path
    # 2. Chemins standards (souvent installé sans PATH)
    from pathlib import Path  # noqa: PLC0415
    for candidate in _TESSERACT_STANDARD_PATHS:
        if Path(candidate).exists():
            return candidate
    return None


def _tesseract_available() -> bool:
    try:
        import pytesseract  # noqa: PLC0415
    except ImportError:
        return False
    try:
        # Si Tesseract n'est pas dans PATH, on le configure explicitement
        binary = _find_tesseract_binary()
        if binary:
            pytesseract.pytesseract.tesseract_cmd = binary
        pytesseract.get_tesseract_version()
        return True
    except Exception as exc:
        logger.debug("Tesseract non dispo : {}", exc)
        return False


def _preprocess_for_ocr(img_bgr: np.ndarray) -> np.ndarray:
    """Préprocess pour Tesseract : upscale, grayscale, threshold.

    Meilleure lisibilité OCR sur le rendu Dofus (texte anti-aliased).
    """
    if img_bgr is None or img_bgr.size == 0:
        return img_bgr
    # Upscale x2
    upscaled = cv2.resize(img_bgr, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    # Grayscale
    gray = cv2.cvtColor(upscaled, cv2.COLOR_BGR2GRAY)
    # Adaptive threshold (mieux que fixed pour texte sur fond varié)
    thresh = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY, 11, 2,
    )
    return thresh


def read_text(img_bgr: np.ndarray, lang: str = "fra+eng") -> str:
    """Lit tout le texte d'une image via Tesseract. Retourne '' si indispo."""
    if not _tesseract_available():
        return ""
    try:
        import pytesseract  # noqa: PLC0415
        pre = _preprocess_for_ocr(img_bgr)
        return pytesseract.image_to_string(pre, lang=lang, config="--psm 6")
    except Exception as exc:
        logger.debug("Tesseract échec : {}", exc)
        return ""


def parse_stats(text: str) -> list[StatReading]:
    """Extrait les stats d'un bloc de texte brut.

    Format attendu (Dofus) :
        "+12 Force"
        "- 3 Sagesse"
        "+5 % Tacle"

    Tolère les typos OCR (ex: "Vitalite" sans accent).
    """
    stats: list[StatReading] = []
    if not text:
        return stats

    # Regex pour : [sign][espace][nombre][espace][% optionnel][espace][nom]
    pattern = re.compile(
        r"([+\-])\s*(\d+)\s*(%)?\s*([a-zA-ZÀ-ÿéèêàâîïôöùûüç\s]{2,40})",
        re.MULTILINE,
    )

    for match in pattern.finditer(text):
        sign = 1 if match.group(1) == "+" else -1
        value = int(match.group(2)) * sign
        unit = match.group(3) or ""
        raw_name = match.group(4).strip()
        normalized = _normalize(raw_name)
        # Match fuzzy contre les alias connus
        best_name = None
        for alias, canonical in STAT_ALIASES.items():
            if alias in normalized or normalized in alias:
                best_name = canonical
                break
        if best_name:
            stats.append(StatReading(name=best_name, value=value, unit=unit))

    return stats


def read_item_stats(
    frame_bgr: np.ndarray,
    region: tuple[int, int, int, int] | None = None,
) -> list[StatReading]:
    """Combine read_text + parse_stats sur une frame ou une région.

    Args:
        frame_bgr: Capture écran BGR.
        region: (x1, y1, x2, y2) — zone de l'image où lire. Si None = toute l'image.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return []
    if region:
        x1, y1, x2, y2 = region
        h, w = frame_bgr.shape[:2]
        x1, x2 = max(0, x1), min(w, x2)
        y1, y2 = max(0, y1), min(h, y2)
        crop = frame_bgr[y1:y2, x1:x2]
    else:
        crop = frame_bgr
    text = read_text(crop)
    return parse_stats(text)


def is_available() -> bool:
    """Retourne True si Tesseract est utilisable."""
    return _tesseract_available()
