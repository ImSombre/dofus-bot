"""Runner Forgemagie (FM)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from loguru import logger

from src.handlers.jobs.base_job_runner import BaseJobRunner

if TYPE_CHECKING:
    from src.models.detection import DetectedObject, Frame


@dataclass
class CibleFM:
    stat_cible: str  # ex : "pa", "pm", "vie", "force"
    rune_type: str   # ex : "rune_pa_pu", "rune_vie_ra"
    jet_min: int     # seuil accepté
    jet_max: int | None = None  # si on veut un intervalle
    jets_max: int = 1000  # sécurité anti-boucle infinie
    jets_tentes: int = 0
    historique: list[int] = field(default_factory=list)


class ForgemagieRunner(BaseJobRunner):
    """Tente des jets de forgemagie jusqu'à atteindre une cible."""

    metier = "forgemagie"
    cible: CibleFM | None = None

    def set_cible(self, cible: CibleFM) -> None:
        self.cible = cible
        logger.info(
            "FM : cible = {}  (jet >= {}, max {} tentatives)",
            cible.stat_cible, cible.jet_min, cible.jets_max,
        )

    def scan_targets(self, frame: "Frame") -> list["DetectedObject"]:
        return []

    def interact_with(self, target: "DetectedObject") -> bool:
        return False

    def tick(self) -> str:
        if self.cible is None:
            return "cible_manquante"
        if self.cible.jets_tentes >= self.cible.jets_max:
            return "limite_jets_atteinte"
        # TODO : drag rune vers item, valider, lire jet OCR, comparer à cible.
        # Pour l'instant : scaffold, retourne calibration_required.
        return "calibration_required"
