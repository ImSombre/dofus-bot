"""Runner Mineur — minerais en grottes / mines."""
from src.handlers.jobs.harvesting.base_harvesting import HarvestingJobRunner


class MinerRunner(HarvestingJobRunner):
    metier = "miner"
    animation_duration_sec = 4.0  # pioche plus lente
