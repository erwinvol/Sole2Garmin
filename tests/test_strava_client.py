"""Unit tests for Strava API client."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.strava_client import StravaClient


class TestStravaClient(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.token_dir = Path(self.temp_dir.name) / "tokens"
        self.sample_dir = Path(self.temp_dir.name) / "samples"
        self.sample_dir.mkdir(parents=True)

        self.client = StravaClient(
            client_id="12345",
            client_secret="secret_abc",
            refresh_token="ref_token_xyz",
            token_dir=self.token_dir,
            sample_dir=self.sample_dir,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_is_sole_workout(self):
        self.assertTrue(self.client._is_sole_workout({"name": "Sole F80 Treadmill Run"}))
        self.assertTrue(self.client._is_sole_workout({"device_name": "Dyaco Sole TT8"}))
        self.assertTrue(self.client._is_sole_workout({"external_id": "sole_activity_102"}))
        self.assertFalse(self.client._is_sole_workout({"name": "Outdoor Park Run", "device_name": "Garmin Edge"}))

    def test_normalize_strava_activity_with_streams(self):
        raw_strava = {
            "id": 99887766,
            "name": "Sole F80 Morning Run",
            "type": "Run",
            "start_date": "2026-10-13T08:00:00Z",
            "elapsed_time": 1800,
            "moving_time": 1800,
            "distance": 5000.0,
            "calories": 410,
            "average_speed": 2.77,
            "max_speed": 3.3,
            "average_heartrate": 150,
            "max_heartrate": 165,
            "average_cadence": 162,
            "total_elevation_gain": 45.0,
        }

        mock_streams = {
            "time": {"data": [0, 5, 10, 15]},
            "distance": {"data": [0.0, 13.85, 27.7, 41.55]},
            "velocity_smooth": {"data": [2.77, 2.77, 2.77, 2.77]},
            "grade_smooth": {"data": [1.5, 1.5, 2.0, 2.0]},
            "altitude": {"data": [100.0, 100.2, 100.4, 100.7]},
            "heartrate": {"data": [140, 145, 150, 152]},
            "cadence": {"data": [160, 162, 164, 164]},
        }

        norm = self.client._normalize_strava_activity(raw_strava, mock_streams)
        self.assertIsNotNone(norm)
        self.assertEqual(norm["id"], "strava_99887766")
        self.assertEqual(norm["sport"], "RUNNING")
        self.assertEqual(norm["sub_sport"], "INDOOR_RUNNING")
        self.assertEqual(norm["distance_meters"], 5000.0)
        self.assertEqual(norm["duration_seconds"], 1800.0)
        self.assertEqual(norm["calories"], 410)
        self.assertEqual(len(norm["data_points"]), 4)

        first_pt = norm["data_points"][0]
        self.assertEqual(first_pt["speed"], 2.77)
        self.assertEqual(first_pt["heart_rate"], 140)
        self.assertEqual(first_pt["cadence"], 160)

    @patch("requests.post")
    def test_refresh_access_token(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "access_token": "new_strava_access_token_123",
            "refresh_token": "new_strava_refresh_token_456",
            "expires_at": 1893456000,
        }
        mock_post.return_value = mock_resp

        success = self.client.refresh_access_token()
        self.assertTrue(success)
        self.assertEqual(self.client.access_token, "new_strava_access_token_123")
        self.assertEqual(self.client.refresh_token, "new_strava_refresh_token_456")

        # Verify token file was written to disk
        token_file = self.token_dir / "strava_token.json"
        self.assertTrue(token_file.exists())

    def test_normalize_strava_walk_threshold(self):
        raw_walk = {
            "id": 11223344,
            "name": "Sole F80 Walk",
            "type": "Run",
            "start_date": "2026-10-13T09:00:00Z",
            "elapsed_time": 1800,
            "distance": 2500.0,  # 2.5 km in 30 min = 5.0 km/h (<= 6.0)
            "average_speed": 1.39,
        }
        norm = self.client._normalize_strava_activity(raw_walk)
        self.assertEqual(norm["sport"], "WALKING")
        self.assertEqual(norm["sub_sport"], "GENERIC")


if __name__ == "__main__":
    unittest.main()
