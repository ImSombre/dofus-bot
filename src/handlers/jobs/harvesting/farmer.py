"""Runner Paysan — céréales et fibres."""
from src.handlers.jobs.harvesting.base_harvesting import HarvestingJobRunner


class FarmerRunner(HarvestingJobRunner):
    metier = "farmer"
    animation_duration_sec = 2.5  # céréales rapides à faucher
