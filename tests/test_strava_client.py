"""Unit tests for Strava client integration."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.strava_client import StravaClient


class TestStravaClient(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.token_dir = Path(self.temp_dir.name) / "strava_tokens"
        self.sample_dir = Path(self.temp_dir.name) / "samples"
        self.sample_dir.mkdir(parents=True)

        self.client = StravaClient(
            client_id="12345",
            client_secret="secret_abc",
            refresh_token="initial_refresh_token",
            token_dir=self.token_dir,
            filter_sole_only=True,
            sample_dir=self.sample_dir,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_is_sole_workout(self):
        # 1. Matching by title
        self.assertTrue(self.client._is_sole_workout({"name": "Sole F80 5K Run", "sport_type": "Run"}))
        self.assertTrue(self.client._is_sole_workout({"name": "Morning Treadmill Sole", "sport_type": "VirtualRun"}))

        # 2. Matching by equipment / device name
        self.assertTrue(self.client._is_sole_workout({"name": "Morning Workout", "device_name": "Sole F85"}))

        # 3. Outdoor regular run (should not match)
        self.assertFalse(self.client._is_sole_workout({"name": "Outdoor Park 5K", "sport_type": "Run", "trainer": False}))

        # 4. Relaxed filter: matches any indoor trainer run if filter_sole_only is False
        relaxed_client = StravaClient("1", "2", "3", token_dir=self.token_dir, filter_sole_only=False)
        self.assertTrue(relaxed_client._is_sole_workout({"name": "Treadmill Run", "trainer": True, "sport_type": "Run"}))

    def test_normalize_strava_activity_with_streams(self):
        raw_strava = {
            "id": 99887766,
            "name": "Sole F80 Treadmill Run",
            "start_date": "2026-10-13T14:00:00Z",
            "sport_type": "Run",
            "moving_time": 1800,
            "elapsed_time": 1800,
            "distance": 5000.0,
            "calories": 410,
            "average_speed": 2.77,
            "max_speed": 3.3,
            "average_heartrate": 150,
            "max_heartrate": 165,
            "average_cadence": 164,
            "total_elevation_gain": 50.0,
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
        self.assertEqual(norm["sub_sport"], "TREADMILL")
        self.assertEqual(norm["distance_meters"], 5000.0)
        self.assertEqual(norm["duration_seconds"], 1800.0)
        self.assertEqual(norm["calories"], 410)
        self.assertEqual(len(norm["data_points"]), 4)

        first_pt = norm["data_points"][0]
        self.assertEqual(first_pt["distance"], 0.0)
        self.assertEqual(first_pt["heart_rate"], 140)
        self.assertEqual(first_pt["incline"], 1.5)

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


if __name__ == "__main__":
    unittest.main()
