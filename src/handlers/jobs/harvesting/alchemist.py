"""Runner Alchimiste — plantes et herbes."""
from src.handlers.jobs.harvesting.base_harvesting import HarvestingJobRunner


class AlchemistRunner(HarvestingJobRunner):
    metier = "alchemist"
    animation_duration_sec = 2.0
