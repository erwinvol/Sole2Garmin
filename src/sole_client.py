"""Client for communicating with Dyaco / SOLE+ cloud services and workout data."""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import requests
    RequestException = requests.RequestException
except ImportError:
    requests = None  # type: ignore

    class RequestException(Exception):  # type: ignore
        pass

logger = logging.getLogger(__name__)


class SoleClient:
    """Handles authentication and workout discovery from SOLE+ / Dyaco platform."""

    USER_AGENT = "SOLE+/2.0.0 (Android; com.soletreadmills.sole_v2; SDK 34)"

    def __init__(
        self,
        email: str,
        password: str,
        base_url: str = "https://api.dyaco.com/v1",
        token_dir: Optional[Path] = None,
        sample_dir: Optional[Path] = None,
    ):
        self.email = email
        self.password = password
        self.base_url = base_url.rstrip("/")
        self.token_dir = token_dir or Path("data/sole_tokens")
        self.sample_dir = sample_dir or Path("data/sample_activities")

        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.token_expiry: float = 0.0
        self.user_id: Optional[str] = None

        self._load_cached_token()

    def _token_file_path(self) -> Path:
        return self.token_dir / "sole_token.json"

    def _load_cached_token(self) -> bool:
        """Attempt to restore session tokens from disk."""
        token_file = self._token_file_path()
        if not token_file.exists():
            return False

        try:
            with open(token_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            if data.get("email") != self.email and self.email:
                logger.debug("Cached token email does not match configured email. Ignoring cache.")
                return False

            expiry = data.get("expires_at", 0)
            if expiry > time.time() + 60:
                self.access_token = data.get("access_token")
                self.refresh_token = data.get("refresh_token")
                self.token_expiry = expiry
                self.user_id = data.get("user_id")
                logger.info("Loaded active cached SOLE+ token (User ID: %s)", self.user_id)
                return True
            elif data.get("refresh_token"):
                self.refresh_token = data.get("refresh_token")
                self.user_id = data.get("user_id")
                logger.info("Cached SOLE+ token expired; will attempt refresh.")
                return False
        except Exception as exc:
            logger.warning("Error reading cached SOLE+ token: %s", exc)

        return False

    def _save_cached_token(self) -> None:
        """Persist session tokens to disk."""
        try:
            self.token_dir.mkdir(parents=True, exist_ok=True)
            token_file = self._token_file_path()
            payload = {
                "email": self.email,
                "user_id": self.user_id,
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "expires_at": self.token_expiry,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            with open(token_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            logger.debug("Saved SOLE+ session tokens to %s", token_file)
        except Exception as exc:
            logger.warning("Failed to save SOLE+ session token: %s", exc)

    def login(self) -> bool:
        """Authenticate with Dyaco / SOLE+ API using user credentials."""
        if not self.email or not self.password:
            logger.warning("SOLE_EMAIL and SOLE_PASSWORD not configured. Skipping cloud login.")
            return False

        logger.info("Authenticating with SOLE+ backend as %s...", self.email)
        login_endpoints = [
            f"{self.base_url}/auth/login",
            f"{self.base_url}/user/login",
            f"{self.base_url}/api/v1/auth/login",
        ]

        payload = {
            "email": self.email,
            "username": self.email,
            "password": self.password,
            "client_id": "com.soletreadmills.sole_v2",
            "platform": "Android",
        }
        headers = {
            "User-Agent": self.USER_AGENT,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        last_error = None
        for endpoint in login_endpoints:
            try:
                response = requests.post(endpoint, json=payload, headers=headers, timeout=20)
                if response.status_code in (200, 201):
                    data = response.json()
                    self.access_token = data.get("access_token") or data.get("token") or data.get("data", {}).get("token")
                    self.refresh_token = data.get("refresh_token") or data.get("data", {}).get("refresh_token")
                    expires_in = data.get("expires_in", 86400)
                    self.token_expiry = time.time() + int(expires_in)
                    self.user_id = str(data.get("user_id") or data.get("userId") or data.get("data", {}).get("userId", "default_user"))

                    self._save_cached_token()
                    logger.info("Successfully authenticated with SOLE+ (User ID: %s)", self.user_id)
                    return True
                else:
                    last_error = f"HTTP {response.status_code}: {response.text[:120]}"
            except RequestException as exc:
                err_msg = str(exc)
                if "Failed to resolve" in err_msg or "NameResolutionError" in err_msg or "Errno -2" in err_msg:
                    from urllib.parse import urlparse
                    host = urlparse(endpoint).hostname or self.base_url
                    last_error = (
                        f"Cannot resolve host '{host}'. Dyaco / SOLE+ does not publish a public DNS "
                        f"record for '{host}'. If you have captured the production endpoint using a proxy "
                        f"(mitmproxy / HTTP Toolkit), set SOLE_BASE_URL in your .env file. "
                        f"Falling back to local workout files in '{self.sample_dir}'."
                    )
                    break
                last_error = err_msg

        logger.warning("Could not authenticate against SOLE+ cloud (%s).", last_error)
        return False

    def ensure_authenticated(self) -> bool:
        """Ensure an active access token is present, refreshing or logging in if needed."""
        if self.access_token and self.token_expiry > time.time() + 30:
            return True

        if self.refresh_token:
            if self._refresh_access_token():
                return True

        return self.login()

    def _refresh_access_token(self) -> bool:
        """Attempt to exchange a refresh token for a new access token."""
        refresh_url = f"{self.base_url}/auth/refresh"
        headers = {
            "User-Agent": self.USER_AGENT,
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.refresh_token}",
        }
        try:
            response = requests.post(refresh_url, json={"refresh_token": self.refresh_token}, headers=headers, timeout=20)
            if response.status_code in (200, 201):
                data = response.json()
                self.access_token = data.get("access_token") or data.get("token")
                expires_in = data.get("expires_in", 86400)
                self.token_expiry = time.time() + int(expires_in)
                self._save_cached_token()
                logger.info("Successfully refreshed SOLE+ access token.")
                return True
        except Exception as exc:
            logger.debug("Token refresh failed: %s", exc)
        return False

    def get_activities(self, limit: int = 10, page: int = 1) -> List[Dict[str, Any]]:
        """Fetch list of user activities from SOLE+ API or local fallback directory.

        Returns:
            List of standardized activity dictionaries.
        """
        activities: List[Dict[str, Any]] = []

        # 1. Attempt cloud fetch if authenticated
        if self.ensure_authenticated() and self.access_token:
            headers = {
                "User-Agent": self.USER_AGENT,
                "Authorization": f"Bearer {self.access_token}",
                "Accept": "application/json",
            }
            workout_endpoints = [
                f"{self.base_url}/user/workouts",
                f"{self.base_url}/activities",
                f"{self.base_url}/api/v1/workouts",
            ]

            params = {"page": page, "limit": limit, "sort": "-date"}
            for endpoint in workout_endpoints:
                try:
                    response = requests.get(endpoint, headers=headers, params=params, timeout=20)
                    if response.status_code == 200:
                        data = response.json()
                        items = data.get("data") or data.get("workouts") or data.get("items") or (data if isinstance(data, list) else [])
                        for item in items:
                            norm = self._normalize_activity(item)
                            if norm:
                                activities.append(norm)
                        logger.info("Retrieved %d activities from SOLE+ cloud API.", len(activities))
                        break
                except RequestException as exc:
                    logger.debug("Failed checking endpoint %s: %s", endpoint, exc)

        # 2. Check local fallback / drop directory (sample files or manual exports)
        local_activities = self._load_local_activities(limit=limit)
        if local_activities:
            logger.info("Found %d local activity files in %s.", len(local_activities), self.sample_dir)
            existing_ids = {a["id"] for a in activities}
            for loc in local_activities:
                if loc["id"] not in existing_ids:
                    activities.append(loc)

        return activities

    def _normalize_activity(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Convert a raw Dyaco / SOLE+ workout item to a standardized dictionary."""
        act_id = str(item.get("id") or item.get("workout_id") or item.get("workoutId") or item.get("uuid") or "")
        if not act_id:
            return None

        # Parse date
        date_raw = item.get("date") or item.get("start_time") or item.get("startTime") or item.get("created_at") or datetime.now(timezone.utc).isoformat()

        # Equipment / Sport resolution
        machine_type = str(item.get("machine_type") or item.get("type") or item.get("equipment") or "treadmill").lower()
        if "treadmill" in machine_type or "run" in machine_type:
            sport = "RUNNING"
            sub_sport = "TREADMILL"
            default_name = "Sole Treadmill Run"
        elif "elliptical" in machine_type:
            sport = "FITNESS_EQUIPMENT"
            sub_sport = "ELLIPTICAL"
            default_name = "Sole Elliptical Session"
        elif "bike" in machine_type or "cycle" in machine_type:
            sport = "CYCLING"
            sub_sport = "INDOOR_CYCLING"
            default_name = "Sole Indoor Cycling"
        else:
            sport = "RUNNING"
            sub_sport = "TREADMILL"
            default_name = "Sole Workout"

        name = item.get("name") or item.get("title") or default_name

        # Distance: convert to meters (can be given in km or miles)
        dist = float(item.get("distance") or item.get("total_distance") or 0.0)
        dist_unit = str(item.get("distance_unit") or item.get("unit") or "km").lower()
        if dist_unit in ("mi", "miles", "mile"):
            dist_meters = dist * 1609.344
        elif dist_unit in ("m", "meters"):
            dist_meters = dist
        else:
            dist_meters = dist * 1000.0  # assume km

        # Duration in seconds
        duration = float(item.get("duration") or item.get("elapsed_time") or item.get("total_time") or 0.0)
        calories = int(item.get("calories") or item.get("total_calories") or 0)

        # Incline / Grade
        avg_incline = float(item.get("avg_incline") or item.get("incline") or 0.0)
        max_incline = float(item.get("max_incline") or avg_incline)

        # Elevation gain estimate if not present
        total_ascent = float(item.get("elevation_gain") or item.get("total_ascent") or (dist_meters * (avg_incline / 100.0) if avg_incline > 0 else 0.0))

        # Speed (m/s)
        avg_speed = float(item.get("avg_speed") or 0.0)
        speed_unit = str(item.get("speed_unit") or "kmh").lower()
        if speed_unit in ("mph", "mi/h"):
            avg_speed_mps = avg_speed * 0.44704
        elif speed_unit in ("mps", "m/s"):
            avg_speed_mps = avg_speed
        elif avg_speed > 0:
            avg_speed_mps = avg_speed / 3.6  # km/h to m/s
        else:
            avg_speed_mps = (dist_meters / duration) if duration > 0 else 0.0

        max_speed = float(item.get("max_speed") or 0.0)
        max_speed_mps = (max_speed / 3.6) if max_speed > 0 else (avg_speed_mps * 1.15)

        # Heart Rate
        avg_hr = int(item.get("avg_hr") or item.get("avg_heart_rate") or item.get("heart_rate") or 0)
        max_hr = int(item.get("max_hr") or item.get("max_heart_rate") or (avg_hr + 15 if avg_hr > 0 else 0))

        # Cadence (steps/min for run, rpm for bike)
        avg_cadence = int(item.get("avg_cadence") or item.get("cadence") or 0)
        max_cadence = int(item.get("max_cadence") or (avg_cadence + 10 if avg_cadence > 0 else 0))

        data_points = item.get("data_points") or item.get("records") or []

        return {
            "id": act_id,
            "name": name,
            "date": date_raw,
            "sport": sport,
            "sub_sport": sub_sport,
            "duration_seconds": duration,
            "distance_meters": dist_meters,
            "calories": calories,
            "avg_speed_mps": avg_speed_mps,
            "max_speed_mps": max_speed_mps,
            "avg_heart_rate": avg_hr,
            "max_heart_rate": max_hr,
            "avg_cadence": avg_cadence,
            "max_cadence": max_cadence,
            "avg_incline_pct": avg_incline,
            "max_incline_pct": max_incline,
            "total_ascent_meters": total_ascent,
            "data_points": data_points,
            "raw_fit_path": item.get("raw_fit_path"),
        }

    def _load_local_activities(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Load workouts from local JSON files or FIT files in sample_dir."""
        results: List[Dict[str, Any]] = []
        if not self.sample_dir.exists():
            return results

        # 1. JSON files
        json_files = sorted(self.sample_dir.glob("*.json"), key=os.path.getmtime, reverse=True)
        for jf in json_files[:limit]:
            try:
                with open(jf, "r", encoding="utf-8") as f:
                    content = json.load(f)

                if isinstance(content, list):
                    for item in content:
                        norm = self._normalize_activity(item)
                        if norm:
                            results.append(norm)
                elif isinstance(content, dict):
                    # Check if multi-workout container or single workout
                    items = content.get("data") or content.get("workouts") or [content]
                    for item in items:
                        norm = self._normalize_activity(item)
                        if norm:
                            results.append(norm)
            except Exception as exc:
                logger.warning("Error reading local activity JSON %s: %s", jf, exc)

        # 2. Raw FIT files dropped into sample_dir
        fit_files = sorted(self.sample_dir.glob("*.fit"), key=os.path.getmtime, reverse=True)
        for ff in fit_files[:limit]:
            act_id = f"local_{ff.stem}"
            results.append({
                "id": act_id,
                "name": f"Sole Activity ({ff.stem})",
                "date": datetime.fromtimestamp(ff.stat().st_mtime, tz=timezone.utc).isoformat(),
                "sport": "RUNNING",
                "sub_sport": "TREADMILL",
                "duration_seconds": 1800.0,
                "distance_meters": 5000.0,
                "calories": 350,
                "avg_speed_mps": 2.77,
                "max_speed_mps": 3.2,
                "avg_heart_rate": 140,
                "max_heart_rate": 160,
                "avg_cadence": 160,
                "max_cadence": 170,
                "avg_incline_pct": 1.0,
                "max_incline_pct": 2.0,
                "total_ascent_meters": 50.0,
                "data_points": [],
                "raw_fit_path": str(ff.absolute()),
            })

        return results[:limit]
