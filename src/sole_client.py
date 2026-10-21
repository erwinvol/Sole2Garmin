"""Client for communicating with Dyaco / SOLE+ cloud services and workout data.

Reverse-engineered from com.soletreadmills.sole_v2 (SOLE+ Android app)
DyacoApiService & HeaderInterceptor specifications.
"""

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

try:
    import requests
    RequestException = requests.RequestException
except ImportError:
    requests = None  # type: ignore

    class RequestException(Exception):  # type: ignore
        pass

logger = logging.getLogger(__name__)


class SoleClient:
    """Handles authentication and workout ingestion from SOLE+ / Dyaco cloud backend."""

    DEFAULT_BASE_URL = "https://sole-homeplus.dyacocloud.com"
    APP_NAME = "HOME_ANDROID"
    APP_VERSION = "4.36"
    BRAND_CODE = "sole"
    COUNTRY_CODE = "US"
    DEVICE_INFO = "Google Pixel 8"
    CLIENT_DEVICE_ID = "sole2garmin-bridge-client"

    def __init__(
        self,
        email: str,
        password: str,
        base_url: str = DEFAULT_BASE_URL,
        token_dir: Optional[Path] = None,
        sample_dir: Optional[Path] = None,
        walk_run_threshold_kmh: float = 6.0,
    ):
        def _clean_cred(s: Optional[str]) -> str:
            v = (s or "").strip()
            if len(v) >= 2 and ((v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'"))):
                return v[1:-1].strip()
            return v

        self.email = _clean_cred(email)
        self.password = _clean_cred(password)
        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self.token_dir = token_dir or Path("data/sole_tokens")
        self.sample_dir = sample_dir or Path("data/sample_activities")
        self.walk_run_threshold_kmh = float(walk_run_threshold_kmh)

        self.session_id: Optional[str] = None
        self.login_token: Optional[str] = None
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.token_expiry: float = 0.0
        self.user_id: Optional[str] = None
        self.push_token: str = f"sole2garmin_fcm_{uuid.uuid4().hex}"

        self._load_cached_token()

    def _token_file_path(self) -> Path:
        return self.token_dir / "sole_token.json"

    def _load_cached_token(self) -> bool:
        """Attempt to restore session tokens and session ID from disk."""
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
                self.session_id = data.get("session_id")
                self.login_token = data.get("login_token") or data.get("access_token")
                self.access_token = self.login_token
                self.refresh_token = data.get("refresh_token")
                self.token_expiry = expiry
                self.user_id = data.get("user_id")
                logger.info("Loaded active cached SOLE+ session (User ID: %s, Session ID: %s)", self.user_id, self.session_id)
                return True
            elif data.get("refresh_token"):
                self.refresh_token = data.get("refresh_token")
                self.session_id = data.get("session_id")
                self.user_id = data.get("user_id")
                logger.info("Cached SOLE+ session expired; will re-authenticate.")
                return False
        except Exception as exc:
            logger.warning("Error reading cached SOLE+ token: %s", exc)

        return False

    def _save_cached_token(self) -> None:
        """Persist session token and session ID to disk."""
        try:
            self.token_dir.mkdir(parents=True, exist_ok=True)
            token_file = self._token_file_path()
            payload = {
                "email": self.email,
                "user_id": self.user_id,
                "session_id": self.session_id,
                "login_token": self.login_token,
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "expires_at": self.token_expiry,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            with open(token_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            logger.debug("Saved SOLE+ session to %s", token_file)
        except Exception as exc:
            logger.warning("Failed to save SOLE+ session: %s", exc)

    def _get_headers(self) -> Dict[str, str]:
        """Generate HTTP headers matching Dyaco Android HeaderInterceptor."""
        now_dt = datetime.now(timezone.utc)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "app": self.APP_NAME,
            "appVersion": self.APP_VERSION,
            "now": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "nowMillis": str(int(now_dt.timestamp() * 1000)),
            "timeZone": os.environ.get("TZ", "America/New_York"),
            "lang": "en",
            "deviceInfo": self.DEVICE_INFO,
            "clientDeviceId": self.CLIENT_DEVICE_ID,
            "brandCode": self.BRAND_CODE,
            "countryCode": self.COUNTRY_CODE,
            "sessionId": self.session_id or "",
            "sessionid": self.session_id or "",
        }
        if self.session_id:
            headers["Cookie"] = f"sessionid={self.session_id}; sessionId={self.session_id}; JSESSIONID={self.session_id}"
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
            headers["token"] = self.access_token
        return headers

    def login(self) -> bool:
        """Authenticate with Dyaco / SOLE+ API via POST api/LoginByEmail.

        Implements exact flow reverse-engineered from LoginFragment$submitLoginByEmail$1:
        1. Resolve assigned cluster server address via POST api/GetLoginServerAddress
        2. Authenticate directly against the assigned cluster via POST api/LoginByEmail
        """
        if not self.email or not self.password:
            logger.warning("SOLE_EMAIL and SOLE_PASSWORD not configured. Skipping cloud login.")
            return False

        # Step 1: Pre-login cluster resolution via Dyaco global gateway
        resolved_server = self._resolve_login_server_address()
        if resolved_server:
            self.base_url = resolved_server

        logger.info("Authenticating with SOLE+ backend (%s) as %s...", self.base_url, self.email)

        payload = {
            "email": self.email,
            "emailOrUserUuidInIdentityProvider": self.email,
            "password": self.password,
            "pushToken": self.push_token,
            "pushMessageToken": self.push_token,
        }

        # Primary route reverse-engineered from DyacoApiService: POST api/LoginByEmail
        login_endpoints = [
            f"{self.base_url}/api/LoginByEmail",
        ]

        last_error = None
        for endpoint in login_endpoints:
            try:
                headers = self._get_headers()
                response = requests.post(endpoint, json=payload, headers=headers, timeout=20)
                if response.status_code in (200, 201):
                    data = response.json()
                    is_success = bool(data.get("success", False))

                    if not is_success:
                        err_code = data.get("errorCode")
                        err_msg = data.get("errorMessage") or data.get("errorMsg") or "Login credentials rejected"
                        logger.error(
                            "SOLE+ authentication rejected by %s: %s (ErrorCode: %s, Full Response: %s)",
                            endpoint,
                            err_msg,
                            err_code,
                            data,
                        )
                        if err_code == 112:
                            logger.error(
                                "SOLE+ error 112 (LOGIN_ERR_WRONG_PWD_OR_USERNAME): Invalid account or password. "
                                "Please verify SOLE_EMAIL and SOLE_PASSWORD.",
                            )
                            # Diagnose registration status via Dyaco global gateway
                            exists_email = self.check_user_exists(register_type=1)
                            if exists_email is False:
                                exists_google = self.check_user_exists(register_type=2)
                                if exists_google is True:
                                    logger.error(
                                        "Account diagnostic: '%s' is registered via 'Sign in with Google' (RegisterType.GOOGLE), "
                                        "not direct email. Please use the SOLE+ mobile app 'Forgot Password' screen to set a direct "
                                        "password for %s, or register a direct email account.",
                                        self.email,
                                        self.email,
                                    )
                                else:
                                    logger.error(
                                        "Account diagnostic: '%s' was NOT found in Dyaco's user database (IsUserExists returned false).",
                                        self.email,
                                    )
                            elif exists_email is True:
                                logger.info("Account diagnostic: '%s' is confirmed to exist as a direct email account on Dyaco.", self.email)
                        last_error = f"{err_msg} (ErrorCode: {err_code})"
                        continue

                    # Extract sessionid from response headers (HeaderInterceptor convention)
                    resp_headers = {k.lower(): v for k, v in response.headers.items()}
                    session_from_header = resp_headers.get("sessionid") or resp_headers.get("session_id")

                    session_from_cookie = None
                    cookies = getattr(response, "cookies", None)
                    if cookies is not None:
                        try:
                            if isinstance(cookies, dict):
                                session_from_cookie = cookies.get("sessionid") or cookies.get("sessionId")
                            elif hasattr(cookies, "get"):
                                session_from_cookie = cookies.get("sessionid") or cookies.get("sessionId")
                        except Exception:
                            pass

                    self.session_id = None
                    if isinstance(session_from_header, str):
                        self.session_id = session_from_header
                    elif isinstance(session_from_cookie, str):
                        self.session_id = session_from_cookie

                    data_map = data.get("dataMap") if isinstance(data.get("dataMap"), dict) else {}
                    raw_user_data = data_map.get("data")
                    user_data = raw_user_data if isinstance(raw_user_data, dict) else {}

                    login_token = data_map.get("loginToken") or data.get("access_token") or data.get("token")
                    self.login_token = str(login_token) if isinstance(login_token, str) else None
                    self.access_token = self.login_token
                    refresh_token = data_map.get("serviceLoginToken") or data.get("refresh_token")
                    self.refresh_token = str(refresh_token) if isinstance(refresh_token, str) else None

                    uid = user_data.get("userId") or data_map.get("userId") or data.get("user_id")
                    self.user_id = str(uid) if uid is not None else ""

                    if not self.session_id:
                        body_sess = data_map.get("sessionId") or data.get("sessionId")
                        if isinstance(body_sess, str):
                            self.session_id = body_sess

                    expires_in = data.get("expires_in", 86400 * 30)  # default 30 days session
                    try:
                        self.token_expiry = time.time() + int(expires_in)
                    except (ValueError, TypeError):
                        self.token_expiry = time.time() + 86400 * 30

                    self._save_cached_token()
                    logger.info("Successfully authenticated with SOLE+ (User ID: %s, Session ID: %s)", self.user_id, self.session_id)
                    return True
                else:
                    last_error = f"HTTP {response.status_code}: {response.text[:120]}"
            except RequestException as exc:
                err_msg = str(exc)
                if "Failed to resolve" in err_msg or "NameResolutionError" in err_msg or "Errno -2" in err_msg:
                    from urllib.parse import urlparse
                    host = urlparse(endpoint).hostname or self.base_url
                    last_error = (
                        f"Cannot resolve host '{host}'. Dyaco / SOLE+ production domain is "
                        f"'https://sole-homeplus.dyacocloud.com'. Please verify network access."
                    )
                    break
                last_error = err_msg

        logger.warning("Could not authenticate against SOLE+ cloud (%s).", last_error)
        return False

    def check_user_exists(self, register_type: int = 1) -> Optional[bool]:
        """Check if user account exists on Dyaco global gateway.

        Reverse-engineered from DyacoApiKt.callIsUserExists:
        POST https://homeglobal.dyacocloud.com/api/IsUserExists
        Body: {"registerType": 1, "emailOrUserUuidInIdentityProvider": email}
        Response: {"success": true, "dataMap": {"data": true}}
        """
        global_url = "https://homeglobal.dyacocloud.com/api/IsUserExists"
        headers = self._get_headers()
        payload = {
            "registerType": register_type,
            "email": self.email,
            "emailOrUserUuidInIdentityProvider": self.email,
            "brandCode": self.BRAND_CODE,
        }
        try:
            resp = requests.post(global_url, json=payload, headers=headers, timeout=10)
            logger.info(
                "Dyaco IsUserExists (registerType=%d) [%s]: HTTP %s, Response: %s",
                register_type,
                global_url,
                resp.status_code,
                resp.text[:300],
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success", False):
                    data_map = data.get("dataMap") or {}
                    return bool(data_map.get("data"))
        except Exception as exc:
            logger.warning("IsUserExists query failed: %s", exc)
        return None

    def _resolve_login_server_address(self, register_type: int = 1) -> Optional[str]:
        """Query Dyaco global gateway for user's assigned cluster server address.

        Reverse-engineered from LoginFragment$submitLoginByEmail$1:
        POST https://homeglobal.dyacocloud.com/api/GetLoginServerAddress
        Body: {"registerType": 1, "emailOrUserUuidInIdentityProvider": email}
        Response: {"success": true, "dataMap": {"data": "https://..."}}
        """
        global_url = "https://homeglobal.dyacocloud.com/api/GetLoginServerAddress"
        headers = self._get_headers()
        payload = {
            "registerType": register_type,
            "email": self.email,
            "emailOrUserUuidInIdentityProvider": self.email,
            "brandCode": self.BRAND_CODE,
        }
        try:
            resp = requests.post(global_url, json=payload, headers=headers, timeout=10)
            logger.info(
                "Dyaco GetLoginServerAddress (registerType=%d) [%s]: HTTP %s, Response: %s",
                register_type,
                global_url,
                resp.status_code,
                resp.text[:300],
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success", False):
                    data_map = data.get("dataMap") or {}
                    # In decompiled GetLoginServerAddressApiData$DataMap, server URL is in field "data"
                    server_addr = (
                        data_map.get("data")
                        or data_map.get("serverAddress")
                        or data_map.get("serverUrl")
                    )
                    if server_addr and isinstance(server_addr, str) and server_addr.startswith("http"):
                        clean_addr = server_addr.rstrip("/")
                        logger.info("Resolved cluster server address from Dyaco global gateway: %s", clean_addr)
                        return clean_addr
        except Exception as exc:
            logger.warning("GetLoginServerAddress query failed: %s", exc)
        return None

    def ensure_authenticated(self) -> bool:
        """Ensure an active session is present, logging in if needed."""
        if (self.session_id or self.access_token) and self.token_expiry > time.time() + 30:
            return True

        return self.login()

    def get_activities(self, limit: int = 10, page: int = 1) -> List[Dict[str, Any]]:
        """Fetch list of user activities from SOLE+ API or local fallback directory.

        Workflow:
        1. Query workout UUIDs via POST api/GetMyWorkoutDateList
        2. Query high-res telemetry & raw data via POST api/GetUserWorkoutContent
        3. Merge with any local files in sample_dir
        """
        activities: List[Dict[str, Any]] = []

        # 1. Attempt cloud fetch if authenticated
        if self.ensure_authenticated():
            try:
                cloud_activities = self._fetch_cloud_activities(limit=limit)
                activities.extend(cloud_activities)
                logger.info("Retrieved %d activities from SOLE+ cloud API.", len(activities))
            except Exception as exc:
                logger.warning("Failed fetching workouts from SOLE+ cloud: %s", exc)

        # 2. Check local fallback / drop directory (sample files or manual exports)
        local_activities = self._load_local_activities(limit=limit)
        if local_activities:
            logger.info("Found %d local activity files in %s.", len(local_activities), self.sample_dir)
            existing_ids = {a["id"] for a in activities}
            for loc in local_activities:
                if loc["id"] not in existing_ids:
                    activities.append(loc)

        return activities

    def _is_auth_error(self, err_code: Any, err_msg: Any) -> bool:
        """Determine if Dyaco API response indicates invalid/expired/missing authentication."""
        s_msg = str(err_msg or "").upper()
        try:
            code_int = int(err_code) if err_code is not None else None
        except (ValueError, TypeError):
            code_int = None

        return (
            code_int in (101, 103, 104, 113, 114, 115)
            or "LOGIN_REQUIRED" in s_msg
            or "LOGIN" in s_msg
            or "EXPIRED" in s_msg
            or "TOKEN" in s_msg
            or "UNAUTHORIZED" in s_msg
            or "NOT_LOGIN" in s_msg
        )

    def _reauthenticate_and_retry(self, limit: int, reason: str = "") -> List[Dict[str, Any]]:
        """Purge invalid/expired session tokens and attempt fresh login & fetch."""
        logger.info(
            "Dyaco session rejected (%s). Purging cached token and re-authenticating...",
            reason,
        )
        self.session_id = None
        self.access_token = None
        self.login_token = None
        self.token_expiry = 0.0
        try:
            token_file = self._token_file_path()
            if token_file.exists():
                token_file.unlink()
                logger.debug("Deleted stale cached token file: %s", token_file)
        except Exception as exc:
            logger.debug("Failed deleting stale token file: %s", exc)

        if self.login():
            logger.info("Successfully re-authenticated with SOLE+. Retrying activity fetch...")
            return self._fetch_cloud_activities(limit=limit, allow_reauth=False)
        else:
            logger.warning("Re-authentication failed after auth rejection: %s", reason)
            return []

    def _fetch_cloud_activities(self, limit: int = 10, allow_reauth: bool = True) -> List[Dict[str, Any]]:
        """Fetch workout list and detailed telemetry from Dyaco cloud endpoints.

        Endpoints:
        1. Primary: POST api/GetUserMonthlyStatistics (returns UserMonthlyStatisticsBeanData with workoutList)
        2. Fallback: POST api/GetMyWorkoutDateList and POST api/GetMyLatest3MonthWorkoutStatsByActivityType
        3. Detailed Telemetry: POST api/GetUserWorkoutContent (with needRawData: true)
        """
        headers = self._get_headers()
        raw_workouts: List[Dict[str, Any]] = []
        workout_uuids: List[str] = []

        # Step 1: POST api/GetUserMonthlyStatistics
        monthly_url = f"{self.base_url}/api/GetUserMonthlyStatistics"
        current_year = datetime.now(timezone.utc).year
        monthly_payload = {
            "page": 1,
            "pageSize": max(limit, 20),
            "sortAsc": False,
            "years": [current_year, current_year - 1, current_year - 2],
            "toFilter": False,
        }

        try:
            resp = requests.post(monthly_url, json=monthly_payload, headers=headers, timeout=20)
            logger.debug("GetUserMonthlyStatistics (HTTP %d, %d bytes): %s", resp.status_code, len(resp.text), resp.text)
            if resp.status_code == 200:
                data = resp.json()
                if not data.get("success", False):
                    err_msg = data.get("errorMessage") or data.get("msg")
                    err_code = data.get("errorCode")
                    logger.warning(
                        "Dyaco GetUserMonthlyStatistics returned success=false: %s (ErrorCode: %s, Response: %s)",
                        err_msg,
                        err_code,
                        data,
                    )
                    # If session/token is invalid or expired, clear session and re-authenticate
                    if allow_reauth and self._is_auth_error(err_code, err_msg):
                        return self._reauthenticate_and_retry(limit=limit, reason=f"{err_msg} (ErrorCode: {err_code})")
                beans = data.get("dataMap", {}).get("data") or []
                if isinstance(beans, list):
                    for bean in beans:
                        if isinstance(bean, dict):
                            w_list = bean.get("workoutList") or []
                            if isinstance(w_list, list):
                                for w in w_list:
                                    if isinstance(w, dict):
                                        raw_workouts.append(w)
                                        w_id = w.get("userWorkoutUuid") or w.get("workoutUuid")
                                        if w_id and w_id not in workout_uuids:
                                            workout_uuids.append(w_id)
                logger.debug("Retrieved %d workouts from GetUserMonthlyStatistics.", len(raw_workouts))
            else:
                logger.warning("GetUserMonthlyStatistics returned HTTP %d: %s", resp.status_code, resp.text[:200])
        except RequestException as exc:
            logger.warning("GetUserMonthlyStatistics request failed: %s", exc)

        # Step 1b: Fallback to GetMyWorkoutDateList if no workouts found
        if not raw_workouts and not workout_uuids:
            date_list_url = f"{self.base_url}/api/GetMyWorkoutDateList"
            try:
                resp = requests.post(date_list_url, json={}, headers=headers, timeout=20)
                logger.debug("GetMyWorkoutDateList (HTTP %d, %d bytes): %s", resp.status_code, len(resp.text), resp.text)
                if resp.status_code == 200:
                    data = resp.json()
                    if not data.get("success", False):
                        err_msg = data.get("errorMessage") or data.get("msg")
                        err_code = data.get("errorCode")
                        logger.warning(
                            "Dyaco GetMyWorkoutDateList returned success=false: %s (ErrorCode: %s, Response: %s)",
                            err_msg,
                            err_code,
                            data,
                        )
                        if allow_reauth and self._is_auth_error(err_code, err_msg):
                            return self._reauthenticate_and_retry(limit=limit, reason=f"{err_msg} (ErrorCode: {err_code})")
                    workout_uuids = self._extract_workout_uuids(data)
                    logger.debug("Discovered %d workout UUIDs from GetMyWorkoutDateList.", len(workout_uuids))
                else:
                    logger.warning("GetMyWorkoutDateList returned HTTP %d: %s", resp.status_code, resp.text[:200])
            except RequestException as exc:
                logger.warning("GetMyWorkoutDateList request failed: %s", exc)

        # Step 1c: Fallback to GetMyLatest3MonthWorkoutStatsByActivityType
        if not raw_workouts and not workout_uuids:
            stats_url = f"{self.base_url}/api/GetMyLatest3MonthWorkoutStatsByActivityType"
            try:
                resp = requests.post(stats_url, json={}, headers=headers, timeout=20)
                logger.debug("GetMyLatest3MonthWorkoutStatsByActivityType (HTTP %d, %d bytes): %s", resp.status_code, len(resp.text), resp.text)
                if resp.status_code == 200:
                    data = resp.json()
                    if not data.get("success", False):
                        err_msg = data.get("errorMessage") or data.get("msg")
                        err_code = data.get("errorCode")
                        logger.warning(
                            "Dyaco GetMyLatest3MonthWorkoutStatsByActivityType returned success=false: %s (ErrorCode: %s, Response: %s)",
                            err_msg,
                            err_code,
                            data,
                        )
                        if allow_reauth and self._is_auth_error(err_code, err_msg):
                            return self._reauthenticate_and_retry(limit=limit, reason=f"{err_msg} (ErrorCode: {err_code})")
                    workout_uuids = self._extract_workout_uuids(data)
                    logger.debug("Discovered %d workout UUIDs from 3-Month stats.", len(workout_uuids))
                else:
                    logger.warning("Fallback stats returned HTTP %d: %s", resp.status_code, resp.text[:200])
            except RequestException as exc:
                logger.warning("Fallback stats request failed: %s", exc)

        # Step 2: Ensure detailed WorkoutViewVo with rawDataList telemetry
        activities: List[Dict[str, Any]] = []
        content_url = f"{self.base_url}/api/GetUserWorkoutContent"

        # If we have direct WorkoutViewVo objects from GetUserMonthlyStatistics
        if raw_workouts:
            for vo in raw_workouts[:limit]:
                # If rawDataList is missing or empty, fetch full content
                w_uuid = vo.get("userWorkoutUuid") or vo.get("workoutUuid")
                if not vo.get("rawDataList") and w_uuid:
                    try:
                        detail_resp = requests.post(
                            content_url,
                            json={"workoutUuid": w_uuid, "needRawData": True},
                            headers=headers,
                            timeout=25,
                        )
                        if detail_resp.status_code == 200:
                            d_map = detail_resp.json().get("dataMap") or {}
                            vo = d_map.get("data") or vo
                    except RequestException as exc:
                        logger.debug("Could not fetch detailed rawDataList for %s: %s", w_uuid, exc)

                norm = self._normalize_activity(vo)
                if norm:
                    activities.append(norm)
        else:
            # Query by UUID list
            for uuid in workout_uuids[:limit]:
                try:
                    body = {
                        "workoutUuid": uuid,
                        "needRawData": True,
                    }
                    detail_resp = requests.post(content_url, json=body, headers=headers, timeout=25)
                    if detail_resp.status_code == 200:
                        detail_data = detail_resp.json()
                        data_map = detail_data.get("dataMap") or {}
                        vo = data_map.get("data") or data_map
                        norm = self._normalize_activity(vo)
                        if norm:
                            activities.append(norm)
                except RequestException as exc:
                    logger.warning("Error fetching content for workout %s: %s", uuid, exc)

        return activities

    def _extract_workout_uuids(self, obj: Any) -> List[str]:
        """Recursively traverse JSON structure to find all workout UUID strings."""
        uuids: List[str] = []
        candidates = ("userWorkoutUuid", "workoutUuid", "externalWorkoutUuid", "workout_uuid", "uuid", "id")

        if isinstance(obj, dict):
            for key in candidates:
                val = obj.get(key)
                if val and isinstance(val, str) and len(val) > 8:
                    uuids.append(val)
                    break
            for val in obj.values():
                uuids.extend(self._extract_workout_uuids(val))
        elif isinstance(obj, list):
            for item in obj:
                uuids.extend(self._extract_workout_uuids(item))

        # Preserve order while removing duplicates
        seen: Set[str] = set()
        deduped = []
        for u in uuids:
            if u not in seen:
                seen.add(u)
                deduped.append(u)
        return deduped

    def _normalize_activity(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Convert a raw Dyaco WorkoutViewVo or workout dict to a standardized dictionary."""
        act_id = str(
            item.get("userWorkoutUuid")
            or item.get("workoutUuid")
            or item.get("id")
            or item.get("workout_id")
            or item.get("workoutId")
            or item.get("uuid")
            or ""
        )
        if not act_id:
            return None

        # Parse date and start time
        start_time_millis = item.get("startTimeMillis")
        if start_time_millis:
            try:
                date_raw = datetime.fromtimestamp(start_time_millis / 1000.0, tz=timezone.utc).isoformat()
            except Exception:
                date_raw = item.get("startTime") or item.get("workoutDate") or datetime.now(timezone.utc).isoformat()
        else:
            date_raw = (
                item.get("startTime")
                or item.get("workoutDate")
                or item.get("date")
                or item.get("created_at")
                or datetime.now(timezone.utc).isoformat()
            )

        # Distance: convert to meters
        dist = float(item.get("totalDistance") or item.get("distance") or item.get("total_distance") or 0.0)
        dist_unit = str(item.get("distance_unit") or item.get("unit") or "").lower()
        if dist_unit in ("mi", "miles", "mile"):
            dist_meters = dist * 1609.344
        elif dist_unit in ("m", "meters"):
            dist_meters = dist
        elif dist_unit in ("km", "kilometers"):
            dist_meters = dist * 1000.0
        elif dist > 0 and dist < 100.0:
            # Stored as kilometers or miles in summary
            dist_meters = dist * 1000.0
        else:
            dist_meters = dist

        # Duration in seconds
        duration = float(item.get("totalTime") or item.get("duration") or item.get("elapsed_time") or item.get("total_time") or 0.0)
        calories = int(item.get("totalCalories") or item.get("calories") or item.get("total_calories") or 0)

        # Incline / Grade
        avg_incline = float(item.get("avgIncline") or item.get("avg_incline") or item.get("incline") or 0.0)
        max_incline = float(item.get("maxIncline") or item.get("max_incline") or avg_incline)

        # Elevation gain estimate if not present
        total_ascent = float(
            item.get("totalElevation")
            or item.get("vertical")
            or item.get("elevation_gain")
            or item.get("total_ascent")
            or (dist_meters * (avg_incline / 100.0) if avg_incline > 0 else 0.0)
        )

        # Speed (m/s and km/h)
        avg_speed = float(item.get("avgSpeed") or item.get("avg_speed") or 0.0)
        speed_unit = str(item.get("speed_unit") or "").lower()
        if speed_unit in ("mph", "mi/h"):
            avg_speed_mps = avg_speed * 0.44704
        elif speed_unit in ("mps", "m/s"):
            avg_speed_mps = avg_speed
        elif speed_unit in ("kmh", "km/h", "kph"):
            avg_speed_mps = avg_speed / 3.6
        elif avg_speed > 1.0 and avg_speed < 40.0:
            # Typically reported in km/h by Dyaco treadmill console
            avg_speed_mps = avg_speed / 3.6
        elif avg_speed > 0:
            avg_speed_mps = avg_speed
        else:
            avg_speed_mps = (dist_meters / duration) if duration > 0 else 0.0

        avg_speed_kmh = avg_speed_mps * 3.6
        max_speed = float(item.get("maxSpeed") or item.get("max_speed") or 0.0)
        max_speed_mps = (max_speed / 3.6) if max_speed > 0 else (avg_speed_mps * 1.15)

        # Equipment / Sport resolution from WorkoutViewVo
        machine_cat = item.get("_machineCategoryType") or item.get("machineCategoryType")
        machine_model = str(
            item.get("machineModelName")
            or item.get("machineName")
            or item.get("modelNameFromMachine")
            or item.get("machine_type")
            or item.get("type")
            or ""
        ).lower()

        # Dyaco machine category conventions: 1 = Treadmill, 2 = Bike, 3 = Elliptical, 4 = Rower
        program_name = item.get("programName") or item.get("title") or item.get("name")
        if machine_cat == 2 or "bike" in machine_model or "cycle" in machine_model:
            sport = "CYCLING"
            sub_sport = "INDOOR_CYCLING"
            default_name = "Sole Indoor Cycling"
            name = f"{default_name} - {program_name}" if program_name and not program_name.startswith("Sole ") else (program_name or default_name)
        elif machine_cat == 3 or "elliptical" in machine_model:
            sport = "FITNESS_EQUIPMENT"
            sub_sport = "ELLIPTICAL"
            default_name = "Sole Elliptical Session"
            name = f"{default_name} - {program_name}" if program_name and not program_name.startswith("Sole ") else (program_name or default_name)
        elif self.walk_run_threshold_kmh > 0 and avg_speed_kmh <= self.walk_run_threshold_kmh:
            sport = "WALKING"
            sub_sport = "INDOOR_WALKING"
            name = "Sole Treadmill Indoor Walking"
        else:
            sport = "RUNNING"
            sub_sport = "INDOOR_RUNNING"
            name = "Sole Treadmill Indoor Run"

        # Heart Rate
        avg_hr = int(item.get("avgHeartRate") or item.get("avg_hr") or item.get("avg_heart_rate") or item.get("heart_rate") or 0)
        max_hr = int(
            item.get("peakHeartRate")
            or item.get("maxHeartRate")
            or item.get("max_hr")
            or item.get("max_heart_rate")
            or (avg_hr + 15 if avg_hr > 0 else 0)
        )

        # Cadence (steps/min for run, rpm for bike)
        avg_cadence = int(item.get("avgCadence") or item.get("avg_cadence") or item.get("cadence") or 0)
        max_cadence = int(item.get("maxCadence") or item.get("max_cadence") or (avg_cadence + 10 if avg_cadence > 0 else 0))

        # High-resolution time-series data points from rawDataList
        raw_list = item.get("rawDataList") or item.get("data_points") or item.get("records") or []
        data_points = self._parse_raw_data_list(raw_list, start_time_millis)

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

    def _parse_raw_data_list(self, raw_list: List[Any], start_time_millis: Optional[int] = None) -> List[Dict[str, Any]]:
        """Normalize rawDataList entries to standardized telemetry points."""
        points: List[Dict[str, Any]] = []
        base_ms = start_time_millis or int(time.time() * 1000)

        for i, pt in enumerate(raw_list):
            if not isinstance(pt, dict):
                continue

            sec_offset = pt.get("second") or pt.get("timeOffset") or pt.get("seconds") or i
            pt_ts = pt.get("timestamp") or pt.get("time")
            if not pt_ts:
                pt_ts = int(base_ms + (float(sec_offset) * 1000.0))

            # Speed: convert to m/s if necessary
            pt_speed = pt.get("speed") or pt.get("speedMps")
            if pt_speed is not None:
                pt_speed = float(pt_speed)
                if pt_speed > 1.0 and pt_speed < 40.0 and "speedMps" not in pt:
                    pt_speed = pt_speed / 3.6  # km/h to m/s

            # Distance in meters
            pt_dist = pt.get("distance") or pt.get("distanceMeters") or pt.get("totalDistance")
            if pt_dist is not None:
                pt_dist = float(pt_dist)
                if pt_dist < 100.0 and pt_dist > 0 and "distanceMeters" not in pt:
                    pt_dist = pt_dist * 1000.0

            point_record: Dict[str, Any] = {"timestamp": pt_ts}
            if pt_speed is not None:
                point_record["speed"] = pt_speed
            if pt_dist is not None:
                point_record["distance"] = pt_dist
            if "heartRate" in pt or "heart_rate" in pt or "hr" in pt:
                hr_v = int(pt.get("heartRate") or pt.get("heart_rate") or pt.get("hr", 0))
                if hr_v > 0:
                    point_record["heart_rate"] = hr_v
            if "cadence" in pt or "spm" in pt or "rpm" in pt:
                cad_v = int(pt.get("cadence") or pt.get("spm") or pt.get("rpm", 0))
                if cad_v > 0:
                    point_record["cadence"] = cad_v
            if "altitude" in pt or "elevation" in pt:
                point_record["altitude"] = float(pt.get("altitude") or pt.get("elevation", 0.0))
            if "incline" in pt or "grade" in pt:
                point_record["incline"] = float(pt.get("incline") or pt.get("grade", 0.0))

            points.append(point_record)

        return points

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
                    items = (
                        content.get("dataMap", {}).get("data")
                        or content.get("data")
                        or content.get("workouts")
                        or [content]
                    )
                    if isinstance(items, list):
                        for item in items:
                            norm = self._normalize_activity(item)
                            if norm:
                                results.append(norm)
                    elif isinstance(items, dict):
                        norm = self._normalize_activity(items)
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
