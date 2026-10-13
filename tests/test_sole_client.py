"""Unit tests for SOLE+ / Dyaco client."""

import json
import tempfile
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
        self.assertEqual(norm["sub_sport"], "TREADMILL")
        self.assertAlmostEqual(norm["distance_meters"], 3.1 * 1609.344, places=1)
        self.assertEqual(norm["calories"], 320)
        self.assertAlmostEqual(norm["avg_speed_mps"], 6.2 * 0.44704, places=2)
        self.assertEqual(norm["avg_heart_rate"], 148)
        self.assertEqual(norm["avg_cadence"], 164)
        self.assertEqual(norm["avg_incline_pct"], 2.0)

    def test_normalize_elliptical_and_bike(self):
        client = SoleClient("test@example.com", "pass", token_dir=self.token_dir, sample_dir=self.sample_dir)
        elliptical = {
            "id": "e_1",
            "machine_type": "elliptical",
            "distance": 4.0,
            "duration": 1200,
        }
        norm_e = client._normalize_activity(elliptical)
        self.assertEqual(norm_e["sport"], "FITNESS_EQUIPMENT")
        self.assertEqual(norm_e["sub_sport"], "ELLIPTICAL")

        bike = {
            "id": "b_1",
            "machine_type": "bike",
            "distance": 10.0,
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
    def test_cloud_login(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "mock_jwt_token",
            "refresh_token": "mock_refresh_token",
            "expires_in": 3600,
            "user_id": "sole_user_99",
        }
        mock_post.return_value = mock_response

        client = SoleClient("soleuser@example.com", "mypass", token_dir=self.token_dir, sample_dir=self.sample_dir)
        success = client.login()

        self.assertTrue(success)
        self.assertEqual(client.access_token, "mock_jwt_token")
        self.assertEqual(client.user_id, "sole_user_99")
        # Check token file was saved
        token_file = self.token_dir / "sole_token.json"
        self.assertTrue(token_file.exists())


if __name__ == "__main__":
    unittest.main()
