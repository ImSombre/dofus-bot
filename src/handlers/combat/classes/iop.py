"""Combat Iop — guerrier corps à corps, priorité dégâts mélée."""
from src.handlers.combat.classes.base_class import BaseClassCombat
from src.handlers.combat.combat_ai import CombatState


class IopCombat(BaseClassCombat):
    class_id = "iop"
    nom_fr = "Iop"

    SORTS_PRIO = ["epee_du_jugement", "pression", "bond", "intimidation"]

    def doit_se_deplacer(self, state: CombatState) -> bool:
        return state.pm_restants >= 2 and len(state.cibles_visibles) > 0

    def choisir_sort(self, state: CombatState) -> str | None:
        if not state.cibles_visibles:
            return None
        # Iop : tape d'abord, Bond si ennemi loin
        if state.pa_restants >= 4:
            return "epee_du_jugement"
        if state.pa_restants >= 3:
            return "pression"
        if state.pa_restants >= 3 and state.pm_restants <= 1:
            return "bond"
        return None
