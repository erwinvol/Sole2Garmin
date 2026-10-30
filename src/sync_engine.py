"""Sync engine orchestrator for Sole2Garmin service."""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.config import Config
from src.fit_builder import FitBuilder
from src.garmin_client import GarminClient, format_local_datetime, parse_activity_datetime
from src.sole_client import SoleClient
from src.strava_client import StravaClient
from src.sync_store import SyncStore

logger = logging.getLogger(__name__)


class SyncEngine:
    """Coordinates fetching from Sole/Strava, FIT generation/patching, and uploading to Garmin Connect."""

    def __init__(
        self,
        config: Config,
        source: Optional[Any] = None,
        sole: Optional[Any] = None,
        garmin: Optional[GarminClient] = None,
        builder: Optional[FitBuilder] = None,
        store: Optional[SyncStore] = None,
    ):
        self.config = config

        # Determine active ingestion client
        active_source = source or sole
        if active_source is None:
            if config.ingestion_provider == "cloud":
                active_source = SoleClient(
                    email=config.sole_email,
                    password=config.sole_password,
                    base_url=config.sole_base_url,
                    token_dir=config.sole_token_dir,
                    sample_dir=config.sample_dir,
                    walk_run_threshold_kmh=config.walk_run_threshold_kmh,
                )
            else:
                active_source = StravaClient(
                    client_id=config.strava_client_id,
                    client_secret=config.strava_client_secret,
                    refresh_token=config.strava_refresh_token,
                    token_dir=config.strava_token_dir,
                    filter_sole_only=config.strava_filter_sole_only,
                    sample_dir=config.sample_dir,
                    walk_run_threshold_kmh=config.walk_run_threshold_kmh,
                )

        self.source = active_source
        self.sole = self.source  # Backward compatibility alias

        self.garmin = garmin or GarminClient(
            username=config.garmin_username,
            password=config.garmin_password,
            token_dir=config.garmin_token_dir,
        )
        self.builder = builder or FitBuilder(
            spoof_device=config.spoof_garmin_device,
            device_product_id=config.device_product_id,
            software_version=config.device_software_version,
        )
        db_path = config.data_dir / "sync_state.db"
        self.store = store or SyncStore(db_path=db_path)

    def run_sync(
        self,
        limit: Optional[int] = None,
        dry_run: bool = False,
        sync_latest: bool = False,
    ) -> Dict[str, int]:
        """Execute a synchronization run.

        Args:
            limit: Maximum activities to inspect (defaults to config.sync_batch_size).
            dry_run: When True, simulates the process without uploading or persisting state.
            sync_latest: When True, strictly targets only the single latest activity.

        Returns:
            Dictionary with counts of processed, uploaded, skipped, and failed activities.
        """
        batch_limit = 1 if sync_latest else (limit or self.config.sync_batch_size)
        logger.info(
            "Starting sync run [Provider: %s] (batch limit: %d, sync_latest: %s, dry_run: %s)...",
            self.config.ingestion_provider.upper(),
            batch_limit,
            sync_latest,
            dry_run,
        )

        stats = {
            "total_found": 0,
            "already_synced": 0,
            "uploaded": 0,
            "skipped_duplicate": 0,
            "failed": 0,
        }

        try:
            activities = self.source.get_activities(limit=batch_limit)
        except Exception as exc:
            logger.exception("Failed to retrieve activities from %s: %s", self.config.ingestion_provider, exc)
            return stats

        stats["total_found"] = len(activities)
        if not activities:
            logger.info("No activities found to sync.")
            return stats

        # Ensure activities are sorted chronologically ascending
        def _get_sort_key(act: Dict[str, Any]) -> float:
            d = act.get("date")
            parsed = parse_activity_datetime(d)
            return parsed.timestamp() if parsed else 0.0

        sorted_activities = sorted(activities, key=_get_sort_key)
        if sync_latest and len(sorted_activities) > 1:
            sorted_activities = [sorted_activities[-1]]

        for activity in sorted_activities:
            # Auto-tag Walk vs Run based on threshold
            dur = activity.get("duration_seconds", 0)
            dist = activity.get("distance_meters", 0)
            avg_kmh = (dist / dur * 3.6) if dur and dur > 0 else 0.0

            if self.config.walk_run_threshold_kmh > 0:
                if avg_kmh <= self.config.walk_run_threshold_kmh:
                    activity["sport"] = "WALKING"
                    activity["sub_sport"] = "GENERIC"
                    activity["name"] = "Sole Treadmill Indoor Walking"
                else:
                    activity["sport"] = "RUNNING"
                    activity["sub_sport"] = "INDOOR_RUNNING"
                    activity["name"] = "Sole Treadmill Indoor Run"

            act_id = activity["id"]
            name = activity["name"]
            act_date = activity["date"]
            sport = activity.get("sport", "RUNNING")
            sub_sport = activity.get("sub_sport", "INDOOR_RUNNING")

            logger.info("--------------------------------------------------")
            logger.info(
                "Evaluating Sole activity: %s (ID: %s, Date: %s, Sport: %s/%s, Speed: %.1f km/h [Threshold: %.1f km/h])",
                name,
                act_id,
                format_local_datetime(act_date),
                sport,
                sub_sport,
                avg_kmh,
                self.config.walk_run_threshold_kmh,
            )

            # Check 1: Already handled in local database
            if self.store.is_synced(act_id):
                logger.info("Activity %s already marked in sync database. Skipping upload.", act_id)
                stats["already_synced"] += 1
                if sync_latest:
                    break
                continue

            if dry_run:
                logger.info("[DRY-RUN] Would process activity %s: %s", act_id, name)
                stats["uploaded"] += 1
                if sync_latest:
                    break
                continue

            generated_path = None
            try:
                # Check 2: Duplicate check on Garmin Connect
                if self.config.check_duplicates:
                    if self.garmin.check_duplicate(
                        activity_date_str=act_date,
                        activity_name=name,
                        target_sport=sport,
                        threshold_hours=self.config.duplicate_threshold_hours,
                    ):
                        logger.warning("Activity %s already present on Garmin Connect. Marking skipped.", act_id)
                        # Ensure existing duplicate activity has desired title
                        self.garmin._try_rename_uploaded_activity(
                            activity_date_str=act_date,
                            desired_title=name,
                            target_sport=sport,
                            retries=1,
                            delay_sec=0,
                            threshold_hours=self.config.duplicate_threshold_hours,
                        )
                        self.store.record_activity(
                            sole_id=act_id,
                            name=name,
                            activity_date=act_date,
                            sport=sport,
                            status="SKIPPED_DUPLICATE",
                            notes="Detected matching activity on Garmin Connect",
                        )
                        stats["skipped_duplicate"] += 1
                        continue

                # Build or enhance FIT file
                generated_path = self.builder.build_fit_from_activity(activity)

                # Upload to Garmin Connect
                upload_res = self.garmin.upload_activity(
                    generated_path,
                    activity_name=name,
                    activity_date_str=act_date,
                    target_sport=sport,
                )
                status_code = upload_res.get("status")

                if status_code == "skipped_duplicate":
                    self.store.record_activity(
                        sole_id=act_id,
                        name=name,
                        activity_date=act_date,
                        sport=sport,
                        status="SKIPPED_DUPLICATE",
                        notes="Garmin returned HTTP 409 Conflict",
                    )
                    stats["skipped_duplicate"] += 1
                else:
                    self.store.record_activity(
                        sole_id=act_id,
                        name=name,
                        activity_date=act_date,
                        sport=sport,
                        status="UPLOADED",
                        notes=f"Device: Epix Gen 2 (Product {self.config.device_product_id})",
                    )
                    stats["uploaded"] += 1

                logger.info("Successfully handled activity %s: %s", act_id, name)

            except Exception as exc:
                logger.exception("Error processing activity %s: %s", act_id, exc)
                stats["failed"] += 1
            finally:
                # Clean up temporary FIT file
                if generated_path and "/tmp/" in str(generated_path):
                    try:
                        import os
                        if os.path.exists(generated_path):
                            os.remove(generated_path)
                    except Exception:
                        pass

            if sync_latest:
                break

        logger.info("==================================================")
        logger.info(
            "Sync run finished. Total: %d, Already Synced: %d, Uploaded: %d, Skipped Duplicates: %d, Failed: %d",
            stats["total_found"],
            stats["already_synced"],
            stats["uploaded"],
            stats["skipped_duplicate"],
            stats["failed"],
        )
        return stats

    def sync_previously_synced_activities(self, limit: int = 50) -> int:
        """Retroactively update titles and categories on Garmin Connect for previously synced activities."""
        logger.info("Checking up to %d previously synced activities to update titles/categories...", limit)
        recent = self.store.get_recent_synced(limit=limit)
        updated_count = 0

        for row in recent:
            sole_id = row.get("sole_id")
            name = row.get("name")
            act_date = row.get("activity_date")
            sport = row.get("sport")

            if not act_date or not name:
                continue

            try:
                success = self.garmin._try_rename_uploaded_activity(
                    activity_date_str=act_date,
                    desired_title=name,
                    target_sport=sport,
                    retries=1,
                    delay_sec=1,
                    threshold_hours=self.config.duplicate_threshold_hours,
                )
                if success:
                    updated_count += 1
            except Exception as exc:
                logger.warning("Could not update title for activity %s: %s", sole_id, exc)

        logger.info("Completed retroactive update. %d activities were processed.", updated_count)
        return updated_count
