"""Synchronization engine orchestrating ingestion from providers and export to Garmin Connect."""

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.config import Config
from src.fit_builder import FitBuilder
from src.garmin_client import GarminClient, format_local_datetime, parse_activity_datetime
from src.sync_store import SyncStore

logger = logging.getLogger(__name__)


class SyncEngine:
    """Manages activity detection, deduplication, conversion, and upload workflow."""

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

        # Determine activity source provider based on config
        self.source = source or sole
        if self.source is None:
            provider = self.config.ingestion_provider
            logger.info("Initializing activity ingestion provider: '%s'", provider)

            if provider == "strava":
                from src.strava_client import StravaClient
                self.source = StravaClient(
                    client_id=config.strava_client_id,
                    client_secret=config.strava_client_secret,
                    refresh_token=config.strava_refresh_token,
                    token_dir=config.strava_token_dir,
                    filter_sole_only=config.strava_filter_sole_only,
                    sample_dir=config.sample_dir,
                    walk_run_threshold_kmh=config.walk_run_threshold_kmh,
                )
            else:
                # Default: SOLE+ / Dyaco Cloud API
                from src.sole_client import SoleClient
                self.source = SoleClient(
                    email=config.sole_email,
                    password=config.sole_password,
                    base_url=config.sole_base_url,
                    token_dir=config.sole_token_dir,
                    sample_dir=config.sample_dir,
                    walk_run_threshold_kmh=config.walk_run_threshold_kmh,
                )

        self.sole = self.source  # Backward compatibility alias

        self.garmin = garmin or GarminClient(
            username=config.garmin_username,
            password=config.garmin_password,
            token_dir=config.garmin_token_dir,
            user_timezone=config.timezone,
        )
        self.builder = builder or FitBuilder(
            spoof_device=config.spoof_garmin_device,
            device_product_id=config.device_product_id,
            software_version=config.device_software_version,
            user_timezone=config.timezone,
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
        stats = {
            "total_found": 0,
            "uploaded": 0,
            "skipped_duplicate": 0,
            "already_synced": 0,
            "failed": 0,
        }

        # Step 0: Ensure historical / already synced activities have the desired name & sport in Garmin Connect
        if self.config.check_duplicates and not dry_run:
            self.sync_previously_synced_activities()

        logger.info(
            "Starting synchronization cycle (Provider: %s, Batch limit: %d, Dry run: %s)...",
            self.config.ingestion_provider,
            batch_limit,
            dry_run,
        )

        try:
            activities = self.source.get_activities(limit=batch_limit)
        except Exception as exc:
            logger.exception("Failed to retrieve activities from %s: %s", self.config.ingestion_provider, exc)
            return stats

        stats["total_found"] = len(activities)
        if not activities:
            logger.info("No activities returned from %s provider.", self.config.ingestion_provider)
            return stats

        # Sort chronological (oldest first) to sync in logical order
        sorted_activities = sorted(
            activities,
            key=lambda a: parse_activity_datetime(a.get("date")) or datetime.min.replace(tzinfo=timezone.utc),
        )

        for activity in sorted_activities:
            # Auto-reclassify sport and name based on speed threshold
            avg_mps = activity.get("avg_speed_mps")
            if avg_mps is None:
                dist_m = float(activity.get("distance_meters") or 0.0)
                dur_s = float(activity.get("duration_seconds") or 0.0)
                avg_mps = (dist_m / dur_s) if dur_s > 0 else 0.0
            avg_kmh = avg_mps * 3.6

            # Only auto-tag if sport is pedestrian (walking/running)
            current_sport = str(activity.get("sport", "RUNNING")).upper()
            if current_sport in ("WALKING", "RUNNING") and self.config.walk_run_threshold_kmh > 0:
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
                format_local_datetime(act_date, user_tz=self.config.timezone),
                sport,
                sub_sport,
                avg_kmh,
                self.config.walk_run_threshold_kmh,
            )

            # Check 1: Already handled in local database
            if self.store.is_synced(act_id):
                # Ensure previously uploaded activities have the desired title and sport category on Garmin Connect
                if self.config.check_duplicates:
                    self.garmin._try_rename_uploaded_activity(
                        activity_date_str=act_date,
                        desired_title=name,
                        target_sport=sport,
                        retries=1,
                        delay_sec=0,
                        threshold_hours=self.config.duplicate_threshold_hours,
                    )
                logger.info("Activity %s already synced in local database. Skipping.", act_id)
                stats["already_synced"] += 1
                continue

            # Check 2: Duplicate check against Garmin Connect API
            if self.config.check_duplicates:
                try:
                    logger.info(
                        "Checking Garmin Connect for duplicate activities on %s (±%dh window)...",
                        format_local_datetime(act_date, include_time=False, user_tz=self.config.timezone),
                        self.config.duplicate_threshold_hours,
                    )
                    is_dup = self.garmin.check_duplicate(
                        activity_date_str=act_date,
                        activity_name=name,
                        target_sport=sport,
                        threshold_hours=self.config.duplicate_threshold_hours,
                    )
                    if is_dup:
                        logger.warning(
                            "Activity %s already present on Garmin Connect. Marking skipped.",
                            act_id,
                        )
                        if not dry_run:
                            self.store.record_synced(
                                act_id,
                                status="SKIPPED_DUPLICATE",
                                name=name,
                                sport=sport,
                                activity_date=act_date,
                                notes="Duplicate detected on Garmin Connect",
                            )
                            # Ensure the existing activity has the expected title and sport category
                            self.garmin._try_rename_uploaded_activity(
                                activity_date_str=act_date,
                                desired_title=name,
                                target_sport=sport,
                                retries=1,
                                delay_sec=0,
                                threshold_hours=self.config.duplicate_threshold_hours,
                            )
                        stats["skipped_duplicate"] += 1
                        continue
                except Exception as exc:
                    logger.warning("Duplicate check failed (%s); proceeding with upload attempt.", exc)

            if dry_run:
                logger.info("[DRY RUN] Would synthesize and upload FIT file for %s (%s)", act_id, name)
                stats["uploaded"] += 1
                continue

            # Build spoofed FIT file
            fit_file: Optional[str] = None
            try:
                fit_file = self.builder.build_fit_from_activity(activity)

                # Upload to Garmin Connect
                resp = self.garmin.upload_activity(fit_file)
                logger.info("Upload response: %s", resp)

                # Verify & rename/re-type activity on Garmin Connect if needed
                self.garmin._try_rename_uploaded_activity(
                    activity_date_str=act_date,
                    desired_title=name,
                    target_sport=sport,
                    retries=3,
                    delay_sec=4,
                    threshold_hours=self.config.duplicate_threshold_hours,
                )

                # Persist success in database
                self.store.record_synced(
                    act_id,
                    status="UPLOADED",
                    name=name,
                    sport=sport,
                    activity_date=act_date,
                    notes=f"Uploaded to Garmin Connect (Device ID: {self.config.device_product_id})",
                )
                stats["uploaded"] += 1
                logger.info("Successfully handled activity %s: %s", act_id, name)

            except Exception as exc:
                logger.exception("Failed to process activity %s (%s): %s", act_id, name, exc)
                self.store.record_failed(act_id, error_msg=str(exc))
                stats["failed"] += 1

            finally:
                if fit_file and os.path.exists(fit_file):
                    FitBuilder.cleanup_file(fit_file)

        logger.info("--------------------------------------------------")
        logger.info(
            "Sync cycle finished. Found: %d | Uploaded: %d | Skipped (Dup): %d | Already Synced: %d | Failed: %d",
            stats["total_found"],
            stats["uploaded"],
            stats["skipped_duplicate"],
            stats["already_synced"],
            stats["failed"],
        )
        return stats

    def sync_previously_synced_activities(self) -> int:
        """Scan previously synced activities in SQLite and update their titles/types on Garmin Connect."""
        synced_records = self.store.get_all_synced()
        if not synced_records:
            return 0

        logger.info("Checking %d previously synced activities for title/sport updates on Garmin Connect...", len(synced_records))
        updated_count = 0
        for rec in synced_records:
            act_id = rec.get("sole_id")
            name = rec.get("name")
            act_date = rec.get("activity_date")
            sport = rec.get("sport")

            if not act_date:
                continue

            try:
                success = self.garmin._try_rename_uploaded_activity(
                    activity_date_str=act_date,
                    desired_title=name,
                    target_sport=sport,
                    retries=1,
                    delay_sec=0,
                    threshold_hours=self.config.duplicate_threshold_hours,
                )
                if success:
                    updated_count += 1
            except Exception as exc:
                logger.debug("Retroactive update for %s (%s) failed: %s", act_id, name, exc)

        logger.info("Retroactive update check completed. %d activities verified/updated.", updated_count)
        return updated_count
