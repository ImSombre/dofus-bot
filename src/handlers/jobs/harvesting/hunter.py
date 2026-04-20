"""Runner Chasseur — bêtes / viande."""
from src.handlers.jobs.harvesting.base_harvesting import HarvestingJobRunner


class HunterRunner(HarvestingJobRunner):
    metier = "hunter"
    animation_duration_sec = 3.0
