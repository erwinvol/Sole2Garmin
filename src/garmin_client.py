"""Garmin Connect client for authentication, duplicate checks, and activity uploading."""

import locale
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    locale.setlocale(locale.LC_TIME, "")
except Exception:
    pass


def get_host_timezone() -> datetime.tzinfo:
    """Resolve timezone from environment variables (TZ, TIMEZONE) or system host timezone."""
    for env_var in ("TZ", "TIMEZONE"):
        tz_str = os.getenv(env_var, "").strip()
        if tz_str:
            try:
                import zoneinfo
                return zoneinfo.ZoneInfo(tz_str)
            except Exception:
                pass
    try:
        local_tz = datetime.now().astimezone().tzinfo
        if local_tz is not None:
            return local_tz
    except Exception:
        pass
    return timezone.utc


def parse_activity_datetime(date_val: Any) -> Optional[datetime]:
    """Parse various timestamp representations into a timezone-aware UTC datetime.

    If date_val is a naive string or naive datetime (without timezone offset),
    it is treated as local wall-clock time in the host system timezone and converted to UTC.
    If date_val is already timezone-aware or an epoch timestamp, it is normalized to UTC.
    """
    if not date_val:
        return None

    host_tz = get_host_timezone()

    if isinstance(date_val, datetime):
        if date_val.tzinfo is None:
            return date_val.replace(tzinfo=host_tz).astimezone(timezone.utc)
        return date_val.astimezone(timezone.utc)

    # Handle numeric timestamp
    try:
        ts = float(date_val)
        if ts > 100_000_000_000:  # milliseconds
            ts = ts / 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        pass

    # Clean string format
    clean_str = str(date_val).strip()
    if clean_str.endswith("Z"):
        clean_str = clean_str[:-1] + "+00:00"

    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(clean_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=host_tz).astimezone(timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return dt
        except (ValueError, TypeError):
            continue

    try:
        dt = datetime.fromisoformat(clean_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=host_tz).astimezone(timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    except (ValueError, TypeError):
        pass

    return None


def format_local_datetime(date_val: Any, include_time: bool = True) -> str:
    """Format an activity datetime, timestamp string, or epoch into local system format.

    Converts UTC timestamps to the local system timezone and formats according to system locale (%x %X).
    """
    if not date_val:
        return ""

    dt = parse_activity_datetime(date_val)
    if not dt:
        return str(date_val)

    local_dt = dt.astimezone(get_host_timezone())

    try:
        fmt = "%x %X" if include_time else "%x"
        return local_dt.strftime(fmt)
    except Exception:
        return local_dt.strftime("%Y-%m-%d %H:%M:%S")


class GarminClient:
    """Handles authentication, duplicate checks, and uploading to Garmin Connect."""

    def __init__(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        token_dir: Path = Path("data/garmin_tokens"),
    ):
        self.username = username
        self.password = password
        self.token_dir = Path(token_dir)
        self.token_dir.mkdir(parents=True, exist_ok=True)
        self._client: Optional[Any] = None
        self._authenticated = False
        self._cached_activity_types: Optional[List[Dict[str, Any]]] = None

    def authenticate(self) -> bool:
        """Authenticate with Garmin Connect using cached tokens or credentials."""
        try:
            import garminconnect
            from garminconnect import Garmin

            token_file = self.token_dir / "garmin_tokens.json"

            # 1. Try loading cached tokens first
            if token_file.exists():
                try:
                    logger.info("Attempting login to Garmin Connect using cached token: %s", token_file)
                    client = Garmin()
                    client.login(str(token_file))
                    self._client = client
                    self._authenticated = True
                    logger.info("Garmin Connect authentication successful (resumed session from token cache).")
                    return True
                except Exception as exc:
                    logger.warning("Cached Garmin tokens invalid or expired (%s). Re-authenticating...", exc)

            # 2. Authenticate with credentials if provided
            if not self.username or not self.password:
                logger.error("No valid Garmin tokens found and credentials are not fully configured.")
                return False

            logger.info("Authenticating with Garmin Connect as %s...", self.username)
            client = Garmin(self.username, self.password)
            client.login()
            client.garth.dump(str(token_file))
            self._client = client
            self._authenticated = True
            logger.info("Garmin Connect authentication successful. Saved new session tokens to %s", token_file)
            return True

        except Exception as exc:
            logger.error("Garmin Connect authentication failed: %s", exc)
            self._authenticated = False
            return False

    def ensure_authenticated(self) -> None:
        """Raise RuntimeError if client is not authenticated."""
        if not self._authenticated:
            if not self.authenticate():
                raise RuntimeError("Garmin Connect authentication required but failed.")

    def get_activity_types(self) -> List[Dict[str, Any]]:
        """Retrieve and cache all available Garmin activity types (id, key, parent)."""
        self.ensure_authenticated()
        if self._cached_activity_types is not None:
            return self._cached_activity_types

        try:
            types = self._client.get_activity_types()
            if isinstance(types, list) and types:
                self._cached_activity_types = types
                return self._cached_activity_types
        except Exception as exc:
            logger.debug("Could not fetch Garmin activity types: %s", exc)

        return []

    def set_activity_name(self, activity_id: Any, name: str) -> bool:
        """Update the title of an existing activity on Garmin Connect.

        Uses the official Connect API endpoint:
        PUT https://connect.garmin.com/activity-service/activity/{activity_id}
        payload: {"activityId": int, "activityName": str}
        """
        self.ensure_authenticated()
        try:
            url = f"{self._client.garmin_connect_activity}/{activity_id}"
            payload = {"activityId": int(activity_id), "activityName": name}
            logger.debug("Updating Garmin activity %s title to '%s' via %s...", activity_id, name, url)
            self._client.client.put("connectapi", url, json=payload, api=True)
            logger.info("Successfully updated Garmin activity %s title to '%s'.", activity_id, name)
            return True
        except Exception as exc:
            logger.warning("Failed to update activity title for Garmin activity %s: %s", activity_id, exc)
            return False

    def set_activity_type(self, activity_id: Any, type_key: str = "walking") -> bool:
        """Update the activity type of an existing activity on Garmin Connect (e.g. to walking).

        Uses the official Connect API endpoint:
        PUT https://connect.garmin.com/activity-service/activity/{activity_id}
        payload: {"activityId": int, "activityTypeDTO": {"typeId": int, "typeKey": str, "parentTypeId": int}}
        """
        self.ensure_authenticated()
        try:
            activity_types = self.get_activity_types()
            matched_type = None
            for t in activity_types:
                if t.get("typeKey", "").lower() == type_key.lower():
                    matched_type = t
                    break

            if not matched_type:
                logger.warning("Could not find Garmin activity type '%s' in available activity types.", type_key)
                return False

            url = f"{self._client.garmin_connect_activity}/{activity_id}"
            type_dto = {
                "typeId": matched_type["typeId"],
                "typeKey": matched_type["typeKey"],
                "parentTypeId": matched_type.get("parentTypeId", 17),
            }
            payload = {"activityId": int(activity_id), "activityTypeDTO": type_dto}
            logger.debug("Updating Garmin activity %s type to '%s' (%s) via %s...", activity_id, type_key, type_dto, url)
            self._client.client.put("connectapi", url, json=payload, api=True)
            logger.info("Successfully changed Garmin activity %s type to '%s'.", activity_id, type_key)
            return True
        except Exception as exc:
            logger.warning("Failed to change activity type for Garmin activity %s: %s", activity_id, exc)
            return False

    def update_activity(
        self,
        activity_id: Any,
        name: Optional[str] = None,
        type_key: Optional[str] = None,
    ) -> bool:
        """Atomically update activity title and activity type in a single Garmin Connect API call."""
        self.ensure_authenticated()
        try:
            payload: Dict[str, Any] = {"activityId": int(activity_id)}
            if name:
                payload["activityName"] = name

            if type_key:
                activity_types = self.get_activity_types()
                matched_type = next((t for t in activity_types if t.get("typeKey", "").lower() == type_key.lower()), None)
                if matched_type:
                    payload["activityTypeDTO"] = {
                        "typeId": matched_type["typeId"],
                        "typeKey": matched_type["typeKey"],
                        "parentTypeId": matched_type.get("parentTypeId", 17),
                    }
                else:
                    logger.warning("Could not find Garmin activity type '%s' in available activity types.", type_key)

            url = f"{self._client.garmin_connect_activity}/{activity_id}"
            logger.debug("Updating Garmin activity %s via %s (payload: %s)...", activity_id, url, payload)
            self._client.client.put("connectapi", url, json=payload, api=True)
            logger.info("Successfully updated Garmin activity %s: name='%s', type='%s'.", activity_id, name, type_key)
            return True
        except Exception as exc:
            logger.warning("Failed atomic update for Garmin activity %s: %s", activity_id, exc)
            return False

    def _calc_time_difference_hours(self, act: Dict[str, Any], target_dt: datetime) -> float:
        """Compute the minimum absolute difference in hours between target_dt and a Garmin activity."""
        diffs = []

        # 1. Epoch beginTimestamp (milliseconds UTC)
        if act.get("beginTimestamp"):
            try:
                dt_begin = datetime.fromtimestamp(float(act["beginTimestamp"]) / 1000.0, tz=timezone.utc)
                diffs.append(abs((dt_begin - target_dt).total_seconds()) / 3600.0)
            except Exception:
                pass

        # 2. startTimeGMT (UTC string)
        start_gmt = act.get("startTimeGMT")
        if start_gmt:
            try:
                clean_gmt = str(start_gmt).strip().replace("T", " ")
                if "." in clean_gmt:
                    clean_gmt = clean_gmt.split(".")[0]
                dt_gmt = datetime.strptime(clean_gmt, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                diffs.append(abs((dt_gmt - target_dt).total_seconds()) / 3600.0)
            except Exception:
                dt_gmt = parse_activity_datetime(start_gmt)
                if dt_gmt:
                    diffs.append(abs((dt_gmt - target_dt).total_seconds()) / 3600.0)

        # 3. startTimeLocal (local naive or ISO string)
        start_local = act.get("startTimeLocal") or act.get("startTime")
        if start_local:
            try:
                clean_local = str(start_local).strip().replace("T", " ")
                if "." in clean_local:
                    clean_local = clean_local.split(".")[0]
                dt_local_naive = datetime.strptime(clean_local, "%Y-%m-%d %H:%M:%S")
                target_local_naive = target_dt.astimezone(get_host_timezone()).replace(tzinfo=None)
                diffs.append(abs((dt_local_naive - target_local_naive).total_seconds()) / 3600.0)
            except Exception:
                dt_local = parse_activity_datetime(start_local)
                if dt_local:
                    diffs.append(abs((dt_local - target_dt).total_seconds()) / 3600.0)

        return min(diffs) if diffs else 999.0

    def check_duplicate(
        self,
        activity_date_str: Optional[str],
        activity_name: Optional[str] = None,
        target_sport: Optional[str] = None,
        threshold_hours: int = 1,
    ) -> bool:
        """Check if an activity matching the given timestamp already exists on Garmin Connect.

        Args:
            activity_date_str: ISO format date string, timestamp, or epoch string.
            activity_name: Optional title string to check against existing Garmin activity titles.
            target_sport: Sport string (e.g. 'WALKING', 'RUNNING') to prevent false matches across different disciplines.
            threshold_hours: Search buffer in hours (default: 1 hour).

        Returns:
            True if an existing activity is detected on Garmin Connect.
        """
        self.ensure_authenticated()

        target_dt = parse_activity_datetime(activity_date_str)
        if not target_dt:
            logger.warning("Could not parse activity datetime '%s'; skipping Garmin duplicate check.", activity_date_str)
            return False

        start_date = (target_dt - timedelta(days=1)).strftime("%Y-%m-%d")
        end_date = (target_dt + timedelta(days=1)).strftime("%Y-%m-%d")

        try:
            activities = self._client.get_activities_by_date(start_date, end_date)
            if not activities:
                return False

            logger.info(
                "Checking %d Garmin activities between %s and %s for '%s' (target: %s)...",
                len(activities),
                start_date,
                end_date,
                activity_name or "Sole Workout",
                target_dt.isoformat(),
            )

            # Expected Garmin type keys based on target sport
            sport_key = (target_sport or "").upper()
            matching_type_keys = set()
            if sport_key == "WALKING":
                matching_type_keys = {"walking", "casual_walking", "speed_walking"}
            elif sport_key == "RUNNING":
                matching_type_keys = {"running", "treadmill_running", "indoor_running", "street_running"}
            elif sport_key == "CYCLING":
                matching_type_keys = {"cycling", "indoor_cycling", "virtual_ride"}

            for act in activities:
                # 1. Title match
                current_title = (act.get("activityName") or "").strip()
                if activity_name and current_title and current_title.lower() == activity_name.strip().lower():
                    time_diff = self._calc_time_difference_hours(act, target_dt)
                    if time_diff <= max(threshold_hours, 2):
                        logger.info(
                            "Found exact title match on Garmin Connect: '%s' (ID: %s, Diff: %.2fh). Marking duplicate.",
                            current_title,
                            act.get("activityId"),
                            time_diff,
                        )
                        return True

                # 2. Timestamp proximity check
                min_diff = self._calc_time_difference_hours(act, target_dt)
                if min_diff <= threshold_hours:
                    act_type_dto = act.get("activityType") or {}
                    act_type_key = str(act_type_dto.get("typeKey", "")).lower()

                    # If sports match or generic treadmill upload, mark duplicate
                    if not matching_type_keys or act_type_key in matching_type_keys or act_type_key in ("treadmill_running", "running", "walking"):
                        logger.info(
                            "Found existing activity '%s' on Garmin Connect (ID: %s, Type: %s) within %.2f hours of target. Marking duplicate.",
                            current_title or "Untitled",
                            act.get("activityId"),
                            act_type_key,
                            min_diff,
                        )
                        return True
                    else:
                        logger.debug(
                            "Activity '%s' (ID: %s) is within %.2fh but type '%s' does not match target sport '%s'. Skipping duplicate match.",
                            current_title,
                            act.get("activityId"),
                            act_type_key,
                            target_sport,
                        )

            return False

        except Exception as exc:
            logger.warning("Error querying Garmin Connect activities by date: %s", exc)
            return False

    def is_activity_uploaded(
        self,
        activity_date_str: str,
        activity_name: Optional[str] = None,
        target_sport: Optional[str] = None,
        threshold_hours: int = 1,
    ) -> bool:
        """Alias for check_duplicate."""
        return self.check_duplicate(
            activity_date_str=activity_date_str,
            activity_name=activity_name,
            target_sport=target_sport,
            threshold_hours=threshold_hours,
        )

    def upload_activity(
        self,
        fit_file_path: str,
        activity_name: Optional[str] = None,
        activity_date_str: Optional[str] = None,
        target_sport: Optional[str] = None,
        auto_rename: bool = True,
    ) -> Dict[str, Any]:
        """Upload a FIT file to Garmin Connect and optionally rename / set activity type afterwards.

        Args:
            fit_file_path: Path to the binary FIT file.
            activity_name: Desired activity title (e.g. 'Sole Treadmill Indoor Walking').
            activity_date_str: Timestamp of the activity to locate and rename it post-upload.
            target_sport: Desired sport type ('WALKING', 'RUNNING', 'CYCLING', etc.).
            auto_rename: If True, queries Garmin Connect post-upload to set the name and type.

        Returns:
            Dict containing upload status, uploadId, and activityId (if retrieved).
        """
        self.ensure_authenticated()

        if not os.path.exists(fit_file_path):
            raise FileNotFoundError(f"FIT file not found: {fit_file_path}")

        file_size = os.path.getsize(fit_file_path)
        logger.info(
            "Uploading FIT file %s (%d bytes) to Garmin Connect (Title: '%s', Date: '%s', Sport: %s)...",
            fit_file_path,
            file_size,
            activity_name or "Default",
            activity_date_str or "Unknown",
            target_sport or "Default",
        )

        try:
            res = self._client.upload_activity(fit_file_path)
            upload_id = None
            if isinstance(res, dict):
                upload_id = res.get("uploadId") or res.get("id")

            logger.info("FIT file upload request succeeded (Upload ID: %s, Response: %s).", upload_id, res)

            # Post-upload renaming & activity type updating
            renamed = False
            if auto_rename and (activity_name or target_sport) and activity_date_str:
                renamed = self._try_rename_uploaded_activity(
                    activity_date_str=activity_date_str,
                    desired_title=activity_name,
                    target_sport=target_sport,
                )

            return {
                "status": "uploaded",
                "upload_id": upload_id,
                "renamed": renamed,
                "response": res,
            }

        except Exception as exc:
            err_msg = str(exc)
            if "409" in err_msg or "Conflict" in err_msg or "exists" in err_msg.lower():
                logger.info("Garmin Connect returned HTTP 409 Conflict: Activity already exists.")
                return {"status": "skipped_duplicate", "error": err_msg}
            logger.error("Garmin Connect upload failed: %s", exc)
            return {"status": "failed", "error": err_msg}

    def _try_rename_uploaded_activity(
        self,
        activity_date_str: str,
        desired_title: Optional[str] = None,
        target_sport: Optional[str] = None,
        retries: int = 4,
        delay_sec: int = 5,
        threshold_hours: float = 2.0,
    ) -> bool:
        """Find the uploaded activity on Garmin Connect matching activity_date_str and update its title and type."""
        self.ensure_authenticated()

        target_dt = parse_activity_datetime(activity_date_str)
        if not target_dt:
            logger.warning("Could not parse activity datetime '%s' for Garmin renaming.", activity_date_str)
            return False

        start_date = (target_dt - timedelta(days=1)).strftime("%Y-%m-%d")
        end_date = (target_dt + timedelta(days=1)).strftime("%Y-%m-%d")

        for attempt in range(1, retries + 1):
            if attempt > 1 and delay_sec > 0:
                time.sleep(delay_sec)

            try:
                activities = self._client.get_activities_by_date(start_date, end_date)
                if not activities:
                    continue

                best_act = None
                best_diff = 999.0

                for act in activities:
                    diff = self._calc_time_difference_hours(act, target_dt)
                    if diff < best_diff and diff <= threshold_hours:
                        best_diff = diff
                        best_act = act

                if not best_act:
                    continue

                act_id = best_act.get("activityId")
                curr_name = (best_act.get("activityName") or "").strip()
                curr_type_dto = best_act.get("activityType") or {}
                curr_type_key = str(curr_type_dto.get("typeKey", "")).lower()

                # Determine if type key needs update
                target_type_key = None
                if target_sport:
                    sport_upper = target_sport.upper()
                    if sport_upper == "WALKING":
                        target_type_key = "walking"
                    elif sport_upper == "RUNNING":
                        target_type_key = "running"
                    elif sport_upper == "CYCLING":
                        target_type_key = "cycling"

                needs_type_update = False
                if target_type_key and curr_type_key != target_type_key:
                    # Protect distinct running workouts from being overwritten by a distant walking sync
                    if curr_type_key in ("running", "treadmill_running") and target_type_key == "walking" and best_diff > 0.5:
                        logger.warning(
                            "Skipping reclassification of Garmin activity %s (%s, diff=%.2fh) to walking to protect existing run.",
                            act_id,
                            curr_type_key,
                            best_diff,
                        )
                        needs_type_update = False
                    else:
                        needs_type_update = True

                needs_name_update = bool(desired_title and curr_name != desired_title.strip())

                if not needs_name_update and not needs_type_update:
                    logger.debug("Garmin activity %s already has desired name and type.", act_id)
                    return True

                # Apply atomic update
                return self.update_activity(
                    activity_id=act_id,
                    name=desired_title if needs_name_update else None,
                    type_key=target_type_key if needs_type_update else None,
                )

            except Exception as exc:
                logger.debug("Attempt %d/%d to rename Garmin activity failed: %s", attempt, retries, exc)

        logger.warning(
            "Could not locate Garmin activity for datetime '%s' (window: %s to %s) to update metadata.",
            activity_date_str,
            start_date,
            end_date,
        )
        return False
