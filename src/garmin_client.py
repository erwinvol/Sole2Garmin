"""Garmin Connect client for authentication, duplicate checks, and activity uploading."""

import locale
import logging
import os
import time
import zoneinfo
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

try:
    locale.setlocale(locale.LC_TIME, "")
except Exception:
    pass


def get_configured_timezone(tz_name: Optional[Union[str, zoneinfo.ZoneInfo]] = None) -> zoneinfo.ZoneInfo:
    """Resolve timezone from parameter, environment variables, or fallback to America/New_York."""
    if isinstance(tz_name, zoneinfo.ZoneInfo):
        return tz_name

    candidates = [
        str(tz_name).strip() if tz_name else None,
        os.getenv("TIMEZONE", "").strip() or None,
        os.getenv("TZ", "").strip() or None,
    ]
    for c in candidates:
        if c:
            try:
                return zoneinfo.ZoneInfo(c)
            except Exception as exc:
                logger.debug("Could not parse timezone '%s': %s", c, exc)

    # Fallback to system local timezone if not UTC
    try:
        local_tz = datetime.now().astimezone().tzinfo
        if local_tz and str(local_tz) not in ("UTC", "GMT", "Coordinated Universal Time"):
            return zoneinfo.ZoneInfo(str(local_tz))
    except Exception:
        pass

    return zoneinfo.ZoneInfo("America/New_York")


def parse_activity_datetime(date_val: Any, user_tz: Optional[Any] = None) -> Optional[datetime]:
    """Parse various timestamp representations into a timezone-aware UTC datetime.

    If date_val is a naive string or naive datetime (without timezone offset),
    it is treated as local wall-clock time in user_tz (defaulting to America/New_York
    or system timezone) and converted to UTC.
    If date_val is already timezone-aware or an epoch timestamp, it is normalized to UTC.
    """
    if not date_val:
        return None

    target_tz = get_configured_timezone(user_tz)

    if isinstance(date_val, datetime):
        if date_val.tzinfo is None:
            return date_val.replace(tzinfo=target_tz).astimezone(timezone.utc)
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

    # Try formats with explicit timezone offset
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S%z",
    ):
        try:
            dt = datetime.strptime(clean_str, fmt)
            return dt.astimezone(timezone.utc)
        except (ValueError, TypeError):
            continue

    try:
        dt = datetime.fromisoformat(clean_str)
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc)
        return dt.replace(tzinfo=target_tz).astimezone(timezone.utc)
    except (ValueError, TypeError):
        pass

    # Try naive datetime formats
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(clean_str, fmt)
            return dt.replace(tzinfo=target_tz).astimezone(timezone.utc)
        except (ValueError, TypeError):
            continue

    return None


def format_local_datetime(date_val: Any, include_time: bool = True, user_tz: Optional[Any] = None) -> str:
    """Format an activity datetime, timestamp string, or epoch into local system/configured format.

    Converts UTC timestamps to the user/configured timezone and formats according to system locale (%x %X).
    """
    if not date_val:
        return ""

    dt = parse_activity_datetime(date_val, user_tz=user_tz)
    if not dt:
        return str(date_val)

    target_tz = get_configured_timezone(user_tz)
    local_dt = dt.astimezone(target_tz)

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
        token_dir: Optional[Path] = None,
        user_timezone: Optional[str] = None,
    ):
        self.username = username
        self.password = password
        self.token_dir = token_dir or Path.home() / ".garminconnect"
        self.user_timezone = user_timezone
        self._client: Optional[Any] = None
        self._authenticated = False
        self._activity_types_cache: Optional[List[Dict[str, Any]]] = None

    def authenticate(self) -> None:
        """Authenticate with Garmin Connect using token cache or user/password credentials."""
        from garminconnect import (
            Garmin,
            GarminConnectAuthenticationError,
            GarminConnectConnectionError,
            GarminConnectTooManyRequestsError,
        )

        logger.info("Authenticating with Garmin Connect...")
        self.token_dir.mkdir(parents=True, exist_ok=True)
        token_path = str(self.token_dir)

        try:
            # 1. Proactive Direct Refresh Attempt
            token_file = self.token_dir / "garmin_tokens.json"
            if not token_file.exists():
                token_files = list(self.token_dir.glob("*"))
                token_file = token_files[0] if token_files else token_file

            if token_file.exists():
                try:
                    import json
                    with open(token_file, "r") as f:
                        data = json.load(f)
                    di_token = data.get("di_token") or {}
                    refresh_token = di_token.get("refresh_token")
                    expires_at = di_token.get("expires_at", 0)

                    now = time.time()
                    if refresh_token and (expires_at - now < 300):
                        logger.info("Garmin token expiring soon or expired, refreshing DI token...")
                        from garminconnect.client import GarminClient as RawClient
                        raw_c = RawClient()
                        raw_c.refresh_token = refresh_token
                        new_di = raw_c.refresh_di_token()
                        if new_di:
                            data["di_token"] = new_di
                            with open(token_file, "w") as f:
                                json.dump(data, f)
                            logger.info("Successfully refreshed and updated Garmin DI token on disk.")
                except Exception as e:
                    logger.debug("Direct load/refresh of token file failed: %s", e)

            # 2. Try loading cached tokens
            client = Garmin()
            try:
                client.login(token_path)
                self._client = client
                self._authenticated = True
                logger.info("Garmin Connect authentication successful (resumed session from token cache).")
                return
            except Exception as token_err:
                logger.warning(
                    "Cached token login failed (%s). Attempting credential login...",
                    token_err,
                )

            # 3. Fallback to username/password login
            if not self.username or not self.password:
                raise RuntimeError(
                    "Garmin credentials not provided and cached tokens are missing or invalid."
                )

            client = Garmin(self.username, self.password)
            client.login()
            client.garth.dump(token_path)
            self._client = client
            self._authenticated = True
            logger.info("Garmin Connect authentication successful (new session saved to token cache).")

        except GarminConnectAuthenticationError:
            logger.exception("Garmin Connect authentication error. Please verify credentials.")
            raise
        except GarminConnectTooManyRequestsError:
            logger.exception("Garmin Connect rate limit encountered (HTTP 429 Too Many Requests).")
            raise
        except GarminConnectConnectionError:
            logger.exception("Garmin Connect network connection failure.")
            raise
        except Exception as exc:
            logger.exception("Failed to authenticate with Garmin Connect: %s", exc)
            raise RuntimeError(f"Garmin Connect login failed: {exc}") from exc

    def ensure_authenticated(self) -> None:
        """Verify client authentication status."""
        if not self._authenticated or self._client is None:
            self.authenticate()

    def _compute_activity_time_difference(self, target_dt: datetime, act: Dict[str, Any]) -> float:
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
                target_tz = get_configured_timezone(self.user_timezone)
                target_local_naive = target_dt.astimezone(target_tz).replace(tzinfo=None)
                diffs.append(abs((dt_local_naive - target_local_naive).total_seconds()) / 3600.0)
            except Exception:
                dt_local = parse_activity_datetime(start_local, user_tz=self.user_timezone)
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
            activity_date_str: Raw date or timestamp string.
            activity_name: Optional name for fuzzy comparison.
            target_sport: Optional sport (e.g. WALKING, RUNNING) to avoid cross-sport false positives.
            threshold_hours: Time difference window to consider as duplicate.

        Returns:
            True if an existing activity is detected on Garmin Connect.
        """
        self.ensure_authenticated()

        target_dt = parse_activity_datetime(activity_date_str, user_tz=self.user_timezone)
        if not target_dt:
            logger.warning("Could not parse activity datetime '%s'; skipping Garmin duplicate check.", activity_date_str)
            return False

        start_date = (target_dt - timedelta(days=1)).strftime("%Y-%m-%d")
        end_date = (target_dt + timedelta(days=1)).strftime("%Y-%m-%d")
        logger.info("Checking Garmin Connect for duplicate activities between %s and %s (±%dh window)...", start_date, end_date, threshold_hours)

        for attempt in range(1, 4):
            try:
                existing_activities = self._client.get_activities_by_date(start_date, end_date) or []
                logger.info("Found %d existing activities on Garmin between %s and %s", len(existing_activities), start_date, end_date)

                for act in existing_activities:
                    hours_diff = self._compute_activity_time_difference(target_dt, act)

                    if hours_diff <= threshold_hours:
                        existing_title = act.get("activityName", "")
                        type_key = str(act.get("activityType", {}).get("typeKey", "")).lower()

                        # Prevent cross-sport false positives (e.g., walking workout skipped due to virtual cycling)
                        if target_sport:
                            norm_target = str(target_sport).upper()
                            is_target_pedestrian = norm_target in ("WALKING", "RUNNING")
                            is_act_cycling = "cycl" in type_key or "ride" in type_key or "bike" in type_key
                            if is_target_pedestrian and is_act_cycling:
                                logger.info(
                                    "Ignoring Garmin activity '%s' (%s) because it is cycling while target is %s.",
                                    existing_title,
                                    type_key,
                                    norm_target,
                                )
                                continue

                        logger.warning(
                            "Found potential duplicate activity on Garmin: '%s' (%s) (diff: %.1f hrs)",
                            existing_title,
                            type_key or "unknown",
                            hours_diff,
                        )
                        return True

                return False

            except Exception as exc:
                logger.warning("Error querying Garmin Connect activities (attempt %d/3): %s", attempt, exc)
                time.sleep(2)

        return False

    def upload_activity(
        self,
        fit_file_path: str,
        activity_name: Optional[str] = None,
        activity_date_str: Optional[str] = None,
        target_sport: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Upload a FIT file to Garmin Connect.

        Args:
            fit_file_path: Absolute path to the .fit file.
            activity_name: Optional desired activity name to set in Garmin Connect.
            activity_date_str: Optional activity start date/time string.
            target_sport: Optional sport (e.g. WALKING, RUNNING).
            **kwargs: Additional optional parameters.

        Returns:
            Dictionary with upload response details ('status', 'response', or 'skipped_duplicate').
        """
        self.ensure_authenticated()

        if not os.path.exists(fit_file_path):
            raise FileNotFoundError(f"FIT file not found: {fit_file_path}")

        file_size = os.path.getsize(fit_file_path)
        file_name = os.path.basename(fit_file_path)
        logger.info("Uploading %s (%d bytes) to Garmin Connect...", file_name, file_size)

        for attempt in range(1, 4):
            try:
                resp = self._client.upload_activity(fit_file_path)
                logger.info("Upload to Garmin Connect successful!")

                if activity_name and activity_date_str:
                    self._try_rename_uploaded_activity(
                        activity_date_str=activity_date_str,
                        desired_title=activity_name,
                        target_sport=target_sport,
                    )

                return {"status": "uploaded", "response": resp}
            except Exception as exc:
                err_str = str(exc)
                if "409" in err_str or "Conflict" in err_str:
                    logger.warning("Garmin Connect returned HTTP 409 Conflict: Activity already exists.")
                    return {"status": "skipped_duplicate", "reason": "HTTP 409 Conflict"}

                if ("429" in err_str or "too many requests" in err_str.lower()) and attempt < 3:
                    wait_sec = attempt * 10
                    logger.warning("Garmin Connect rate limit (HTTP 429) during upload. Retrying in %ds...", wait_sec)
                    time.sleep(wait_sec)
                    continue

                logger.exception("Failed to upload FIT file %s to Garmin: %s", fit_file_path, exc)
                raise RuntimeError(f"Garmin upload failed: {exc}") from exc

    def update_activity(
        self,
        activity_id: str,
        name: Optional[str] = None,
        type_key: Optional[str] = None,
        type_id: Optional[int] = None,
        parent_type_id: Optional[int] = None,
    ) -> bool:
        """Update activity name and/or activity type atomically in Garmin Connect."""
        self.ensure_authenticated()
        try:
            url = f"/activity-service/activity/{activity_id}"
            payload: Dict[str, Any] = {"activityId": int(activity_id)}

            if name:
                payload["activityName"] = name

            if type_key or type_id:
                if type_id is None or parent_type_id is None:
                    info = self._get_activity_type_info(type_key or "walking")
                    if info:
                        type_id = info.get("typeId")
                        type_key = info.get("typeKey", type_key)
                        parent_type_id = info.get("parentTypeId")

                if type_id is None:
                    fallbacks = {
                        "walking": (9, 17),
                        "indoor_walking": (9, 17),
                        "running": (1, 17),
                        "treadmill_running": (18, 1),
                    }
                    fb = fallbacks.get(str(type_key).lower(), (9, 17))
                    type_id = fb[0]
                    parent_type_id = parent_type_id or fb[1]

                payload["activityTypeDTO"] = {
                    "typeId": type_id,
                    "typeKey": type_key,
                    "parentTypeId": parent_type_id or 17,
                }

            self._client.client.put("connectapi", url, json=payload, api=True)
            logger.info(
                "Successfully updated Garmin activity %s (name: '%s', type: '%s')",
                activity_id,
                name or "(unchanged)",
                type_key or "(unchanged)",
            )
            return True
        except Exception as exc:
            logger.warning("Failed to update Garmin activity %s: %s", activity_id, exc)
            return False

    def set_activity_name(self, activity_id: str, title: str) -> bool:
        """Update an activity title in Garmin Connect."""
        return self.update_activity(activity_id=str(activity_id), name=title)

    def _get_activity_type_info(self, desired_key: str) -> Optional[Dict[str, Any]]:
        """Fetch activity type metadata from Garmin Connect."""
        if self._activity_types_cache is None:
            try:
                self._activity_types_cache = self._client.get_activity_types()
            except Exception as e:
                logger.debug("Could not fetch activity types list from Garmin: %s", e)
                self._activity_types_cache = []

        desired_lower = desired_key.lower()
        for t in (self._activity_types_cache or []):
            if str(t.get("typeKey", "")).lower() == desired_lower:
                return t
        return None

    def set_activity_type(
        self,
        activity_id: str,
        type_key: str = "walking",
        type_id: Optional[int] = None,
        parent_type_id: Optional[int] = None,
    ) -> bool:
        """Update an activity category/type in Garmin Connect."""
        return self.update_activity(
            activity_id=str(activity_id),
            type_key=type_key,
            type_id=type_id,
            parent_type_id=parent_type_id,
        )

    def _try_rename_uploaded_activity(
        self,
        activity_date_str: str,
        desired_title: str,
        target_sport: Optional[str] = None,
        retries: int = 3,
        delay_sec: int = 4,
        threshold_hours: float = 2.0,
    ) -> bool:
        """Find the uploaded activity on Garmin Connect matching activity_date_str and update its title and type."""
        self.ensure_authenticated()

        target_dt = parse_activity_datetime(activity_date_str, user_tz=self.user_timezone)
        if not target_dt:
            logger.warning("Could not parse activity datetime '%s' for Garmin renaming.", activity_date_str)
            return False

        start_date = (target_dt - timedelta(days=1)).strftime("%Y-%m-%d")
        end_date = (target_dt + timedelta(days=1)).strftime("%Y-%m-%d")

        for attempt in range(1, retries + 1):
            if delay_sec > 0:
                time.sleep(delay_sec)
            try:
                activities = self._client.get_activities_by_date(start_date, end_date) or []
                logger.info(
                    "Checking %d Garmin activities between %s and %s for '%s' (target: %s)...",
                    len(activities),
                    start_date,
                    end_date,
                    desired_title,
                    target_dt.isoformat(),
                )
                for act in activities:
                    hours_diff = self._compute_activity_time_difference(target_dt, act)

                    if hours_diff <= threshold_hours:
                        type_key = str(act.get("activityType", {}).get("typeKey", "")).lower()
                        if target_sport:
                            norm_target = str(target_sport).upper()
                            is_target_pedestrian = norm_target in ("WALKING", "RUNNING")
                            is_act_cycling = "cycl" in type_key or "ride" in type_key or "bike" in type_key
                            if is_target_pedestrian and is_act_cycling:
                                continue

                        act_id = act.get("activityId")
                        if not act_id:
                            continue

                        current_title = act.get("activityName")
                        needs_name_update = bool(desired_title and current_title != desired_title)
                        needs_type_update = False
                        desired_type_key = None

                        if target_sport:
                            norm_target = str(target_sport).upper()
                            if norm_target == "WALKING" and "walk" not in type_key:
                                needs_type_update = True
                                desired_type_key = "walking"
                            elif norm_target == "RUNNING" and "run" not in type_key:
                                needs_type_update = True
                                desired_type_key = "running"

                        if needs_name_update or needs_type_update:
                            logger.info(
                                "Updating Garmin activity %s: title='%s' (was '%s'), type='%s' (was '%s')...",
                                act_id,
                                desired_title,
                                current_title,
                                desired_type_key or type_key,
                                type_key,
                            )
                            self.update_activity(
                                activity_id=str(act_id),
                                name=desired_title if needs_name_update else current_title,
                                type_key=desired_type_key,
                            )
                        return True
            except Exception as exc:
                logger.debug("Rename/update attempt %d for %s-%s failed: %s", attempt, start_date, end_date, exc)

        return False
