"""Détection de l'état de combat Dofus (en combat / mon tour / game over).

Signaux visuels utilisés :
  - Bouton "TERMINER LE TOUR" : visible uniquement en combat à notre tour
  - Zone timeline initiative en haut (liste des participants)
  - Zone des HP/PA/PM du perso (en bas)

Flow type :
    detector = CombatDetector(vision)
    if detector.is_in_combat():
        if detector.is_my_turn():
            # jouer le tour
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import cv2
import numpy as np
from loguru import logger

from src.models.detection import Region
from src.services.vision import MssVisionService


@dataclass
class CombatSnapshot:
    """État visuel d'un combat à un instant T."""
    in_combat: bool = False
    my_turn: bool = False
    raw_ocr_end_turn: str = ""

    @property
    def summary(self) -> str:
        if not self.in_combat:
            return "pas en combat"
        return "mon tour" if self.my_turn else "tour ennemi"


class CombatDetector:
    """Détecte si on est en combat et si c'est notre tour.

    Principe : cherche le bouton "TERMINER LE TOUR" vert-jaune en bas-droite.
    S'il est visible et actif (couleur vive) → c'est notre tour.
    S'il est visible mais grisé → on est en combat mais c'est un autre qui joue.
    S'il est absent → pas en combat (ou écran différent).
    """

    # Zone du bouton "TERMINER LE TOUR" : bas-droite large pour attraper la variance de résolution
    END_TURN_REGION_RATIO = (0.70, 0.84, 0.22, 0.10)  # (x, y, w, h) en ratios

    # Couleur typique du bouton actif (vert-jaune vif) en BGR
    ACTIVE_BUTTON_HSV_HUE_RANGE = (25, 55)  # hue : 25-55 = jaune-vert (plus permissif)
    ACTIVE_BUTTON_MIN_VALUE = 180  # très lumineux
    ACTIVE_BUTTON_MIN_SATURATION = 100

    # Seuil strict pour considérer "en combat" : nécessite OCR confirmation
    MIN_ACTIVE_COLOR_PCT = 0.15  # au moins 15% de la zone en couleur vive

    def __init__(self, vision: MssVisionService) -> None:
        self._vision = vision
        # Cache de la position pixel du bouton fin de tour (calibré au 1er combat valide)
        self._cached_end_turn_center: tuple[int, int] | None = None

    def snapshot(self) -> CombatSnapshot:
        """Capture + analyse. Retourne l'état complet.

        Logique : on considère "en combat" UNIQUEMENT si :
          - Le bouton TERMINER est visible (couleur vive) ET
          - L'OCR trouve "termin" ou "tour" dans la zone.
        Ça évite le faux positif si le serveur privé affiche le bouton en permanence.
        """
        try:
            frame = self._vision.capture()
        except Exception as exc:
            logger.debug("CombatDetector capture échouée : {}", exc)
            return CombatSnapshot()

        h, w = frame.shape[:2]
        rx, ry, rw, rh = self.END_TURN_REGION_RATIO
        region = frame[
            int(h * ry) : int(h * (ry + rh)),
            int(w * rx) : int(w * (rx + rw)),
        ]

        if region.size == 0:
            return CombatSnapshot()

        # Cherche si le bouton "TERMINER LE TOUR" est visible (couleur vert-jaune vive)
        color_ok, color_pct = self._looks_like_end_turn_button(region)

        # OCR pour confirmer le texte "TERMINER" dans la zone
        raw_text = self._ocr_quick(frame, Region(
            x=int(w * rx), y=int(h * ry),
            w=int(w * rw), h=int(h * rh),
        ))
        ocr_ok = bool(re.search(r"(?i)(termin|tour)", raw_text))

        # DEUX signaux requis : couleur vive + OCR "termin/tour"
        # Si seulement la couleur : possible que le bouton soit là hors combat sur certains servs
        in_combat = color_ok and ocr_ok
        is_our_turn = in_combat  # même signal ; à raffiner plus tard (bouton grisé = pas notre tour)

        return CombatSnapshot(
            in_combat=in_combat,
            my_turn=is_our_turn,
            raw_ocr_end_turn=raw_text,
        )

    def find_end_turn_button_center(self, frame: np.ndarray) -> tuple[int, int] | None:
        """Localise le centre exact du bouton 'TERMINER' via HSV.

        Retourne (x, y) en coords de la frame (à ajouter à region.x/y pour écran).
        Utilisé par CombatRunner pour cliquer précisément sur le bon endroit.
        """
        try:
            h, w = frame.shape[:2]
            rx, ry, rw, rh = self.END_TURN_REGION_RATIO
            x0, y0 = int(w * rx), int(h * ry)
            region = frame[y0:y0 + int(h * rh), x0:x0 + int(w * rw)]
            if region.size == 0:
                return None
            hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
            hue_lo, hue_hi = self.ACTIVE_BUTTON_HSV_HUE_RANGE
            lo = np.array([hue_lo, self.ACTIVE_BUTTON_MIN_SATURATION, self.ACTIVE_BUTTON_MIN_VALUE])
            hi = np.array([hue_hi, 255, 255])
            mask = cv2.inRange(hsv, lo, hi)
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not contours:
                return None
            # Le plus grand contour = le bouton (suppose qu'il est le plus gros élément vif dans la zone)
            c = max(contours, key=cv2.contourArea)
            if cv2.contourArea(c) < 500:
                return None
            bx, by, bw, bh = cv2.boundingRect(c)
            cx = x0 + bx + bw // 2
            cy = y0 + by + bh // 2
            return (cx, cy)
        except Exception as exc:
            logger.debug("find_end_turn_button_center échec : {}", exc)
            return None

    def is_in_combat(self) -> bool:
        return self.snapshot().in_combat

    def is_my_turn(self) -> bool:
        return self.snapshot().my_turn

    # ---------- Internals ----------

    def _looks_like_end_turn_button(self, region: np.ndarray) -> tuple[bool, float]:
        """Détecte le vert-jaune vif caractéristique du bouton 'TERMINER LE TOUR'.

        Retourne (visible, pct) — visible True si pct > seuil.
        """
        try:
            hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
            hue_lo, hue_hi = self.ACTIVE_BUTTON_HSV_HUE_RANGE
            lo = np.array([hue_lo, self.ACTIVE_BUTTON_MIN_SATURATION, self.ACTIVE_BUTTON_MIN_VALUE])
            hi = np.array([hue_hi, 255, 255])
            mask = cv2.inRange(hsv, lo, hi)
            pct = float((mask > 0).sum()) / max(mask.size, 1)
            return (pct > self.MIN_ACTIVE_COLOR_PCT, pct)
        except Exception:
            return (False, 0.0)

    def _ocr_quick(self, frame: np.ndarray, region: Region) -> str:
        """OCR léger de la zone bouton. Pour validation."""
        try:
            return (self._vision.read_text(frame, region=region) or "").strip()
        except Exception:
            return ""
