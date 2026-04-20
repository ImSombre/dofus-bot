"""Enumerations used across the bot."""

from __future__ import annotations

from enum import Enum, auto


class BotState(Enum):
    """High-level bot states driven by the state machine."""

    IDLE = auto()
    CALIBRATING = auto()   # NEW — auto-calibration before first run
    STARTING = auto()
    MOVING = auto()
    SCANNING = auto()
    ACTING = auto()
    COMBAT = auto()
    CHECKING_INVENTORY = auto()
    BANKING = auto()
    PAUSED = auto()
    ERROR = auto()
    RECONNECTING = auto()
    STOPPING = auto()


class JobType(str, Enum):
    LUMBERJACK = "lumberjack"
    FARMER = "farmer"
    # MVP only; placeholders for roadmap
    MINER = "miner"
    FISHERMAN = "fisherman"
    ALCHEMIST = "alchemist"


class ResourceType(str, Enum):
    # lumberjack
    FRENE = "frene"
    CHATAIGNIER = "chataignier"
    CHENE = "chene"
    # farmer
    BLE = "ble"
    ORGE = "orge"
    HOUBLON = "houblon"


class BotMode(str, Enum):
    FARM = "farm"
    COMBAT = "combat"
    IDLE = "idle"


class EndReason(str, Enum):
    USER_STOP = "user_stop"
    ERROR = "error"
    STOP_LOSS = "stop_loss"
    CRASH = "crash"
