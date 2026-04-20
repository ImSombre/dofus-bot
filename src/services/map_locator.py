"""Détection de la map courante via OCR de la bannière top-left de Dofus.

Exemple de bannière :
    Amakna (Champ des Ingalsse)
    9,6, Niveau 30

Parse les coords, le nom de région et le nom de la map.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from loguru import logger

from src.models.detection import Region
from src.services.vision import MssVisionService


# Matche "-9,6" ou "9,-6" ou "(-1,13)"
_COORD_RE = re.compile(r"\(?\s*(-?\d+)\s*,\s*(-?\d+)\s*\)?")
# Tous les nombres signés (pour le parseur lenient qui récupère les digits autour de "Niveau")
_ANY_INT_RE = re.compile(r"-?\d+")
_LEVEL_RE = re.compile(r"[Nn]iveau\s*(\d+)")
_REGION_NAME_RE = re.compile(r"([^(]+?)\s*\(([^)]+)\)")
# Patterns "Niveau" possibles dans l'OCR (Tesseract le lit de 10 façons différentes)
_NIVEAU_PATTERNS = ("iveau", "ivean", "iveou", "ivaau", "iveu", "iveai")


@dataclass
class MapInfo:
    """Position courante du personnage sur la carte Dofus."""
    region: str = ""
    name: str = ""
    x: int | None = None
    y: int | None = None
    level: int | None = None
    raw_ocr: str = ""

    @property
    def coords(self) -> tuple[int, int] | None:
        if self.x is not None and self.y is not None:
            return (self.x, self.y)
        return None

    @property
    def is_valid(self) -> bool:
        return self.x is not None and self.y is not None

    def __str__(self) -> str:
        coord_part = f"({self.x},{self.y})" if self.coords else "?"
        region_part = f"{self.region} - {self.name}" if self.name else self.region
        lvl_part = f" [niv {self.level}]" if self.level else ""
        return f"{region_part} {coord_part}{lvl_part}"


class MapLocator:
    """Lit la bannière de position (haut-gauche) via Tesseract."""

    # Zones OCR : 2 essais seulement (zone normale + zone élargie).
    # Plus de tentatives = trop lent sur du 4K → UI freezait pendant ~30s.
    OCR_REGIONS = [
        (0.00, 0.03, 0.25, 0.10),   # défaut : zone serrée sur la bannière
        (0.00, 0.03, 0.35, 0.15),   # élargi si le texte déborde
    ]
    MAX_ATTEMPTS = 2
    RETRY_DELAY_SEC = 0.25

    def __init__(self, vision: MssVisionService, log_callback=None) -> None:
        self._vision = vision
        self._log_cb = log_callback

    def _log(self, msg: str, level: str = "info") -> None:
        if level == "error":
            logger.error(msg)
        elif level == "warn":
            logger.warning(msg)
        else:
            logger.debug(msg)
        if self._log_cb is not None:
            try:
                self._log_cb(msg, level)
            except Exception:
                pass

    def locate(self) -> MapInfo | None:
        """Retourne MapInfo ou None si parsing échoue.

        Fait jusqu'à MAX_ATTEMPTS captures (avec délai) × plusieurs zones OCR.
        Tesseract est instable sur les petites polices — un retry rapide
        rattrape souvent la lecture.
        """
        import time  # noqa: PLC0415
        last_info: MapInfo | None = None
        all_attempts_debug: list[str] = []

        for attempt in range(self.MAX_ATTEMPTS):
            try:
                frame = self._vision.capture()
            except Exception as exc:
                self._log(f"🔎 OCR : capture échouée — {exc}", "error")
                return None

            h, w = frame.shape[:2]
            if attempt == 0:
                self._log(f"🔎 OCR : capture {w}×{h}", "info")

            for idx, (x0, y0, rw, rh) in enumerate(self.OCR_REGIONS):
                region = Region(
                    x=int(w * x0), y=int(h * y0),
                    w=int(w * rw), h=int(h * rh),
                )
                # OCR avec prétraitement lourd (upscale + binarisation) dédié à la bannière
                text = self._ocr_enhanced(frame, region)

                info = self._parse(text)
                last_info = info
                if info.is_valid:
                    return info
                preview = text.replace("\n", " | ")[:80] if text else "(vide)"
                all_attempts_debug.append(f"tent{attempt}.rég{idx}: '{preview}' → valid={info.is_valid}")

            if attempt < self.MAX_ATTEMPTS - 1:
                time.sleep(self.RETRY_DELAY_SEC)

        # Toutes les tentatives ont échoué — log seulement les 4 derniers essais
        self._log("🔎 OCR : échec après retries — détail des tentatives :", "warn")
        for line in all_attempts_debug[-4:]:
            self._log(f"    {line}", "warn")
        return last_info

    # Flag global : si True, sauvegarde les crops OCR sur disque pour inspection.
    # Utile quand l'utilisateur reporte des échecs OCR et qu'on a besoin de voir
    # ce que Tesseract reçoit réellement.
    _DEBUG_SAVE_CROPS = True
    _DEBUG_DIR = Path("data/ocr_debug")

    def _ocr_coords_tight(self, frame: np.ndarray, full_region: Region) -> str | None:
        """OCR spécialisé : crop ultra-serré sur la ligne des coords uniquement.

        La bannière Dofus a 2 lignes :
          line1 (grande) : "Région (Nom Map)"
          line2 (petite) : "X,Y, Niveau Z"   ← c'est QUE ça qu'on veut lire

        En cropant sur la moitié inférieure + whitelist digits/virgule/tiret/Niveau,
        Tesseract est beaucoup plus précis qu'avec 2 lignes mélangées.
        """
        try:
            # Prend la moitié BASSE de la région (là où se trouvent les coords)
            x, y, w, h = full_region.x, full_region.y, full_region.w, full_region.h
            y_coord = y + int(h * 0.50)  # démarre à mi-hauteur
            h_coord = int(h * 0.60)       # prend 60% de la hauteur
            crop = frame[y_coord : y_coord + h_coord, x : x + w]
            if crop.size == 0:
                return None

            # Prétraitement : gray + upscale 4× + threshold inversé (texte blanc → noir)
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            gray_up = cv2.resize(gray, None, fx=4.0, fy=4.0, interpolation=cv2.INTER_CUBIC)
            _, bin_inv = cv2.threshold(gray_up, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

            if self._DEBUG_SAVE_CROPS:
                try:
                    cv2.imwrite(str(self._DEBUG_DIR / "last_coords_tight.png"), bin_inv)
                except Exception:
                    pass

            # PSM 7 = ligne de texte unique ; whitelist = juste ce qu'il faut pour "X,Y, Niveau Z"
            import pytesseract  # noqa: PLC0415
            whitelist = "0123456789,-NivaueoxfrlS "
            config = f"--psm 7 --oem 3 -c tessedit_char_whitelist={whitelist}"
            try:
                raw = pytesseract.image_to_string(bin_inv, lang="fra", config=config)
            except Exception:
                try:
                    raw = pytesseract.image_to_string(bin_inv, lang="eng", config=config)
                except Exception:
                    return None
            return raw.strip() if raw else None
        except Exception as exc:
            logger.debug("_ocr_coords_tight exception: {}", exc)
            return None

    def _ocr_enhanced(self, frame: np.ndarray, region: Region) -> str:
        """Tente plusieurs variantes de prétraitement et garde le meilleur résultat.

        Variantes :
          A. Grayscale brut (comme vision.read_text) — baseline
          B. Grayscale + upscale 2× (meilleure résolution)
          C. Grayscale + upscale 2× + seuil Otsu direct (texte noir→blanc)
          D. Grayscale + upscale 2× + seuil Otsu inversé (texte blanc→noir)

        Retourne le plus LONG résultat non vide — Tesseract donne parfois 0 chars
        sur une variante et 40 chars sur une autre selon le contraste.
        """
        try:
            x, y, w, h = region.x, region.y, region.w, region.h
            crop = frame[y : y + h, x : x + w]
            if crop.size == 0:
                return ""

            # Sauvegarde crop brut pour debug (one-shot à chaque appel)
            if self._DEBUG_SAVE_CROPS:
                try:
                    self._DEBUG_DIR.mkdir(parents=True, exist_ok=True)
                    abs_path = self._DEBUG_DIR.resolve() / "last_crop.png"
                    cv2.imwrite(str(abs_path), crop)
                    # Log le chemin une seule fois (pour que l'user sache où regarder)
                    if not getattr(self, "_logged_debug_path", False):
                        self._log(f"💾 Debug OCR : {abs_path}", "info")
                        self._logged_debug_path = True
                except Exception:
                    pass

            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            # Upscale 3× + sharpen pour mieux voir les signes - et +
            gray_up = cv2.resize(gray, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
            sharpen_kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)
            sharpened = cv2.filter2D(gray_up, -1, sharpen_kernel)
            _, bin_direct = cv2.threshold(sharpened, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            _, bin_inv = cv2.threshold(sharpened, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

            if self._DEBUG_SAVE_CROPS:
                try:
                    cv2.imwrite(str(self._DEBUG_DIR / "last_gray_up.png"), gray_up)
                    cv2.imwrite(str(self._DEBUG_DIR / "last_bin_inv.png"), bin_inv)
                except Exception:
                    pass

            # Étape 1 : vision.read_text garantit l'init Tesseract + baseline
            try:
                baseline = self._vision.read_text(frame, region=region) or ""
            except Exception:
                baseline = ""

            # Étape 2 : OCR spécialisé sur la ligne des coords (tight crop + PSM 7)
            tight_coords = self._ocr_coords_tight(frame, region)
            if tight_coords:
                # Si le tight a trouvé des chiffres séparés par virgule → utilise-le direct
                if _COORD_RE.search(tight_coords) or _COORD_RE.search(tight_coords.replace(" ", ",")):
                    return tight_coords

            # Étape 3 : variante binaire inversée sur le full crop
            import pytesseract  # noqa: PLC0415
            try:
                raw = pytesseract.image_to_string(bin_inv, lang="fra", config="--psm 6 --oem 3")
                if raw and raw.strip() and "iveau" in raw:
                    return raw
            except Exception:
                raw = ""

            # Combine : ce qu'on a de mieux entre baseline / tight_coords / bin_inv
            candidates = [c for c in (baseline, tight_coords, raw) if c and c.strip()]
            if not candidates:
                return ""
            # Priorise ceux qui contiennent "Niveau" ou un match de coords
            for c in candidates:
                if _COORD_RE.search(c):
                    return c
            # Sinon le plus long
            candidates.sort(key=len, reverse=True)
            return candidates[0]
        except Exception as exc:
            self._log(f"⚠ _ocr_enhanced exception : {exc}", "warn")
            return ""

    # Map de corrections OCR courantes sur les lignes de coords
    # (S/s souvent confondu avec 5, O avec 0, l/I avec 1, ; avec ,)
    _OCR_FIXES = {
        "S": "5", "s": "5", "O": "0", "o": "0",
        "l": "1", "I": "1", "|": "1",
        ";": ",",
    }

    @classmethod
    def _normalize_coord_line(cls, line: str) -> str:
        """Corrige les erreurs OCR courantes UNIQUEMENT sur les sous-chaînes qui
        ressemblent à des coords (avant `Niveau` ou entourées de chiffres/virgules).
        """
        # Si la ligne contient "Niveau" ou "iveau", la partie avant a de bonnes chances
        # d'être les coords. On applique les fixes sur la première moitié.
        if "iveau" in line:
            idx = line.lower().find("iveau")
            head = line[:idx]
            tail = line[idx:]
            fixed = "".join(cls._OCR_FIXES.get(c, c) for c in head)
            return fixed + tail
        return line

    def _parse(self, text: str) -> MapInfo:
        info = MapInfo(raw_ocr=text)
        if not text.strip():
            return info

        # === PASSE 1 : strict (digit,digit) ===
        best_match: tuple[int, int] | None = None
        lines = text.split("\n")
        for line in lines:
            if line.count(".") >= 2:
                continue
            cleaned = self._normalize_coord_line(line)
            for m in _COORD_RE.finditer(cleaned):
                x_val, y_val = int(m.group(1)), int(m.group(2))
                if abs(x_val) > 150 or abs(y_val) > 150:
                    continue
                if "iveau" in line:
                    info.x, info.y = x_val, y_val
                    best_match = (x_val, y_val)
                    break
                elif best_match is None:
                    best_match = (x_val, y_val)

        if info.x is None and best_match is not None:
            info.x, info.y = best_match

        # === PASSE 2 : lenient si passe 1 a échoué ===
        # Cherche "Niveau" (ou variante OCR) puis les 2 derniers entiers AVANT.
        # Gère les cas où Tesseract a remplacé la virgule par un symbole exotique
        # (ex: "0‘13ÆNiveau" → on trouve 0 et 13).
        if info.x is None:
            coords = self._lenient_coord_parse(text)
            if coords is not None:
                info.x, info.y = coords

        # Niveau
        lvl = _LEVEL_RE.search(text)
        if lvl:
            info.level = int(lvl.group(1))

        # Région et nom de map (format "Région (Nom Map)")
        for line in lines:
            line = line.strip()
            if not line:
                continue
            m = _REGION_NAME_RE.search(line)
            if m:
                info.region = m.group(1).strip()
                info.name = m.group(2).strip()
                break

        return info

    @staticmethod
    def _lenient_coord_parse(text: str) -> tuple[int, int] | None:
        """Parse tolérant : cherche 'Niveau' et prend les 2 derniers entiers avant.

        Gère les OCR où la virgule entre X et Y est devenue un caractère exotique
        (quote, symbole accentué, etc.) que le parser strict ignore.
        """
        text_lower = text.lower()
        niveau_idx = -1
        for pattern in _NIVEAU_PATTERNS:
            idx = text_lower.find(pattern)
            if idx > 0:
                niveau_idx = idx
                break
        if niveau_idx < 0:
            return None

        prefix = text[:niveau_idx]
        nums = _ANY_INT_RE.findall(prefix)
        if len(nums) < 2:
            return None

        # Prend les 2 derniers nombres avant "Niveau" = probablement X, Y
        try:
            x_val, y_val = int(nums[-2]), int(nums[-1])
        except ValueError:
            return None

        # Filtre aberrant : coords Dofus ≈ [-80..80]
        if abs(x_val) > 150 or abs(y_val) > 150:
            return None
        return (x_val, y_val)
