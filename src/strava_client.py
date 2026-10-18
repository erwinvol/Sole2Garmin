"""Strava API client for discovering and streaming Sole activities."""

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
    """Client for authenticating with Strava and retrieving Sole activities and telemetry streams."""

    TOKEN_URL = "https://www.strava.com/oauth/token"
    ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"
    STREAM_URL_TEMPLATE = "https://www.strava.com/api/v3/activities/{activity_id}/streams"

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
        """Save active Strava tokens to disk."""
        try:
            self.token_dir.mkdir(parents=True, exist_ok=True)
            token_file = self._token_file_path()
            payload = {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "expires_at": self.token_expires_at,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            with open(token_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            logger.debug("Saved Strava session token to %s", token_file)
        except Exception as exc:
            logger.warning("Failed to save Strava token: %s", exc)

    def refresh_access_token(self) -> bool:
        """Obtain a new access token using the refresh token."""
        if not self.client_id or not self.client_secret or not self.refresh_token:
            logger.warning("Strava credentials (CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN) not configured.")
            return False

        logger.info("Refreshing Strava OAuth access token...")
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
        }

        try:
            response = requests.post(self.TOKEN_URL, data=payload, timeout=20)
            if response.status_code == 200:
                data = response.json()
                self.access_token = data["access_token"]
                self.refresh_token = data.get("refresh_token", self.refresh_token)
                self.token_expires_at = float(data.get("expires_at", time.time() + 21600))
                self._save_cached_token()
                logger.info("Successfully refreshed Strava OAuth token.")
                return True
            else:
                logger.error("Strava token refresh failed: HTTP %d: %s", response.status_code, response.text[:120])
                return False
        except RequestException as exc:
            logger.error("Network error during Strava token refresh: %s", exc)
            return False

    def ensure_authenticated(self) -> bool:
        """Ensure an active, non-expired access token is available."""
        if self.access_token and self.token_expires_at > time.time() + 60:
            return True
        return self.refresh_access_token()

    def get_activities(self, limit: int = 10, page: int = 1) -> List[Dict[str, Any]]:
        """Fetch activities from Strava filtered for Sole workouts and local sample activities."""
        results: List[Dict[str, Any]] = []

        if self.ensure_authenticated() and self.access_token:
            headers = {"Authorization": f"Bearer {self.access_token}"}
            params = {"per_page": max(limit * 2, 20), "page": page}

            try:
                logger.info("Fetching recent activities from Strava (Page %d)...", page)
                response = requests.get(self.ACTIVITIES_URL, headers=headers, params=params, timeout=25)
                if response.status_code == 200:
                    raw_activities = response.json()
                    logger.info("Retrieved %d total activities from Strava. Filtering for Sole workouts...", len(raw_activities))

                    for raw_act in raw_activities:
                        if self._is_sole_workout(raw_act):
                            act_id = str(raw_act["id"])
                            # Fetch high-resolution time-series streams
                            streams = self.get_activity_streams(act_id)
                            norm = self._normalize_strava_activity(raw_act, streams)
                            if norm:
                                results.append(norm)

                            if len(results) >= limit:
                                break
                else:
                    logger.warning("Strava get_activities returned HTTP %d: %s", response.status_code, response.text[:100])
            except RequestException as exc:
                logger.warning("Error fetching Strava activities: %s", exc)

        # Merge local drop folder / sample activities
        local_items = self._load_local_activities(limit=limit)
        existing_ids = {a["id"] for a in results}
        for item in local_items:
            if item["id"] not in existing_ids:
                results.append(item)

        logger.info("Found %d eligible Sole activities to process.", len(results))
        return results[:limit]

    def get_activity_streams(self, activity_id: str) -> Dict[str, Any]:
        """Fetch second-by-second telemetry streams for an activity from Strava."""
        if not self.ensure_authenticated() or not self.access_token:
            return {}

        url = self.STREAM_URL_TEMPLATE.format(activity_id=activity_id)
        headers = {"Authorization": f"Bearer {self.access_token}"}
        params = {
            "keys": "time,distance,velocity_smooth,grade_smooth,altitude,heartrate,cadence,watts",
            "key_by_type": "true",
        }

        try:
            logger.debug("Fetching telemetry streams for Strava activity %s...", activity_id)
            resp = requests.get(url, headers=headers, params=params, timeout=25)
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
            sub_sport = "INDOOR_WALKING"
            if name in ("Sole Activity", "Sole Walking"):
                name = "Sole Treadmill Indoor Walking"
        else:
            sport = "RUNNING"
            sub_sport = "INDOOR_RUNNING"
            if name in ("Sole Activity", "Sole Indoor Running"):
                name = "Sole Treadmill Indoor Run"

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
            grades = streams.get("grade_smooth", {}).get("data", [])
            altitudes = streams.get("altitude", {}).get("data", [])
            hrs = streams.get("heartrate", {}).get("data", [])
            cadences = streams.get("cadence", {}).get("data", [])

            # Compute elevation from grade if elevation is flat
            computed_alt = altitudes[0] if altitudes else 100.0
            last_dist = 0.0

            from src.fit_builder import parse_timestamp_ms
            base_time_ms = parse_timestamp_ms(date_str)

            for i in range(len(times)):
                t_sec = times[i]
                d_m = distances[i] if i < len(distances) else (avg_speed * t_sec)
                spd = speeds[i] if i < len(speeds) else avg_speed
                grd = grades[i] if i < len(grades) else 0.0
                hr = hrs[i] if i < len(hrs) else avg_hr
                cad = cadences[i] if i < len(cadences) else avg_cadence

                if altitudes and i < len(altitudes):
                    alt = altitudes[i]
                else:
                    dist_delta = max(d_m - last_dist, 0.0)
                    computed_alt += dist_delta * (grd / 100.0)
                    alt = computed_alt
                last_dist = d_m

                data_points.append({
                    "timestamp": base_time_ms + int(t_sec * 1000),
                    "distance": d_m,
                    "speed": spd,
                    "incline": grd,
                    "altitude": alt,
                    "heart_rate": hr,
                    "cadence": cad,
                })

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
            "total_ascent_meters": total_ascent,
            "data_points": data_points,
        }

    def _load_local_activities(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Load workouts from local JSON files or FIT files in sample_dir."""
        results: List[Dict[str, Any]] = []
        if not self.sample_dir.exists():
            return results

        json_files = sorted(self.sample_dir.glob("*.json"), key=os.path.getmtime, reverse=True)
        for jf in json_files[:limit]:
            try:
                with open(jf, "r", encoding="utf-8") as f:
                    content = json.load(f)

                items = content if isinstance(content, list) else (content.get("workouts") or [content])
                for item in items:
                    act_id = str(item.get("id") or f"local_{jf.stem}")
                    results.append({
                        "id": act_id,
                        "name": item.get("name", f"Sole Activity ({jf.stem})"),
                        "date": item.get("date", datetime.now(timezone.utc).isoformat()),
                        "sport": item.get("sport", "RUNNING").upper(),
                        "sub_sport": item.get("sub_sport", "TREADMILL").upper(),
                        "duration_seconds": float(item.get("duration", 1800.0)),
                        "distance_meters": float(item.get("distance", 5000.0)),
                        "calories": int(item.get("calories", 350)),
                        "avg_speed_mps": float(item.get("avg_speed_mps", 2.77)),
                        "max_speed_mps": float(item.get("max_speed_mps", 3.2)),
                        "avg_heart_rate": int(item.get("avg_heart_rate", 145)),
                        "max_heart_rate": int(item.get("max_heart_rate", 160)),
                        "avg_cadence": int(item.get("avg_cadence", 160)),
                        "max_cadence": int(item.get("max_cadence", 170)),
                        "avg_incline_pct": float(item.get("avg_incline", 1.0)),
                        "total_ascent_meters": float(item.get("elevation_gain", 50.0)),
                        "data_points": item.get("data_points", []),
                    })
            except Exception as exc:
                logger.warning("Error reading local activity JSON %s: %s", jf, exc)

        return results[:limit]
