"""Base des runners d'artisanat.

Scaffold : la logique réelle nécessite calibration des overlays.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from src.handlers.jobs.base_job_runner import BaseJobRunner, JobContext

if TYPE_CHECKING:
    from src.models.detection import DetectedObject, Frame


@dataclass
class RecetteCible:
    id: str
    nom_fr: str
    ingredients: list[tuple[str, int]]  # (resource_id, qty)
    metier: str
    niveau_requis: int = 1


class BaseCraftRunner(BaseJobRunner):
    """Runner d'artisanat générique.

    Statuts retournés par tick() :
      - "calibration_required" : l'atelier doit être calibré avant usage
      - "recette_manquante" : aucune recette sélectionnée
      - "ingredients_manquants" : inventaire incomplet
      - "craft_ok:{nom}" : item crafté
    """

    metier: str = ""
    recette_active: RecetteCible | None = None

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

    def set_recette(self, recette: RecetteCible) -> None:
        self.recette_active = recette
        logger.info("Recette active : {}", recette.nom_fr)

    def scan_targets(self, frame: "Frame") -> list["DetectedObject"]:
        # En craft, la "cible" est l'atelier ou le PNJ artisan.
        return []

    def interact_with(self, target: "DetectedObject") -> bool:
        return False

    def tick(self) -> str:
        if self.recette_active is None:
            return "recette_manquante"
        # TODO: vérifier inventaire, ouvrir atelier, placer ingrédients, valider
        return "calibration_required"
