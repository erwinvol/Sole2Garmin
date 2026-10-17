"""Strava API client for fetching workouts recorded via Sole+ app sync."""

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


class StravaClient:
    """Client for pulling activities and streams from Strava API."""

    AUTH_URL = "https://www.strava.com/oauth/token"
    API_BASE_URL = "https://www.strava.com/api/v3"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        token_dir: Optional[Path] = None,
        filter_sole_only: bool = True,
        sample_dir: Optional[Path] = None,
        walk_run_threshold_kmh: float = 6.0,
    ):
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.refresh_token = refresh_token.strip()
        self.token_dir = token_dir or Path("data/strava_tokens")
        self.filter_sole_only = filter_sole_only
        self.sample_dir = sample_dir or Path("data/sample_activities")
        self.walk_run_threshold_kmh = float(walk_run_threshold_kmh)

        self.access_token: Optional[str] = None
        self.token_expires_at: float = 0.0

        self._load_cached_token()

    def _token_file_path(self) -> Path:
        return self.token_dir / "strava_token.json"

    def _load_cached_token(self) -> bool:
        """Load cached Strava tokens from disk."""
        token_file = self._token_file_path()
        if not token_file.exists():
            return False

        try:
            with open(token_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            cached_refresh = data.get("refresh_token")
            if cached_refresh:
                self.refresh_token = cached_refresh

            expires_at = data.get("expires_at", 0)
            if expires_at > time.time() + 120 and data.get("access_token"):
                self.access_token = data.get("access_token")
                self.token_expires_at = expires_at
                logger.info("Loaded active cached Strava access token.")
                return True
        except Exception as exc:
            logger.debug("Could not read cached Strava token: %s", exc)

        return False

    def _save_cached_token(self) -> None:
        """Save access and refresh tokens to disk."""
        try:
            self.token_dir.mkdir(parents=True, exist_ok=True)
            token_file = self._token_file_path()
            with open(token_file, "w", encoding="utf-8") as f:
                json.dump({
                    "access_token": self.access_token,
                    "refresh_token": self.refresh_token,
                    "expires_at": self.token_expires_at,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }, f, indent=2)
            logger.debug("Persisted Strava token to %s", token_file)
        except Exception as exc:
            logger.warning("Could not persist Strava token: %s", exc)

    def refresh_access_token(self) -> bool:
        """Exchange refresh token for an active bearer access token."""
        if not self.client_id or not self.client_secret or not self.refresh_token:
            logger.warning("Strava credentials not fully configured (client_id, secret, or refresh_token missing).")
            return False

        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "grant_type": "refresh_token",
        }

        try:
            resp = requests.post(self.AUTH_URL, data=payload, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                self.access_token = data.get("access_token")
                self.refresh_token = data.get("refresh_token") or self.refresh_token
                self.token_expires_at = data.get("expires_at", time.time() + 21600)
                self._save_cached_token()
                logger.info("Strava access token refreshed successfully.")
                return True
            else:
                logger.error("Failed to refresh Strava token (HTTP %d): %s", resp.status_code, resp.text)
        except RequestException as exc:
            logger.error("Network error refreshing Strava token: %s", exc)

        return False

    def ensure_authenticated(self) -> bool:
        """Verify token is active; refresh if expired."""
        if self.access_token and self.token_expires_at > time.time() + 60:
            return True
        return self.refresh_access_token()

    def get_activities(self, limit: int = 10, page: int = 1) -> List[Dict[str, Any]]:
        """Fetch activities from Strava or fallback sample directory."""
        activities: List[Dict[str, Any]] = []

        if self.ensure_authenticated():
            try:
                headers = {"Authorization": f"Bearer {self.access_token}"}
                params = {"per_page": max(limit, 10), "page": page}
                resp = requests.get(f"{self.API_BASE_URL}/athlete/activities", headers=headers, params=params, timeout=20)
                if resp.status_code == 200:
                    raw_list = resp.json()
                    logger.info("Retrieved %d raw activities from Strava API.", len(raw_list))
                    for item in raw_list:
                        if self.filter_sole_only and not self._is_sole_workout(item):
                            logger.debug("Skipping non-Sole activity: %s (type: %s)", item.get("name"), item.get("type"))
                            continue

                        # Fetch stream telemetry if available
                        act_id = item.get("id")
                        streams = self._fetch_activity_streams(act_id)
                        norm = self._normalize_strava_activity(item, streams)
                        if norm:
                            activities.append(norm)
                else:
                    logger.warning("Strava get activities returned HTTP %d: %s", resp.status_code, resp.text)
            except RequestException as exc:
                logger.warning("Failed fetching Strava activities: %s", exc)

        # Fallback to sample directory if present
        if self.sample_dir and self.sample_dir.exists():
            local_files = sorted(self.sample_dir.glob("*.json"), key=os.path.getmtime, reverse=True)
            for jf in local_files[:limit]:
                try:
                    with open(jf, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    items = data if isinstance(data, list) else [data]
                    for item in items:
                        norm = self._normalize_strava_activity(item)
                        if norm and norm["id"] not in {a["id"] for a in activities}:
                            activities.append(norm)
                except Exception as exc:
                    logger.debug("Could not read local activity JSON %s: %s", jf, exc)

        return activities[:limit]

    def _fetch_activity_streams(self, activity_id: int) -> Dict[str, Any]:
        """Fetch detailed second-by-second telemetry streams for a Strava activity."""
        if not activity_id:
            return {}

        headers = {"Authorization": f"Bearer {self.access_token}"}
        keys = "time,distance,velocity_smooth,heartrate,cadence,altitude,grade_smooth"
        url = f"{self.API_BASE_URL}/activities/{activity_id}/streams"
        params = {"keys": keys, "key_by_type": "true"}

        try:
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            else:
                logger.debug("Strava streams returned HTTP %d for activity %s", resp.status_code, activity_id)
        except RequestException as exc:
            logger.debug("Network error fetching streams for activity %s: %s", activity_id, exc)

        return {}

    def _is_sole_workout(self, act: Dict[str, Any]) -> bool:
        """Determine whether an activity originated from Sole Fitness equipment."""
        name = str(act.get("name", "")).lower()
        device_name = str(act.get("device_name", "")).lower()
        external_id = str(act.get("external_id", "")).lower()
        sport_type = str(act.get("sport_type") or act.get("type", "")).lower()
        is_trainer = bool(act.get("trainer", False))

        # Check explicit keywords
        keywords = ("sole", "dyaco", "f63", "f80", "f85", "tt8", "e25", "e35", "e95", "sb700", "sb900")
        for kw in keywords:
            if kw in name or kw in device_name or kw in external_id:
                return True

        # If user relaxed filter: match any indoor run, walk, or indoor bike session
        if not self.filter_sole_only:
            if is_trainer or sport_type in ("virtualrun", "virtualride", "elliptical"):
                return True
            if sport_type in ("run", "walk") and is_trainer:
                return True

        return False

    def _normalize_strava_activity(
        self,
        raw: Dict[str, Any],
        streams: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Normalize Strava activity and streams into standard Sole2Garmin dictionary."""
        act_id = f"strava_{raw.get('id')}"
        name = raw.get("name", "Sole Activity")
        date_str = raw.get("start_date") or raw.get("start_date_local") or datetime.now(timezone.utc).isoformat()

        duration = float(raw.get("moving_time") or raw.get("elapsed_time") or 0.0)
        distance = float(raw.get("distance") or 0.0)
        calories = int(raw.get("calories") or (raw.get("kilojoules", 0) * 1.1) or 0)

        avg_speed = float(raw.get("average_speed") or (distance / duration if duration > 0 else 0.0))
        avg_speed_kmh = avg_speed * 3.6
        max_speed = float(raw.get("max_speed") or (avg_speed * 1.15))

        sport_type = str(raw.get("sport_type") or raw.get("type", "Run")).upper()
        if "RIDE" in sport_type or "CYCLE" in sport_type:
            sport = "CYCLING"
            sub_sport = "INDOOR_CYCLING"
        elif "ELLIPTICAL" in sport_type:
            sport = "FITNESS_EQUIPMENT"
            sub_sport = "ELLIPTICAL"
        elif "WALK" in sport_type or (self.walk_run_threshold_kmh > 0 and avg_speed_kmh <= self.walk_run_threshold_kmh):
            sport = "WALKING"
            sub_sport = "GENERIC"
            if name == "Sole Activity":
                name = "Sole Walking"
        else:
            sport = "RUNNING"
            sub_sport = "INDOOR_RUNNING"
            if name == "Sole Activity":
                name = "Sole Indoor Running"

        avg_hr = int(raw.get("average_heartrate") or 0)
        max_hr = int(raw.get("max_heartrate") or (avg_hr + 15 if avg_hr > 0 else 0))

        avg_cadence = int(raw.get("average_cadence") or 0)
        total_ascent = float(raw.get("total_elevation_gain") or 0.0)

        # Parse streams into data_points if present
        data_points: List[Dict[str, Any]] = []
        if streams:
            times = streams.get("time", {}).get("data", [])
            distances = streams.get("distance", {}).get("data", [])
            speeds = streams.get("velocity_smooth", {}).get("data", [])
            hrs = streams.get("heartrate", {}).get("data", [])
            cadences = streams.get("cadence", {}).get("data", [])
            altitudes = streams.get("altitude", {}).get("data", [])
            grades = streams.get("grade_smooth", {}).get("data", [])

            base_ms = int(datetime.fromisoformat(date_str.replace("Z", "+00:00")).timestamp() * 1000)
            for i in range(len(times)):
                pt: Dict[str, Any] = {"timestamp": base_ms + int(times[i] * 1000)}
                if i < len(distances):
                    pt["distance"] = float(distances[i])
                if i < len(speeds):
                    pt["speed"] = float(speeds[i])
                if i < len(hrs):
                    pt["heart_rate"] = int(hrs[i])
                if i < len(cadences):
                    pt["cadence"] = int(cadences[i])
                if i < len(altitudes):
                    pt["altitude"] = float(altitudes[i])
                if i < len(grades):
                    pt["incline"] = float(grades[i])
                data_points.append(pt)

        return {
            "id": act_id,
            "name": name,
            "date": date_str,
            "sport": sport,
            "sub_sport": sub_sport,
            "duration_seconds": duration,
            "distance_meters": distance,
            "calories": calories,
            "avg_speed_mps": avg_speed,
            "max_speed_mps": max_speed,
            "avg_heart_rate": avg_hr,
            "max_heart_rate": max_hr,
            "avg_cadence": avg_cadence,
            "max_cadence": avg_cadence + 10 if avg_cadence > 0 else 0,
            "avg_incline_pct": 1.0,
            "max_incline_pct": 2.0,
            "total_ascent_meters": total_ascent,
            "data_points": data_points,
            "raw_fit_path": None,
        }
