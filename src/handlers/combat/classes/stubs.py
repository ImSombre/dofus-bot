"""Stubs des 15 autres classes Dofus — chacune retourne un sort générique.

TODO : implémenter la logique de chaque classe (choix de sorts, combos,
placement) dans un fichier dédié (cf. iop.py, cra.py, eniripsa.py comme modèles).
"""
from __future__ import annotations

from src.handlers.combat.classes.base_class import BaseClassCombat
from src.handlers.combat.combat_ai import CombatState


class _StubClass(BaseClassCombat):
    """Fallback minimal : attaque basique si PA disponibles."""

    def choisir_sort(self, state: CombatState) -> str | None:
        if state.cibles_visibles and state.pa_restants >= 3:
            return "attaque_basique"
        return None


def _make(class_id: str, nom_fr: str) -> type:
    """Fabrique dynamique de classes stub."""
    return type(
        f"{nom_fr.capitalize()}Combat",
        (_StubClass,),
        {"class_id": class_id, "nom_fr": nom_fr},
    )


EcaflipCombat = _make("ecaflip", "Ecaflip")
EnutrofCombat = _make("enutrof", "Enutrof")
SramCombat = _make("sram", "Sram")
SadidaCombat = _make("sadida", "Sadida")
OsamodasCombat = _make("osamodas", "Osamodas")
FecaCombat = _make("feca", "Féca")
PandawaCombat = _make("pandawa", "Pandawa")
RoublardCombat = _make("roublard", "Roublard")
ZobalCombat = _make("zobal", "Zobal")
SteamerCombat = _make("steamer", "Steamer")
EliotropeCombat = _make("eliotrope", "Eliotrope")
HuppermageCombat = _make("huppermage", "Huppermage")
OuginakCombat = _make("ouginak", "Ouginak")
ForgelanceCombat = _make("forgelance", "Forgelance")
XelorCombat = _make("xelor", "Xélor")


STUBS_BY_ID = {
    cls.class_id: cls
    for cls in (
        EcaflipCombat, EnutrofCombat, SramCombat, SadidaCombat, OsamodasCombat,
        FecaCombat, PandawaCombat, RoublardCombat, ZobalCombat, SteamerCombat,
        EliotropeCombat, HuppermageCombat, OuginakCombat, ForgelanceCombat, XelorCombat,
    )
}
