"""Application settings loaded from environment (.env) via pydantic-settings.

All knobs of the bot live here. Never read `os.environ` elsewhere.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed configuration.

    The precedence is (highest → lowest):
        1. CLI arguments (not handled here — handled in main)
        2. Environment variables
        3. `.env` file
        4. Defaults defined below
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # meta
    version: str = "0.1.0"

    # game
    dofus_window_title: str = "Dofus 2.64"
    dofus_executable_path: Path | None = None

    # external tools
    tesseract_path: Path = Field(default=Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"))
    tesseract_lang: str = "fra"
    # Custom tessdata dir (contains fra.traineddata). Empty → use Tesseract bundled tessdata.
    tessdata_dir: Path | None = None

    # persistence
    db_path: Path = Path("./data/bot.sqlite3")
    maps_graph_path: Path = Path("./data/maps_graph.json")
    log_dir: Path = Path("./logs")
    screenshots_dir: Path = Path("./screenshots")

    # calibration
    calibration_data_dir: Path = Path("./data/calibration")

    # YOLO (optional — requires ultralytics installed and model trained)
    # Leave yolo_model_path empty to disable YOLO entirely.
    yolo_model_path: Path | None = None
    yolo_confidence_threshold: float = Field(default=0.5, ge=0.0, le=1.0)

    # OCR
    ocr_lang: str = "fra"  # Tesseract language pack ('fra', 'eng', …)

    # discord
    discord_enabled: bool = False
    discord_token: str = ""
    discord_guild_id: int | None = None
    discord_allowed_user_ids: list[int] = Field(default_factory=list)

    # bot defaults
    default_job: str = "lumberjack"
    default_zone: str = "bonta_forest_sud"
    inventory_full_threshold: float = 0.90
    stop_loss_xp_per_hour: int = 0  # 0 = disabled
    humanize_clicks: bool = True

    # logging
    log_level: str = "INFO"
    log_rotation_mb: int = 50
    log_retention_days: int = 14

    # --- validators ---

    @field_validator("discord_allowed_user_ids", mode="before")
    @classmethod
    def _parse_user_ids(cls, v: object) -> list[int]:
        if isinstance(v, str):
            if not v.strip():
                return []
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        if isinstance(v, list):
            return [int(x) for x in v]
        return []

    @field_validator("inventory_full_threshold")
    @classmethod
    def _threshold_range(cls, v: float) -> float:
        if not 0 < v <= 1:
            raise ValueError("inventory_full_threshold must be in (0, 1]")
        return v

    @field_validator("log_level")
    @classmethod
    def _log_level_valid(cls, v: str) -> str:
        v_upper = v.upper()
        if v_upper not in {"TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"invalid log_level: {v}")
        return v_upper
