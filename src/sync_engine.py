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

        # Step 1: Fetch activities from active provider
        try:
            query_limit = 5 if sync_latest else batch_limit
            activities = self.source.get_activities(limit=query_limit)
        except Exception as exc:
            logger.exception("Failed to retrieve activities from %s: %s", self.config.ingestion_provider, exc)
            stats["failed"] += 1
            return stats

        activities.sort(
            key=lambda a: parse_activity_datetime(a.get("date")) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

        if sync_latest:
            activities = activities[:1]
        elif batch_limit and len(activities) > batch_limit:
            activities = activities[:batch_limit]

        stats["total_found"] = len(activities)
        if not activities:
            logger.info("No new activities returned from %s provider.", self.config.ingestion_provider)
            if not dry_run and not sync_latest:
                try:
                    self.sync_previously_synced_activities(limit=self.config.sync_batch_size * 2)
                except Exception as exc:
                    logger.warning("Error verifying previously synced activities: %s", exc)
            return stats

        activities_to_process = activities if sync_latest else list(reversed(activities))

        for activity in activities_to_process:
            # Auto Walk vs Run threshold tagging
            avg_mps = float(activity.get("avg_speed_mps") or 0.0)
            if avg_mps <= 0 and activity.get("duration_seconds", 0) > 0:
                avg_mps = float(activity.get("distance_meters", 0.0)) / float(activity["duration_seconds"])
            avg_kmh = avg_mps * 3.6

            current_sport = str(activity.get("sport", "RUNNING")).upper()
            if current_sport in ("RUNNING", "WALKING"):
                if self.config.walk_run_threshold_kmh > 0 and avg_kmh <= self.config.walk_run_threshold_kmh:
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
                if generated_path:
                    raw_orig = activity.get("raw_fit_path")
                    if not raw_orig or generated_path != raw_orig:
                        self.builder.cleanup_file(generated_path)

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

        if not dry_run and not sync_latest:
            try:
                self.sync_previously_synced_activities(limit=self.config.sync_batch_size * 2)
            except Exception as exc:
                logger.warning("Error verifying previously synced activities: %s", exc)

        return stats

    def sync_previously_synced_activities(self, limit: int = 20) -> int:
        """Retroactively update titles and sport categories on Garmin Connect for previously synced activities.

        Returns:
            Count of activities verified/updated.
        """
        if not self.config.check_duplicates:
            return 0

        synced_records = self.store.get_all_synced(limit=limit)
        if not synced_records:
            return 0

        logger.info("Verifying %d previously recorded activities in Garmin Connect for correct titles...", len(synced_records))
        updated_count = 0

        # Pre-fetch source activities to determine genuine speed, sport, and titles
        source_activities = {}
        try:
            raw_activities = self.source.get_activities(limit=max(limit, 50))
            source_activities = {str(a.get("id")): a for a in raw_activities if a.get("id")}
        except Exception as exc:
            logger.debug("Could not pre-fetch source activities for title verification: %s", exc)

        for record in synced_records:
            sole_id = str(record["sole_id"])
            act_date = record.get("activity_date")
            if not act_date:
                continue

            current_sport = str(record.get("sport") or "RUNNING").upper()
            stored_name = record.get("name") or ""

            act = source_activities.get(sole_id)
            if act:
                if float(act.get("avg_speed_mps", 0.0)) > 0:
                    avg_mps = float(act["avg_speed_mps"])
                elif float(act.get("duration_seconds", 0.0)) > 0:
                    avg_mps = float(act.get("distance_meters", 0.0)) / float(act["duration_seconds"])
                else:
                    avg_mps = 0.0
                avg_kmh = avg_mps * 3.6

                act_sport = str(act.get("sport") or "RUNNING").upper()
                if act_sport in ("RUNNING", "WALKING"):
                    if self.config.walk_run_threshold_kmh > 0 and avg_kmh <= self.config.walk_run_threshold_kmh:
                        target_sport = "WALKING"
                        desired_title = "Sole Treadmill Indoor Walking"
                    else:
                        target_sport = "RUNNING"
                        desired_title = "Sole Treadmill Indoor Run"
                else:
                    target_sport = act_sport
                    desired_title = act.get("name") or "Sole Activity"
            else:
                # Fallback: respect recorded sport if explicitly WALKING, otherwise default to RUNNING.
                # Never downgrade based on 'walk' in stored_name because previous runs may have mislabeled titles.
                if current_sport == "WALKING" and "run" not in stored_name.lower():
                    target_sport = "WALKING"
                    desired_title = "Sole Treadmill Indoor Walking"
                else:
                    target_sport = "RUNNING"
                    desired_title = "Sole Treadmill Indoor Run"

            success = self.garmin._try_rename_uploaded_activity(
                activity_date_str=act_date,
                desired_title=desired_title,
                target_sport=target_sport,
                retries=1,
                delay_sec=0,
                threshold_hours=self.config.duplicate_threshold_hours,
            )

            if success:
                updated_count += 1
                if stored_name != desired_title or current_sport != target_sport:
                    self.store.record_activity(
                        sole_id=sole_id,
                        name=desired_title,
                        activity_date=act_date,
                        sport=target_sport,
                        status=record.get("garmin_status") or "UPLOADED",
                        notes=record.get("notes") or "Retroactively updated title and sport",
                    )

        logger.info("Completed verification of previously synced activities (%d updated/verified).", updated_count)
        return updated_count
