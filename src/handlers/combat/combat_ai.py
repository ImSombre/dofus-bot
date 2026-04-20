"""IA de combat partagée entre toutes les classes."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class CombatStrategy(str, Enum):
    AGGRESSIVE = "aggressif"       # max dégâts, peu de kite
    DEFENSIVE = "defensif"         # priorité soin/esquive, kite
    KITE = "kite"                  # garde distance, tir lointain
    BALANCED = "equilibre"         # auto : choisit selon HP/PA
    STATIC = "statique"            # ne bouge pas, utile pour test


@dataclass
class CombatState:
    """État courant du combat (mis à jour chaque tick via OCR)."""
    en_combat: bool = False
    tour_courant: int = 0
    mon_tour: bool = False
    pa_restants: int = 0
    pm_restants: int = 0
    hp_pourcent: float = 100.0
    cibles_visibles: list[str] = field(default_factory=list)  # monster ids
    positions: dict[str, tuple[int, int]] = field(default_factory=dict)

    @property
    def doit_fuir(self) -> bool:
        return self.hp_pourcent < 25.0

    @property
    def tour_resume(self) -> str:
        return f"Tour {self.tour_courant} | PA {self.pa_restants} PM {self.pm_restants} | HP {self.hp_pourcent:.0f}%"
