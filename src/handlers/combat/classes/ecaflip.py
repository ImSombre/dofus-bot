"""Combat Ecaflip — guerrier à chance, polyvalent mélée/distance.

Stratégie par défaut (règles) :
  - PA >= 5 : Roue de la Fortune (puissant, AoE)
  - PA >= 4 : Griffe Iop (dégâts solides mélée)
  - PA >= 3 : Pile ou Face
  - PA >= 2 : Rekop / tape basique
"""
from src.handlers.combat.classes.base_class import BaseClassCombat
from src.handlers.combat.combat_ai import CombatState


class EcaflipCombat(BaseClassCombat):
    class_id = "ecaflip"
    nom_fr = "Ecaflip"

    SORTS_PRIO = ["roue_fortune", "griffe_iop", "pile_ou_face", "rekop"]

    def doit_se_deplacer(self, state: CombatState) -> bool:
        """L'Eca est polyvalent : se déplace seulement si aucune cible à portée."""
        return state.pm_restants >= 2 and not state.cibles_visibles

    def choisir_sort(self, state: CombatState) -> str | None:
        if not state.cibles_visibles:
            return None
        if state.pa_restants >= 5:
            return "roue_fortune"
        if state.pa_restants >= 4:
            return "griffe_iop"
        if state.pa_restants >= 3:
            return "pile_ou_face"
        if state.pa_restants >= 2:
            return "rekop"
        return None
