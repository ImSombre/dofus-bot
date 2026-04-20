"""Runners pour les métiers d'artisanat (Dofus 2.64).

⚠️ Statut : SCAFFOLD. L'automatisation réelle de l'interface d'artisanat
Dofus nécessite une calibration fine des overlays (atelier, fenêtre de
craft, drag-and-drop des ingrédients) qui dépend de la résolution écran.

La classe `BaseCraftRunner` expose l'API cible. Les 9 métiers d'artisanat
héritent mais ne sont pas encore fonctionnels — chaque run retourne
`calibration_required` pour l'instant.
"""
from src.handlers.jobs.crafting.base_craft import BaseCraftRunner

__all__ = ["BaseCraftRunner"]
