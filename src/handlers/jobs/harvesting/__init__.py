"""Runners pour les métiers de récolte (Dofus 2.64).

Chaque métier a son module dédié. Ils héritent tous de `HarvestingJobRunner`
qui généralise la logique récolte (scan ressource → clic → attendre fin).
"""
from src.handlers.jobs.harvesting.base_harvesting import HarvestingJobRunner
from src.handlers.jobs.harvesting.lumberjack import LumberjackRunner
from src.handlers.jobs.harvesting.farmer import FarmerRunner
from src.handlers.jobs.harvesting.miner import MinerRunner
from src.handlers.jobs.harvesting.alchemist import AlchemistRunner
from src.handlers.jobs.harvesting.fisherman import FishermanRunner
from src.handlers.jobs.harvesting.hunter import HunterRunner

__all__ = [
    "HarvestingJobRunner",
    "LumberjackRunner",
    "FarmerRunner",
    "MinerRunner",
    "AlchemistRunner",
    "FishermanRunner",
    "HunterRunner",
]


def get_runner_class(metier: str):
    """Retourne la classe runner correspondant au métier (str id EN)."""
    mapping = {
        "lumberjack": LumberjackRunner,
        "farmer": FarmerRunner,
        "miner": MinerRunner,
        "alchemist": AlchemistRunner,
        "fisherman": FishermanRunner,
        "hunter": HunterRunner,
    }
    return mapping.get(metier.lower())
