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

    # Ingestion Provider: "cloud" (Dyaco / SOLE+ API), "strava", or "local" (drop folder)
    ingestion_provider: str = "cloud"

    # Strava API Credentials & Settings (Option 1)
    strava_client_id: str = ""
    strava_client_secret: str = ""
    strava_refresh_token: str = ""
    strava_token_dir: Path = Path("data/strava_tokens")
    strava_filter_sole_only: bool = True

    # Sole / Dyaco Credentials & API configuration (Option 2)
    sole_email: str = ""
    sole_password: str = ""
    sole_base_url: str = "https://sole-homeplus.dyacocloud.com"
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
    default_sport: str = "RUNNING"  # RUNNING, CYCLING, FITNESS_EQUIPMENT, WALKING
    default_sub_sport: str = "TREADMILL"  # TREADMILL, INDOOR_RUNNING, ELLIPTICAL, INDOOR_CYCLING, GENERIC

    # Auto Walk vs Run Threshold (km/h)
    # Workouts with avg speed <= threshold are tagged as Walking (Sport: WALKING, SubSport: GENERIC).
    # Workouts with avg speed > threshold are tagged as Indoor Running (Sport: RUNNING, SubSport: INDOOR_RUNNING).
    # Default is 6.0 km/h. Set to 0 to disable auto-threshold tagging.
    walk_run_threshold_kmh: float = 6.0

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

        strava_token_dir_env = os.getenv("STRAVA_TOKEN_DIR")
        strava_token_dir = Path(strava_token_dir_env) if strava_token_dir_env else data_dir / "strava_tokens"

        sole_token_dir_env = os.getenv("SOLE_TOKEN_DIR")
        sole_token_dir = Path(sole_token_dir_env) if sole_token_dir_env else data_dir / "sole_tokens"

        sample_dir_env = os.getenv("SAMPLE_DIR")
        sample_dir = Path(sample_dir_env) if sample_dir_env else data_dir / "sample_activities"

        def _clean_env(key: str, default: str = "") -> str:
            raw = os.getenv(key, default)
            v = (raw or "").strip()
            if len(v) >= 2 and ((v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'"))):
                return v[1:-1].strip()
            return v

        return cls(
            ingestion_provider=_clean_env("INGESTION_PROVIDER", "cloud").lower(),
            strava_client_id=_clean_env("STRAVA_CLIENT_ID", ""),
            strava_client_secret=_clean_env("STRAVA_CLIENT_SECRET", ""),
            strava_refresh_token=_clean_env("STRAVA_REFRESH_TOKEN", ""),
            strava_token_dir=strava_token_dir,
            strava_filter_sole_only=os.getenv("STRAVA_FILTER_SOLE_ONLY", "true").lower() in ("true", "1", "yes"),
            sole_email=_clean_env("SOLE_EMAIL", ""),
            sole_password=_clean_env("SOLE_PASSWORD", ""),
            sole_base_url=_clean_env("SOLE_BASE_URL", "https://sole-homeplus.dyacocloud.com").rstrip("/"),
            sole_token_dir=sole_token_dir,
            garmin_username=_clean_env("GARMIN_USERNAME", ""),
            garmin_password=_clean_env("GARMIN_PASSWORD", ""),
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
            walk_run_threshold_kmh=float(
                _clean_env("WALK_RUN_THRESHOLD_KMH", _clean_env("WALKING_SPEED_THRESHOLD_KMH", "6.0")) or 6.0
            ),
            sample_dir=sample_dir,
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )

    def validate(self) -> None:
        """Validate required configuration values."""
        if self.ingestion_provider == "strava":
            if not self.strava_refresh_token and not any(self.strava_token_dir.glob("*.json")):
                logger.warning("STRAVA_REFRESH_TOKEN not set. Set STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, and STRAVA_REFRESH_TOKEN.")
        elif self.ingestion_provider == "cloud":
            if not self.sole_email and not self.sample_dir.exists():
                logger.warning("SOLE_EMAIL not set and no sample directory found.")
        if not self.garmin_username and not any(self.garmin_token_dir.glob("*")):
            logger.warning("GARMIN_USERNAME not set and no cached Garmin tokens found.")
