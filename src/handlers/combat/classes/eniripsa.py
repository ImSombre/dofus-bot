"""Combat Eniripsa — soigneur / support."""
from src.handlers.combat.classes.base_class import BaseClassCombat
from src.handlers.combat.combat_ai import CombatState, CombatStrategy


class EniripsaCombat(BaseClassCombat):
    class_id = "eniripsa"
    nom_fr = "Eniripsa"

    SORTS_PRIO = ["mot_soignant", "mot_curatif", "mot_frappant", "mot_dEscampette"]

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("strategy", CombatStrategy.DEFENSIVE)
        super().__init__(*args, **kwargs)

    def choisir_sort(self, state: CombatState) -> str | None:
        # Soin prioritaire si HP < 70%
        if state.hp_pourcent < 70 and state.pa_restants >= 2:
            return "mot_soignant"
        if state.hp_pourcent < 50 and state.pa_restants >= 4:
            return "mot_curatif"
        # Sinon attaque
        if state.cibles_visibles and state.pa_restants >= 3:
            return "mot_frappant"
        return None
