"""Unit tests for SOLE+ / Dyaco client."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.sole_client import SoleClient


class TestSoleClient(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.token_dir = Path(self.temp_dir.name) / "tokens"
        self.sample_dir = Path(self.temp_dir.name) / "samples"
        self.sample_dir.mkdir(parents=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_normalize_treadmill_activity(self):
        client = SoleClient("test@example.com", "pass", token_dir=self.token_dir, sample_dir=self.sample_dir)
        raw_workout = {
            "id": "workout_101",
            "name": "Morning Treadmill Run",
            "date": "2026-10-13T07:00:00Z",
            "machine_type": "treadmill",
            "distance": 3.1,
            "distance_unit": "miles",
            "duration": 1800,
            "calories": 320,
            "avg_speed": 6.2,
            "speed_unit": "mph",
            "avg_incline": 2.0,
            "avg_heart_rate": 148,
            "avg_cadence": 164,
        }
        norm = client._normalize_activity(raw_workout)
        self.assertIsNotNone(norm)
        self.assertEqual(norm["id"], "workout_101")
        self.assertEqual(norm["sport"], "RUNNING")
        self.assertEqual(norm["sub_sport"], "INDOOR_RUNNING")
        self.assertAlmostEqual(norm["distance_meters"], 3.1 * 1609.344, places=1)
        self.assertEqual(norm["calories"], 320)
        self.assertAlmostEqual(norm["avg_speed_mps"], 6.2 * 0.44704, places=2)
        self.assertEqual(norm["avg_heart_rate"], 148)
        self.assertEqual(norm["avg_cadence"], 164)
        self.assertEqual(norm["avg_incline_pct"], 2.0)

    def test_normalize_walking_activity_under_threshold(self):
        """Test workouts with avg speed <= 6.0 km/h are tagged as Walking."""
        client = SoleClient("test@example.com", "pass", token_dir=self.token_dir, sample_dir=self.sample_dir)
        raw_walk = {
            "id": "walk_201",
            "programName": "Incline Walk",
            "machineModelName": "Sole F80 Treadmill",
            "distance": 3.0,  # 3 km
            "duration": 2400,  # 40 min = 4.5 km/h
            "avgSpeed": 4.5,
            "speed_unit": "km/h",
        }
        norm = client._normalize_activity(raw_walk)
        self.assertIsNotNone(norm)
        self.assertEqual(norm["sport"], "WALKING")
        self.assertEqual(norm["sub_sport"], "GENERIC")
        self.assertEqual(norm["name"], "Sole Walking - Incline Walk")

    def test_normalize_speed_boundary_and_custom_threshold(self):
        """Test boundary at 6.0 km/h and custom threshold settings."""
        client_default = SoleClient("test@example.com", "pass", token_dir=self.token_dir, sample_dir=self.sample_dir)

        # Exactly 6.0 km/h -> WALKING
        at_boundary = {"id": "w_6", "avgSpeed": 6.0, "duration": 1800, "distance": 3.0}
        norm_6 = client_default._normalize_activity(at_boundary)
        self.assertEqual(norm_6["sport"], "WALKING")
        self.assertEqual(norm_6["sub_sport"], "GENERIC")

        # 6.1 km/h -> INDOOR_RUNNING
        over_boundary = {"id": "r_61", "avgSpeed": 6.1, "duration": 1800, "distance": 3.05}
        norm_61 = client_default._normalize_activity(over_boundary)
        self.assertEqual(norm_61["sport"], "RUNNING")
        self.assertEqual(norm_61["sub_sport"], "INDOOR_RUNNING")

        # Custom threshold: 4.5 km/h
        client_custom = SoleClient(
            "test@example.com", "pass",
            token_dir=self.token_dir, sample_dir=self.sample_dir,
            walk_run_threshold_kmh=4.5,
        )
        # 5.0 km/h is above 4.5 -> RUNNING
        norm_custom = client_custom._normalize_activity({"id": "c_50", "avgSpeed": 5.0, "duration": 1800})
        self.assertEqual(norm_custom["sport"], "RUNNING")
        self.assertEqual(norm_custom["sub_sport"], "INDOOR_RUNNING")

    def test_normalize_workout_view_vo(self):
        """Test normalization of real Dyaco WorkoutViewVo contract with rawDataList."""
        client = SoleClient("test@example.com", "pass", token_dir=self.token_dir, sample_dir=self.sample_dir)
        vo = {
            "userWorkoutUuid": "dyaco-uuid-8888-9999",
            "programName": "Fatburn",
            "_machineCategoryType": 1,
            "machineModelName": "Sole F80 Treadmill",
            "startTimeMillis": 1729080000000,
            "totalTime": 2400,
            "totalDistance": 6200.0,
            "totalCalories": 480,
            "avgSpeed": 9.3,
            "avgIncline": 1.5,
            "avgHeartRate": 152.0,
            "peakHeartRate": 171.0,
            "avgCadence": 168.0,
            "totalElevation": 93.0,
            "rawDataList": [
                {
                    "second": 0,
                    "speed": 8.0,
                    "distance": 0.0,
                    "heartRate": 120,
                    "cadence": 150,
                    "incline": 1.0,
                },
                {
                    "second": 60,
                    "speed": 9.5,
                    "distance": 150.0,
                    "heartRate": 145,
                    "cadence": 165,
                    "incline": 1.5,
                },
            ],
        }
        norm = client._normalize_activity(vo)
        self.assertIsNotNone(norm)
        self.assertEqual(norm["id"], "dyaco-uuid-8888-9999")
        self.assertEqual(norm["sport"], "RUNNING")
        self.assertEqual(norm["sub_sport"], "INDOOR_RUNNING")
        self.assertEqual(norm["name"], "Sole Indoor Running - Fatburn")
        self.assertEqual(norm["duration_seconds"], 2400)
        self.assertEqual(norm["distance_meters"], 6200.0)
        self.assertEqual(norm["calories"], 480)
        self.assertAlmostEqual(norm["avg_speed_mps"], 9.3 / 3.6, places=2)
        self.assertEqual(norm["avg_heart_rate"], 152)
        self.assertEqual(norm["max_heart_rate"], 171)
        self.assertEqual(norm["avg_cadence"], 168)
        self.assertEqual(norm["avg_incline_pct"], 1.5)
        self.assertEqual(norm["total_ascent_meters"], 93.0)

        # Verify parsed rawDataList
        points = norm["data_points"]
        self.assertEqual(len(points), 2)
        self.assertEqual(points[0]["heart_rate"], 120)
        self.assertAlmostEqual(points[0]["speed"], 8.0 / 3.6, places=2)
        self.assertEqual(points[1]["distance"], 150.0)
        self.assertEqual(points[1]["cadence"], 165)

    def test_normalize_elliptical_and_bike(self):
        client = SoleClient("test@example.com", "pass", token_dir=self.token_dir, sample_dir=self.sample_dir)
        elliptical = {
            "id": "e_1",
            "_machineCategoryType": 3,
            "machineModelName": "Sole E35",
            "distance": 4000.0,
            "duration": 1200,
        }
        norm_e = client._normalize_activity(elliptical)
        self.assertEqual(norm_e["sport"], "FITNESS_EQUIPMENT")
        self.assertEqual(norm_e["sub_sport"], "ELLIPTICAL")

        bike = {
            "id": "b_1",
            "_machineCategoryType": 2,
            "machineModelName": "Sole SB900 Bike",
            "distance": 10000.0,
            "duration": 1500,
        }
        norm_b = client._normalize_activity(bike)
        self.assertEqual(norm_b["sport"], "CYCLING")
        self.assertEqual(norm_b["sub_sport"], "INDOOR_CYCLING")

    def test_load_local_sample_activity(self):
        client = SoleClient("", "", token_dir=self.token_dir, sample_dir=self.sample_dir)
        sample_file = self.sample_dir / "sample.json"
        with open(sample_file, "w") as f:
            json.dump({
                "id": "local_sample_1",
                "name": "Local 5k",
                "machine_type": "treadmill",
                "distance": 5.0,
                "duration": 1500,
            }, f)

        activities = client.get_activities()
        self.assertEqual(len(activities), 1)
        self.assertEqual(activities[0]["id"], "local_sample_1")

    @patch("requests.post")
    def test_resolve_login_server_address(self, mock_post):
        """Test reverse-engineered Dyaco global gateway cluster resolution."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "dataMap": {"data": "https://sole-homeplus-cluster.dyacocloud.com"},
        }
        mock_post.return_value = mock_response

        client = SoleClient("user@example.com", "pass", token_dir=self.token_dir, sample_dir=self.sample_dir)
        server = client._resolve_login_server_address(register_type=1)

        self.assertEqual(server, "https://sole-homeplus-cluster.dyacocloud.com")
        mock_post.assert_called_once()
        call_args, call_kwargs = mock_post.call_args
        self.assertEqual(call_args[0], "https://homeglobal.dyacocloud.com/api/GetLoginServerAddress")
        self.assertEqual(call_kwargs["json"]["registerType"], 1)
        self.assertEqual(call_kwargs["json"]["email"], "user@example.com")
        self.assertEqual(call_kwargs["json"]["emailOrUserUuidInIdentityProvider"], "user@example.com")

    @patch("requests.post")
    def test_check_user_exists(self, mock_post):
        """Test reverse-engineered Dyaco IsUserExists contract."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "dataMap": {"data": True},
        }
        mock_post.return_value = mock_response

        client = SoleClient("user@example.com", "pass", token_dir=self.token_dir, sample_dir=self.sample_dir)
        exists = client.check_user_exists(register_type=1)

        self.assertTrue(exists)
        mock_post.assert_called_once()
        call_args, call_kwargs = mock_post.call_args
        self.assertEqual(call_args[0], "https://homeglobal.dyacocloud.com/api/IsUserExists")
        self.assertEqual(call_kwargs["json"]["registerType"], 1)
        self.assertEqual(call_kwargs["json"]["email"], "user@example.com")
        self.assertEqual(call_kwargs["json"]["emailOrUserUuidInIdentityProvider"], "user@example.com")

    @patch("requests.post")
    def test_dyaco_cloud_login(self, mock_post):
        # 1. Gateway cluster resolution
        gateway_resp = MagicMock()
        gateway_resp.status_code = 200
        gateway_resp.json.return_value = {
            "success": True,
            "dataMap": {"data": "https://sole-homeplus-cluster.dyacocloud.com"},
        }

        # 2. Cluster login response
        login_resp = MagicMock()
        login_resp.status_code = 200
        login_resp.headers = {
            "sessionid": "sess_abcdef123456",
            "Content-Type": "application/json",
        }
        login_resp.json.return_value = {
            "success": True,
            "dataMap": {
                "loginToken": "jwt_dyaco_login_token",
                "serviceLoginToken": "refresh_dyaco_token",
                "data": {
                    "userId": "sole_dyaco_user_42",
                    "email": "soleuser@example.com",
                },
            },
        }
        mock_post.side_effect = [gateway_resp, login_resp]

        client = SoleClient("soleuser@example.com", "mypass", token_dir=self.token_dir, sample_dir=self.sample_dir)
        success = client.login()

        self.assertTrue(success)
        self.assertEqual(client.base_url, "https://sole-homeplus-cluster.dyacocloud.com")
        self.assertEqual(client.session_id, "sess_abcdef123456")
        self.assertEqual(client.access_token, "jwt_dyaco_login_token")
        self.assertEqual(client.user_id, "sole_dyaco_user_42")

        # Verify calls: gateway resolution then cluster login
        self.assertEqual(mock_post.call_count, 2)
        gateway_call = mock_post.call_args_list[0]
        self.assertEqual(gateway_call[0][0], "https://homeglobal.dyacocloud.com/api/GetLoginServerAddress")
        self.assertEqual(gateway_call[1]["json"]["registerType"], 1)
        self.assertEqual(gateway_call[1]["json"]["emailOrUserUuidInIdentityProvider"], "soleuser@example.com")

        login_call = mock_post.call_args_list[1]
        self.assertEqual(login_call[0][0], "https://sole-homeplus-cluster.dyacocloud.com/api/LoginByEmail")
        self.assertEqual(login_call[1]["json"]["email"], "soleuser@example.com")
        self.assertEqual(login_call[1]["json"]["emailOrUserUuidInIdentityProvider"], "soleuser@example.com")
        self.assertTrue(bool(login_call[1]["json"].get("pushToken")))
        self.assertTrue(bool(login_call[1]["json"].get("pushMessageToken")))

        # Check token file was saved
        token_file = self.token_dir / "sole_token.json"
        self.assertTrue(token_file.exists())

    @patch("requests.post")
    def test_dyaco_cloud_login_rejected(self, mock_post):
        # 1. Gateway resolution returns default
        gateway_resp = MagicMock()
        gateway_resp.status_code = 200
        gateway_resp.json.return_value = {
            "success": True,
            "dataMap": {"data": "https://sole-homeplus.dyacocloud.com"},
        }

        # 2. Login returns 112
        login_resp = MagicMock()
        login_resp.status_code = 200
        login_resp.json.return_value = {
            "success": False,
            "errorCode": 112,
            "errorMessage": "Account or password error",
        }

        # 3. Diagnostic check_user_exists
        exists_resp = MagicMock()
        exists_resp.status_code = 200
        exists_resp.json.return_value = {
            "success": True,
            "dataMap": {"data": True},
        }

        mock_post.side_effect = [gateway_resp, login_resp, exists_resp]

        client = SoleClient("wrong@example.com", "wrongpass", token_dir=self.token_dir, sample_dir=self.sample_dir)
        success = client.login()

        self.assertFalse(success)
        self.assertIsNone(client.session_id)
        self.assertIsNone(client.access_token)
        token_file = self.token_dir / "sole_token.json"
        self.assertFalse(token_file.exists())

    @patch("requests.post")
    def test_get_activities_monthly_statistics_workflow(self, mock_post):
        """Test primary workflow: GetUserMonthlyStatistics returning UserMonthlyStatisticsBeanData with workoutList."""
        # 0. Gateway resolution
        gateway_resp = MagicMock()
        gateway_resp.status_code = 200
        gateway_resp.json.return_value = {
            "success": True,
            "dataMap": {"data": "https://sole-homeplus.dyacocloud.com"},
        }

        # 1. Login response
        login_resp = MagicMock()
        login_resp.status_code = 200
        login_resp.headers = {"sessionid": "test_session"}
        login_resp.json.return_value = {
            "success": True,
            "dataMap": {
                "loginToken": "test_token",
                "data": {"userId": "user_1"},
            },
        }

        # 2. GetUserMonthlyStatistics response with UserMonthlyStatisticsBeanData
        monthly_resp = MagicMock()
        monthly_resp.status_code = 200
        monthly_resp.json.return_value = {
            "success": True,
            "dataMap": {
                "totalCount": 2,
                "totalPage": 1,
                "data": [
                    {
                        "year": 2026,
                        "month": 10,
                        "totalCalories": 800,
                        "activeHours": 1.2,
                        "workoutList": [
                            {
                                "userWorkoutUuid": "uuid-monthly-1",
                                "startTimeMillis": 1729080000000,
                                "_machineCategoryType": 1,
                                "totalTime": 1800,
                                "totalDistance": 5000.0,
                                "totalCalories": 350,
                                "avgSpeed": 10.0,
                                "avgIncline": 1.0,
                                "rawDataList": [
                                    {"second": 0, "speed": 10.0, "distance": 0.0, "heartRate": 130}
                                ],
                            },
                            {
                                "userWorkoutUuid": "uuid-monthly-2",
                                "startTimeMillis": 1729166400000,
                                "_machineCategoryType": 1,
                                "totalTime": 1200,
                                "totalDistance": 3000.0,
                                "totalCalories": 220,
                                "avgSpeed": 9.0,
                                "avgIncline": 2.0,
                                "rawDataList": [],
                            },
                        ],
                    }
                ],
            },
        }

        # 3. For workout 2, rawDataList was empty so GetUserWorkoutContent is called
        content_resp_2 = MagicMock()
        content_resp_2.status_code = 200
        content_resp_2.json.return_value = {
            "success": True,
            "dataMap": {
                "data": {
                    "userWorkoutUuid": "uuid-monthly-2",
                    "startTimeMillis": 1729166400000,
                    "_machineCategoryType": 1,
                    "totalTime": 1200,
                    "totalDistance": 3000.0,
                    "totalCalories": 220,
                    "avgSpeed": 9.0,
                    "avgIncline": 2.0,
                    "rawDataList": [
                        {"second": 0, "speed": 9.0, "distance": 0.0, "heartRate": 125}
                    ],
                }
            },
        }

        mock_post.side_effect = [gateway_resp, login_resp, monthly_resp, content_resp_2]

        client = SoleClient("user@example.com", "pass", token_dir=self.token_dir, sample_dir=self.sample_dir)
        activities = client.get_activities(limit=10)

        self.assertEqual(len(activities), 2)
        self.assertEqual(activities[0]["id"], "uuid-monthly-1")
        self.assertEqual(activities[0]["distance_meters"], 5000.0)
        self.assertEqual(len(activities[0]["data_points"]), 1)
        self.assertEqual(activities[1]["id"], "uuid-monthly-2")
        self.assertEqual(activities[1]["distance_meters"], 3000.0)
        self.assertEqual(len(activities[1]["data_points"]), 1)

    @patch("requests.post")
    def test_get_activities_date_list_fallback(self, mock_post):
        """Test fallback when GetUserMonthlyStatistics returns 0 items: falls back to GetMyWorkoutDateList."""
        # 0. Gateway resolution
        gateway_resp = MagicMock()
        gateway_resp.status_code = 200
        gateway_resp.json.return_value = {
            "success": True,
            "dataMap": {"data": "https://sole-homeplus.dyacocloud.com"},
        }

        # 1. Login response
        login_resp = MagicMock()
        login_resp.status_code = 200
        login_resp.headers = {"sessionid": "test_session"}
        login_resp.json.return_value = {
            "success": True,
            "dataMap": {
                "loginToken": "test_token",
                "data": {"userId": "user_1"},
            },
        }

        # 2. GetUserMonthlyStatistics returns empty data
        monthly_resp = MagicMock()
        monthly_resp.status_code = 200
        monthly_resp.json.return_value = {"success": True, "dataMap": {"data": []}}

        # 3. GetMyWorkoutDateList response
        date_list_resp = MagicMock()
        date_list_resp.status_code = 200
        date_list_resp.json.return_value = {
            "success": True,
            "dataMap": {
                "data": [
                    {
                        "year": "2026",
                        "monthData": [
                            {"userWorkoutUuid": "uuid-workout-1"},
                        ],
                    }
                ]
            },
        }

        # 4. GetUserWorkoutContent response
        content_resp = MagicMock()
        content_resp.status_code = 200
        content_resp.json.return_value = {
            "success": True,
            "dataMap": {
                "data": {
                    "userWorkoutUuid": "uuid-workout-1",
                    "startTimeMillis": 1729080000000,
                    "_machineCategoryType": 1,
                    "totalTime": 1800,
                    "totalDistance": 5000.0,
                    "totalCalories": 350,
                    "avgSpeed": 10.0,
                    "avgIncline": 1.0,
                    "rawDataList": [],
                }
            },
        }

        mock_post.side_effect = [gateway_resp, login_resp, monthly_resp, date_list_resp, content_resp]

        client = SoleClient("user@example.com", "pass", token_dir=self.token_dir, sample_dir=self.sample_dir)
        activities = client.get_activities(limit=10)

        self.assertEqual(len(activities), 1)
        self.assertEqual(activities[0]["id"], "uuid-workout-1")
        self.assertEqual(activities[0]["distance_meters"], 5000.0)

    @patch("requests.post")
    def test_get_activities_login_required_reauth(self, mock_post):
        """Test automatic recovery and re-login when Dyaco returns LOGIN_REQUIRED(113)."""
        # Seed an existing cached token
        self.token_dir.mkdir(parents=True, exist_ok=True)
        cached_file = self.token_dir / "sole_token.json"
        with open(cached_file, "w") as f:
            json.dump({
                "email": "user@example.com",
                "session_id": "stale_session_123",
                "login_token": "stale_token",
                "expires_at": time.time() + 3600,
            }, f)

        # 1. First GetUserMonthlyStatistics returns LOGIN_REQUIRED(113)
        login_required_resp = MagicMock()
        login_required_resp.status_code = 200
        login_required_resp.text = '{"success":false,"errorMessage":"LOGIN_REQUIRED(113)","errorCode":113}'
        login_required_resp.json.return_value = {
            "success": False,
            "errorMessage": "LOGIN_REQUIRED(113)",
            "errorCode": 113,
            "dataMap": {},
        }

        # 2. Re-login calls GetLoginServerAddress
        gateway_resp = MagicMock()
        gateway_resp.status_code = 200
        gateway_resp.json.return_value = {
            "success": True,
            "dataMap": {"data": "https://sole-homeplus.dyacocloud.com"},
        }

        # 3. LoginByEmail succeeds
        login_resp = MagicMock()
        login_resp.status_code = 200
        login_resp.headers = {"sessionid": "new_fresh_session"}
        login_resp.json.return_value = {
            "success": True,
            "dataMap": {
                "loginToken": "fresh_token_123",
                "data": {"userId": "user_1"},
            },
        }

        # 4. Retried GetUserMonthlyStatistics succeeds
        success_monthly_resp = MagicMock()
        success_monthly_resp.status_code = 200
        success_monthly_resp.text = '{"success":true}'
        success_monthly_resp.json.return_value = {
            "success": True,
            "dataMap": {
                "totalCount": 1,
                "data": [
                    {
                        "year": 2026,
                        "month": 10,
                        "workoutList": [
                            {
                                "userWorkoutUuid": "uuid-recovered-1",
                                "startTimeMillis": 1729080000000,
                                "_machineCategoryType": 1,
                                "totalTime": 1800,
                                "totalDistance": 4000.0,
                                "avgSpeed": 8.0,
                                "rawDataList": [{"second": 0, "speed": 8.0, "distance": 0.0}],
                            }
                        ],
                    }
                ],
            },
        }

        mock_post.side_effect = [
            login_required_resp,
            gateway_resp,
            login_resp,
            success_monthly_resp,
        ]

        client = SoleClient("user@example.com", "pass", token_dir=self.token_dir, sample_dir=self.sample_dir)
        activities = client.get_activities(limit=10)

        self.assertEqual(len(activities), 1)
        self.assertEqual(activities[0]["id"], "uuid-recovered-1")
        self.assertEqual(client.session_id, "new_fresh_session")


if __name__ == "__main__":
    unittest.main()
