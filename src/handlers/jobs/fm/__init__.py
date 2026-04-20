"""Runner Forgemagie (FM) — scaffold.

La FM = ajustement statistique d'items via runes. Nécessite :
  - détection de l'item équipé dans l'atelier
  - choix de la rune à injecter (Pa, Pm, Force, etc.)
  - seuil de jet acceptable (float)
  - logique de "tenter un jet" jusqu'à atteindre la cible

Scaffold actif, exécution réelle nécessite calibration UI FM.
"""
from src.handlers.jobs.fm.fm_runner import ForgemagieRunner

__all__ = ["ForgemagieRunner"]
