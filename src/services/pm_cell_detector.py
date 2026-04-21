"""Détection des cases vertes de déplacement (PM) dans Dofus 2.64.

Quand c'est ton tour, Dofus highlight en VERT les cases où tu peux te déplacer
(1 case = 1 PM). Détection = pixel analysis HSV dans la zone de jeu.

Méthode :
  1. Mask HSV pour isoler les pixels verts PM (H: 50-90, S: 100-255, V: 130-255)
  2. Morphological operations (ouverture/fermeture) pour nettoyer le bruit
  3. Connected components → chaque composant = 1 case
  4. Centre de chaque composant = point cliquable

Retourne une liste de cases pixel cliquables avec leur distance au perso.

Note : fonctionne si la highlight est assez saturée. Si le rendering Dofus
est faible contraste, il faut calibrer les ranges HSV.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from loguru import logger


def _load_calibrated_ranges() -> tuple[np.ndarray, np.ndarray]:
    """Charge les ranges HSV depuis hsv_calibration.json si dispo,
    sinon retourne les défauts."""
    default_low = np.array([45, 100, 120], dtype=np.uint8)
    default_high = np.array([85, 255, 255], dtype=np.uint8)
    try:
        from src.services.hsv_calibrator import CalibrationData  # noqa: PLC0415
        data = CalibrationData.load()
        low, high = data.get_range("pm_cell")
        return (
            np.array(low, dtype=np.uint8),
            np.array(high, dtype=np.uint8),
        )
    except Exception:
        return (default_low, default_high)


# Range HSV pour le vert PM Dofus (chargé depuis la calibration user si dispo)
PM_GREEN_HSV_LOW, PM_GREEN_HSV_HIGH = _load_calibrated_ranges()

# Taille minimale/maximale d'une case de PM détectée (en pixels carrés)
MIN_CELL_AREA = 500    # trop petit = bruit
MAX_CELL_AREA = 20000  # trop gros = erreur fusion de cases


@dataclass
class PmCell:
    """Une case de déplacement (PM) détectée."""
    x: int
    """Centre X pixel (cliquable)."""

    y: int
    """Centre Y pixel."""

    area: int
    """Aire en pixels (aide à filtrer le bruit)."""

    number: int | None = None
    """Numéro de la case (1, 2, 3... pour PM restants) si OCR plus tard."""


def detect_pm_cells(
    frame_bgr: np.ndarray,
    *,
    exclude_ui_regions: bool = True,
) -> list[PmCell]:
    """Détecte toutes les cases de PM visibles sur la frame.

    Args:
        frame_bgr: Capture écran BGR.
        exclude_ui_regions: Si True, ignore les zones UI (barre de sorts bas,
            HP/PA en bas-centre, timeline haut-droite). Évite les faux positifs
            des boutons verts d'interface.

    Returns:
        Liste de cases triées par aire décroissante (cases proches du perso
        sont souvent plus grosses).
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return []

    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, PM_GREEN_HSV_LOW, PM_GREEN_HSV_HIGH)

    if exclude_ui_regions:
        h, w = mask.shape[:2]
        # Masque les zones UI :
        #   - bas de l'écran (barre de sorts) : y > 88% hauteur
        #   - timeline haut-droite : x > 80% et y < 20%
        #   - bouton "TERMINER LE TOUR" : bas-droite
        mask[int(0.88 * h):, :] = 0
        mask[:int(0.20 * h), int(0.80 * w):] = 0

    # Morphological opening (supprime bruit) puis closing (ferme trous)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    # Connected components avec stats
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
        mask, connectivity=8,
    )

    cells: list[PmCell] = []
    for label in range(1, num_labels):  # skip label 0 = background
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area < MIN_CELL_AREA or area > MAX_CELL_AREA:
            continue
        cx, cy = centroids[label]
        cells.append(PmCell(x=int(cx), y=int(cy), area=area))

    # Trie par aire décroissante (cases les plus visibles d'abord)
    cells.sort(key=lambda c: -c.area)
    logger.debug("Cases PM détectées : {}", len(cells))
    return cells


def pick_closest_pm_cell_to_target(
    cells: list[PmCell],
    target_xy: tuple[int, int],
) -> PmCell | None:
    """Parmi les cases de PM, retourne celle la plus proche du `target_xy`."""
    if not cells:
        return None
    return min(
        cells,
        key=lambda c: (c.x - target_xy[0]) ** 2 + (c.y - target_xy[1]) ** 2,
    )


def pick_furthest_pm_cell_from_target(
    cells: list[PmCell],
    target_xy: tuple[int, int],
) -> PmCell | None:
    """Case la plus éloignée du target (utile pour fuite ou prise de distance)."""
    if not cells:
        return None
    return max(
        cells,
        key=lambda c: (c.x - target_xy[0]) ** 2 + (c.y - target_xy[1]) ** 2,
    )
