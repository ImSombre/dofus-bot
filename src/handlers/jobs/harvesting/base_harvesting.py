"""Runner de base pour tous les métiers de récolte.

Logique commune :
  1. Scanner la map via vision (ColorShape avec HSV du catalogue)
  2. Filtrer par métier + niveau personnage
  3. Cliquer sur la ressource la plus proche
  4. Attendre la fin de l'animation de récolte (~3s)
  5. Boucler

Les classes concrètes (Lumberjack, Farmer…) définissent juste le métier et
éventuellement des overrides spécifiques (zones préférées, filtres).
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from loguru import logger

from src.data.catalog import ResourceEntry, get_catalog
from src.handlers.jobs.base_job_runner import BaseJobRunner

if TYPE_CHECKING:
    from src.models.detection import DetectedObject, Frame


class HarvestingJobRunner(BaseJobRunner):
    """Logique commune aux 6 métiers de récolte."""

    metier: str = ""  # override
    animation_duration_sec: float = 3.0

    def get_farmable_resources(self) -> list[ResourceEntry]:
        """Retourne les ressources du catalogue accessibles au niveau courant."""
        cat = get_catalog()
        return cat.by_niveau_max(self.metier, self.ctx.niveau_personnage)

    def scan_targets(self, frame: "Frame") -> list["DetectedObject"]:
        """Scan la frame et retourne les candidats ressource."""
        resources = self.get_farmable_resources()
        if not resources:
            logger.debug("{} : aucune ressource disponible au niveau {}", self.metier, self.ctx.niveau_personnage)
            return []

        # Phase 1 : detection large par ColorShape
        candidates = self.vision.color_shape.detect(frame)

        # Phase 2 : filtrage par HSV catalog (si ColorShape a retourné trop)
        # Pour l'instant on accepte tous les candidats et on laisse l'OCR
        # tooltip les valider au moment de l'interaction.
        # À terme : match HSV dominant vs catalog.
        return candidates

    def interact_with(self, target: "DetectedObject") -> bool:
        """Clic récolte + attente animation."""
        if target.box is None:
            return False
        cx = target.box.x + target.box.w // 2
        cy = target.box.y + target.box.h // 2

        try:
            self.input.click(cx, cy, button="right")  # Dofus = clic droit pour récolter
            logger.info("{} : récolte lancée à ({}, {})", self.metier, cx, cy)
            time.sleep(self.animation_duration_sec)
            return True
        except Exception as exc:
            logger.warning("{} : échec de l'action : {}", self.metier, exc)
            return False

    def status_fr(self) -> str:
        """Petit résumé FR pour l'UI."""
        cat = get_catalog()
        resources = self.get_farmable_resources()
        noms = ", ".join(r.nom_fr for r in resources[:5])
        if len(resources) > 5:
            noms += f", … ({len(resources)} au total)"
        return f"Métier : {self.metier} — Niveau perso : {self.ctx.niveau_personnage} — Ressources : {noms}"
