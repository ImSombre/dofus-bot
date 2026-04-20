"""Runner Pêcheur — poissons près des points d'eau."""
from src.handlers.jobs.harvesting.base_harvesting import HarvestingJobRunner


class FishermanRunner(HarvestingJobRunner):
    metier = "fisherman"
    animation_duration_sec = 5.0  # la pêche est lente
