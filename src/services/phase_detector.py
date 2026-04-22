"""Détection de phase de combat Dofus sans LLM — pur analyse d'image.

Objectif : identifier la phase courante (`mon_tour`, `tour_ennemi`,
`popup_victoire`, `popup_defaite`, `hors_combat`) en <20ms, pour ne plus
dépendre du LLM sur des tâches triviales.

Méthodes utilisées :
  1. Bouton "TERMINER LE TOUR" — bas-droite de l'écran, couleur jaune-vert
     vif (~BGR (30, 200, 220) à (80, 255, 255) en version "active").
     → Présent + actif = `mon_tour`.
     → Présent + grisé (saturation faible) = `tour_ennemi`.
     → Absent = `hors_combat` ou popup.
  2. Popup victoire/défaite : zone modale centrale (rectangle autour du
     centre) avec fond sombre semi-transparent et bordure or.
  3. Écran de combat vs map normal : présence de la timeline initiative
     en haut-droite (portraits alignés) = on est en combat.

Toutes les détections sont basées sur des zones pixel fixes + analyses
HSV. Ajuste les ratios selon la résolution.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class PhaseDetectionResult:
    phase: str
    """Une de 'mon_tour', 'tour_ennemi', 'popup_victoire', 'popup_defaite',
    'hors_combat', 'inconnu'."""
    confidence: float
    """0.0 à 1.0"""
    reason: str


# Ratios (0-1) de la zone où chercher le bouton "TERMINER LE TOUR".
# Zone bas-droite de l'écran. Dofus 2.64 standard.
END_TURN_BTN_REGION = (0.78, 0.87, 0.99, 0.95)  # x1, y1, x2, y2 ratios

# Zone central-modal pour détecter popup victoire/défaite
POPUP_REGION = (0.30, 0.20, 0.70, 0.60)

# Ratios de la zone "timeline initiative" en haut-droite
INITIATIVE_REGION = (0.80, 0.02, 0.99, 0.20)


# HSV ranges pour la couleur du bouton "TERMINER LE TOUR"
#   - Actif : jaune-vert vif (h ~25-70 pour couvrir jaune pur → vert jaune)
#   - Grisé : saturation faible, valeur moyenne
BTN_ACTIVE_HSV = ((25, 80, 160), (70, 255, 255))
BTN_GRAY_HSV = ((0, 0, 80), (179, 50, 180))

# Bordure or/brune des popups Dofus victoire/défaite (couleur caractéristique)
# On exige cette couleur en plus des pixels sombres pour éviter les faux positifs
POPUP_BORDER_HSV = ((10, 80, 100), (30, 255, 220))


def _crop_by_ratio(frame: np.ndarray, rect: tuple[float, float, float, float]) -> np.ndarray:
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = rect
    return frame[
        int(y1 * h):int(y2 * h),
        int(x1 * w):int(x2 * w),
    ]


def _mask_in_range(crop_bgr: np.ndarray, low_hsv: tuple, high_hsv: tuple) -> float:
    """Retourne le % de pixels dans la range HSV (0.0-1.0)."""
    if crop_bgr is None or crop_bgr.size == 0:
        return 0.0
    hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(low_hsv, dtype=np.uint8), np.array(high_hsv, dtype=np.uint8))
    total = mask.shape[0] * mask.shape[1]
    return (mask > 0).sum() / max(1, total)


def detect_phase(frame_bgr: np.ndarray) -> PhaseDetectionResult:
    """Analyse de phase rapide (~10-20ms).

    Returns: PhaseDetectionResult avec phase, confiance, raison.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return PhaseDetectionResult("inconnu", 0.0, "frame vide")

    # 1. Zone bouton TERMINER LE TOUR
    btn_crop = _crop_by_ratio(frame_bgr, END_TURN_BTN_REGION)
    active_ratio = _mask_in_range(btn_crop, *BTN_ACTIVE_HSV)
    gray_ratio = _mask_in_range(btn_crop, *BTN_GRAY_HSV)

    # Debug values utiles
    # print(f"btn active={active_ratio:.1%} gray={gray_ratio:.1%}")

    # 2. Zone initiative (haut-droite) : si présente = on est en combat
    init_crop = _crop_by_ratio(frame_bgr, INITIATIVE_REGION)
    # Détecte des portraits = variance élevée de couleurs
    in_combat = False
    if init_crop.size > 0:
        # Simple proxy : variance haute = présence d'éléments UI
        gray = cv2.cvtColor(init_crop, cv2.COLOR_BGR2GRAY)
        variance = float(gray.var())
        in_combat = variance > 600  # empirique

    # 3. Popup modal : grosse zone sombre + BORDURE OR caractéristique Dofus.
    # Le check "pixels sombres" seul = trop de faux positifs (donjons, cavernes).
    # Dofus popup a toujours une bordure dorée → on exige les 2 conditions.
    popup_crop = _crop_by_ratio(frame_bgr, POPUP_REGION)
    dark_popup_ratio = 0.0
    gold_border_ratio = 0.0
    if popup_crop.size > 0:
        gray_center = cv2.cvtColor(popup_crop, cv2.COLOR_BGR2GRAY)
        # Popup = bande horizontale de pixels TRÈS sombres (<35, pas <50)
        dark_popup_ratio = (gray_center < 35).sum() / max(1, gray_center.size)
        # Check bordure or Dofus
        gold_border_ratio = _mask_in_range(popup_crop, *POPUP_BORDER_HSV)

    # --- Décision ---
    # Popup CONFIRMÉ : >55% pixels très sombres ET >3% bordure or présente
    # Seuils stricts pour éviter les faux positifs sur maps sombres / donjons.
    if dark_popup_ratio > 0.55 and gold_border_ratio > 0.03:
        return PhaseDetectionResult(
            "popup_victoire", min(1.0, dark_popup_ratio),
            f"popup confirmé (sombre {dark_popup_ratio:.0%} + bordure or {gold_border_ratio:.1%})",
        )

    # Bouton TERMINER actif = mon_tour
    if active_ratio > 0.05:
        return PhaseDetectionResult(
            "mon_tour", min(1.0, active_ratio * 10),
            f"bouton TERMINER actif ({active_ratio:.1%})",
        )

    # Bouton présent mais grisé = tour_ennemi
    if gray_ratio > 0.25 and in_combat:
        return PhaseDetectionResult(
            "tour_ennemi", 0.7,
            f"bouton TERMINER grisé + combat actif",
        )

    # Combat sans bouton clair = incertain (peut-être transition)
    if in_combat:
        return PhaseDetectionResult(
            "tour_ennemi", 0.5,
            "timeline visible, bouton incertain",
        )

    # Sinon = hors combat
    return PhaseDetectionResult(
        "hors_combat", 0.6,
        "pas de timeline ni bouton terminer",
    )
