"""Garmin Connect client for authentication, duplicate checks, and activity uploading."""

import locale
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    locale.setlocale(locale.LC_TIME, "")
except Exception:
    pass


def parse_activity_datetime(date_val: Any) -> Optional[datetime]:
    """Parse various timestamp representations into a timezone-aware datetime."""
    if not date_val:
        return None

    if isinstance(date_val, datetime):
        return date_val if date_val.tzinfo else date_val.replace(tzinfo=timezone.utc)

    # Handle numeric timestamp
    try:
        ts = float(date_val)
        if ts > 100_000_000_000:  # milliseconds
            ts = ts / 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        pass

    # Clean string format
    clean_str = str(date_val).replace("Z", "+00:00")
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(clean_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            continue

    try:
        dt = datetime.fromisoformat(clean_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
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

    local_dt = dt.astimezone()

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
    ):
        self.username = username
        self.password = password
        self.token_dir = token_dir or Path.home() / ".garminconnect"
        self._client: Optional[Any] = None
        self._authenticated = False

    def authenticate(self) -> bool:
        """Authenticate with Garmin Connect using cached tokens or user credentials."""
        from garminconnect import (
            Garmin,
            GarminConnectAuthenticationError,
            GarminConnectConnectionError,
            GarminConnectTooManyRequestsError,
        )

        logger.info("Authenticating with Garmin Connect...")
        self.token_dir.mkdir(parents=True, exist_ok=True)
        token_str = str(self.token_dir)

        # Attempt 1: Try using cached tokens if present
        try:
            client = Garmin()
            token_file = self.token_dir / "garmin_tokens.json" if self.token_dir.is_dir() else self.token_dir
            if token_file.exists():
                try:
                    client.client.load(str(token_str))
                    if client.client._token_expires_soon() and client.client.di_refresh_token:
                        logger.info("Garmin token expiring soon or expired, refreshing DI token...")
                        client.client._refresh_di_token()
                        client.client.dump(token_str)
                except Exception as load_err:
                    logger.debug("Direct load/refresh of token file failed: %s", load_err)

            client.login(token_str)
            self._client = client
            self._authenticated = True
            logger.info("Garmin Connect authentication successful using cached tokens at %s", token_str)
            return True
        except GarminConnectTooManyRequestsError:
            logger.error("Garmin Connect rate limit (HTTP 429) hit during cached login. Please wait before retrying.")
            raise
        except Exception as cached_exc:
            logger.warning("Cached Garmin token login failed (%s). Purging stale token cache...", cached_exc)
            token_file = self.token_dir / "garmin_tokens.json" if self.token_dir.is_dir() else self.token_dir
            if token_file.exists():
                try:
                    token_file.unlink()
                except Exception:
                    pass

        # Attempt 2: Login with username/password and persist new tokens
        if not self.username or not self.password:
            raise RuntimeError(
                f"Garmin tokens in {token_str} are missing or expired, and GARMIN_USERNAME / "
                "GARMIN_PASSWORD credentials were not provided."
            )

        try:
            client = Garmin(self.username, self.password)
            client.login()
            try:
                client.client.dump(token_str)
            except AttributeError:
                if hasattr(client, "garth"):
                    client.garth.dump(token_str)
            self._client = client
            self._authenticated = True
            logger.info("Garmin Connect authentication successful with credentials. Dumped tokens to %s", token_str)
            return True
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

        target_dt = parse_activity_datetime(activity_date_str)
        if not target_dt:
            logger.warning("Could not parse activity datetime '%s'; skipping Garmin duplicate check.", activity_date_str)
            return False

        date_key = target_dt.strftime("%Y-%m-%d")
        logger.info("Checking Garmin Connect for duplicate activities on %s (±%dh window)...", date_key, threshold_hours)

        for attempt in range(1, 4):
            try:
                existing_activities = self._client.get_activities_by_date(date_key, date_key) or []
                logger.info("Found %d existing activities on Garmin for %s", len(existing_activities), date_key)

                for act in existing_activities:
                    start_str = act.get("startTimeLocal") or act.get("startTimeGMT") or act.get("startTime")
                    act_dt = parse_activity_datetime(start_str)
                    if not act_dt:
                        continue

                    hours_diff = abs((act_dt - target_dt).total_seconds()) / 3600.0
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
                                    "Ignoring Garmin activity '%s' (%s) at %s because it is cycling while target is %s.",
                                    existing_title,
                                    type_key,
                                    format_local_datetime(act_dt),
                                    norm_target,
                                )
                                continue

                        logger.warning(
                            "Found potential duplicate activity on Garmin: '%s' (%s) at %s (diff: %.1f hrs)",
                            existing_title,
                            type_key or "unknown",
                            format_local_datetime(act_dt),
                            hours_diff,
                        )
                        return True

                return False

            except Exception as exc:
                err_str = str(exc).lower()
                if ("429" in err_str or "too many requests" in err_str) and attempt < 3:
                    wait_sec = attempt * 5
                    logger.warning("Garmin Connect rate limit (HTTP 429) during duplicate check. Retrying in %ds...", wait_sec)
                    time.sleep(wait_sec)
                    continue
                logger.warning("Error querying Garmin Connect activities for duplicate check: %s. Continuing.", exc)
                return False

    def upload_activity(self, fit_file_path: str) -> Dict[str, Any]:
        """Upload a FIT file to Garmin Connect.

        Args:
            fit_file_path: Absolute path to the .fit file.

        Returns:
            Dictionary containing status ('uploaded', 'skipped_duplicate') and details.
        """
        self.ensure_authenticated()

        if not os.path.exists(fit_file_path):
            raise FileNotFoundError(f"FIT file not found for upload: {fit_file_path}")

        file_size = os.path.getsize(fit_file_path)
        logger.info("Uploading %s (%d bytes) to Garmin Connect...", os.path.basename(fit_file_path), file_size)

        for attempt in range(1, 4):
            try:
                response = self._client.upload_activity(fit_file_path)
                logger.info("Upload to Garmin Connect successful! Response: %s", response)
                return {"status": "uploaded", "response": response}

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

                logger.exception("Garmin Connect upload failed: %s", exc)
                raise RuntimeError(f"Garmin Connect upload failed: {exc}") from exc


if __name__ == "__main__":
    import argparse
    import getpass
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="Authenticate with Garmin Connect and save tokens.")
    parser.add_argument("--token-dir", default=os.getenv("GARMIN_TOKEN_DIR", "data/garmin_tokens"), help="Directory to save Garmin tokens")
    parser.add_argument("--username", default=os.getenv("GARMIN_USERNAME"), help="Garmin Connect email")
    parser.add_argument("--password", default=os.getenv("GARMIN_PASSWORD"), help="Garmin Connect password")
    args = parser.parse_args()

    email = args.username or input("Garmin Connect Email: ").strip()
    password = args.password or getpass.getpass("Garmin Connect Password: ").strip()

    from garminconnect import Garmin

    def mfa_prompt():
        return input("Garmin MFA/2FA Verification Code: ").strip()

    print(f"Authenticating with Garmin Connect as {email}...")
    token_path = Path(args.token_dir)
    token_path.mkdir(parents=True, exist_ok=True)

    try:
        client = Garmin(email, password, prompt_mfa=mfa_prompt)
        client.login()
        try:
            client.client.dump(str(token_path))
        except AttributeError:
            if hasattr(client, "garth"):
                client.garth.dump(str(token_path))
        print(f"Success! Garmin tokens saved to {token_path}")
    except Exception as exc:
        print(f"Authentication failed: {exc}", file=sys.stderr)
        sys.exit(1)
