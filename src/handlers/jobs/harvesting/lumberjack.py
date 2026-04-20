"""Runner Bûcheron — récolte des arbres."""
from src.handlers.jobs.harvesting.base_harvesting import HarvestingJobRunner


class LumberjackRunner(HarvestingJobRunner):
    metier = "lumberjack"
    animation_duration_sec = 3.5  # les arbres prennent un peu plus
