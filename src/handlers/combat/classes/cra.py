"""Combat Crâ — archer, distance et kite."""
from src.handlers.combat.classes.base_class import BaseClassCombat
from src.handlers.combat.combat_ai import CombatState, CombatStrategy


class CraCombat(BaseClassCombat):
    class_id = "cra"
    nom_fr = "Crâ"

    SORTS_PRIO = ["fleche_magique", "fleche_explosive", "fleche_punitive", "recul"]

    def __init__(self, *args, **kwargs) -> None:
        # Par défaut le Crâ kite
        kwargs.setdefault("strategy", CombatStrategy.KITE)
        super().__init__(*args, **kwargs)

    def doit_se_deplacer(self, state: CombatState) -> bool:
        # Cra garde la distance : bouge si ennemis proches
        return state.pm_restants >= 2

    def choisir_sort(self, state: CombatState) -> str | None:
        if not state.cibles_visibles:
            return None
        if state.pa_restants >= 5:
            return "fleche_explosive"
        if state.pa_restants >= 4:
            return "fleche_punitive"
        if state.pa_restants >= 3:
            return "fleche_magique"
        return None
