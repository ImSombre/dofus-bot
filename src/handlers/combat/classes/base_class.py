"""Template pattern pour les classes Dofus en combat.

Chaque classe (Iop, Cra, ...) hérite et implémente :
  - `choisir_sort()` : quel sort lancer sur quelle cible
  - `doit_se_deplacer()` : faut-il bouger avant d'attaquer
  - Spécifiques : buff, placement, combos

Statut : 3 classes implémentées (Iop, Cra, Eniripsa).
15 autres = stubs avec TODO documentés.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from loguru import logger

from src.handlers.combat.combat_ai import CombatState, CombatStrategy

if TYPE_CHECKING:
    from src.data.catalog import DofusClass
    from src.services.input_service import InputService
    from src.services.vision import MssVisionService


class BaseClassCombat(ABC):
    """Combat IA commun, specialisé par classe."""

    class_id: str = ""
    nom_fr: str = ""

    def __init__(
        self,
        vision: "MssVisionService",
        input_svc: "InputService",
        strategy: CombatStrategy = CombatStrategy.BALANCED,
    ) -> None:
        self.vision = vision
        self.input = input_svc
        self.strategy = strategy
        self.state = CombatState()

    @abstractmethod
    def choisir_sort(self, state: CombatState) -> str | None:
        """Retourne l'id du sort à lancer ou None si rien à faire."""

    def doit_se_deplacer(self, state: CombatState) -> bool:
        return False  # override si classe mobile (Iop, Roublard)

    def jouer_tour(self) -> str:
        """Orchestre un tour de combat."""
        if not self.state.mon_tour:
            return "not_my_turn"
        if self.state.doit_fuir and self.strategy != CombatStrategy.AGGRESSIVE:
            logger.info("HP critique ({:.0f}%), tentative de fuite", self.state.hp_pourcent)
            return "fuite_demandee"

        sort_id = self.choisir_sort(self.state)
        if sort_id is None:
            return "pas_de_sort"
        logger.info("{} lance {}", self.nom_fr, sort_id)
        return f"sort_lance:{sort_id}"
