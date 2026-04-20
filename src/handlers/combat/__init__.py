"""Package combat : IA de base + une classe par Dofus class."""
from src.handlers.combat.combat_ai import CombatState, CombatStrategy
from src.handlers.combat.classes.base_class import BaseClassCombat
from src.handlers.combat.classes.iop import IopCombat
from src.handlers.combat.classes.cra import CraCombat
from src.handlers.combat.classes.ecaflip import EcaflipCombat
from src.handlers.combat.classes.eniripsa import EniripsaCombat

__all__ = [
    "CombatState",
    "CombatStrategy",
    "BaseClassCombat",
    "IopCombat",
    "CraCombat",
    "EcaflipCombat",
    "EniripsaCombat",
    "get_class_combat",
]


def get_class_combat(class_id: str):
    """Retourne la classe de combat pour un class_id donné."""
    from src.handlers.combat.classes.stubs import STUBS_BY_ID  # lazy

    mapping = {
        "iop": IopCombat,
        "cra": CraCombat,
        "ecaflip": EcaflipCombat,
        "eniripsa": EniripsaCombat,
    }
    mapping.update(STUBS_BY_ID)
    return mapping.get(class_id.lower())


def classes_implementees() -> list[str]:
    """IDs des classes pleinement implémentées (pas stub)."""
    return ["iop", "cra", "ecaflip", "eniripsa"]
