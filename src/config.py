"""Configuration management for Sole2Garmin."""

import os
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)


@dataclass
class Config:
    """Application configuration loaded from environment variables."""

    # Sole Credentials & API configuration
    sole_email: str = ""
    sole_password: str = ""
    sole_base_url: str = "https://api.dyaco.com/v1"
    sole_token_dir: Path = Path("data/sole_tokens")

    # Garmin Credentials & Paths
    garmin_username: str = ""
    garmin_password: str = ""
    data_dir: Path = Path("data")
    garmin_token_dir: Path = Path("data/garmin_tokens")

    # Sync Behaviour
    sync_interval_minutes: int = 60
    sync_batch_size: int = 10
    check_duplicates: bool = True
    duplicate_threshold_hours: int = 2

    # Device Spoofing (Default: Garmin Epix Gen 2 for treadmill / indoor running)
    spoof_garmin_device: bool = True
    device_product_id: int = 3943  # 3943 = Epix Gen 2, 4315 = Forerunner 965, 3927 = Edge 1040
    device_software_version: float = 18.16

    # Activity Default Settings
    default_sport: str = "RUNNING"  # RUNNING, CYCLING, FITNESS_EQUIPMENT
    default_sub_sport: str = "TREADMILL"  # TREADMILL, INDOOR_RUNNING, ELLIPTICAL, INDOOR_CYCLING

    # Local sample / drop directory for offline / manual exports
    sample_dir: Path = Path("data/sample_activities")

    # Logging
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        data_dir = Path(os.getenv("DATA_DIR", "data"))
        garmin_token_dir_env = os.getenv("GARMIN_TOKEN_DIR")
        garmin_token_dir = Path(garmin_token_dir_env) if garmin_token_dir_env else data_dir / "garmin_tokens"

        sole_token_dir_env = os.getenv("SOLE_TOKEN_DIR")
        sole_token_dir = Path(sole_token_dir_env) if sole_token_dir_env else data_dir / "sole_tokens"

        sample_dir_env = os.getenv("SAMPLE_DIR")
        sample_dir = Path(sample_dir_env) if sample_dir_env else data_dir / "sample_activities"

        return cls(
            sole_email=os.getenv("SOLE_EMAIL", ""),
            sole_password=os.getenv("SOLE_PASSWORD", ""),
            sole_base_url=os.getenv("SOLE_BASE_URL", "https://api.dyaco.com/v1").rstrip("/"),
            sole_token_dir=sole_token_dir,
            garmin_username=os.getenv("GARMIN_USERNAME", ""),
            garmin_password=os.getenv("GARMIN_PASSWORD", ""),
            data_dir=data_dir,
            garmin_token_dir=garmin_token_dir,
            sync_interval_minutes=int(os.getenv("SYNC_INTERVAL_MINUTES", "60")),
            sync_batch_size=int(os.getenv("SYNC_BATCH_SIZE", "10")),
            check_duplicates=os.getenv("CHECK_DUPLICATES", "true").lower() in ("true", "1", "yes"),
            duplicate_threshold_hours=int(os.getenv("DUPLICATE_THRESHOLD_HOURS", "2")),
            spoof_garmin_device=os.getenv("SPOOF_GARMIN_DEVICE", "true").lower() in ("true", "1", "yes"),
            device_product_id=int(os.getenv("DEVICE_PRODUCT_ID", "3943")),
            device_software_version=float(os.getenv("DEVICE_SOFTWARE_VERSION", "18.16")),
            default_sport=os.getenv("DEFAULT_SPORT", "RUNNING").upper(),
            default_sub_sport=os.getenv("DEFAULT_SUB_SPORT", "TREADMILL").upper(),
            sample_dir=sample_dir,
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )

    def validate(self) -> None:
        """Validate required configuration values."""
        if not self.sole_email and not self.sample_dir.exists():
            logger.warning("SOLE_EMAIL not set. The service requires credentials or local sample activities.")
        if not self.garmin_username and not any(self.garmin_token_dir.glob("*")):
            logger.warning("GARMIN_USERNAME not set and no cached Garmin tokens found.")
