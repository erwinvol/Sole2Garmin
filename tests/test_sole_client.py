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
        self.assertEqual(norm["name"], "Sole Treadmill Indoor Run")
        self.assertAlmostEqual(norm["distance_meters"], 3.1 * 1609.344, places=1)
        self.assertEqual(norm["duration_seconds"], 1800)
        self.assertEqual(norm["calories"], 320)
        self.assertAlmostEqual(norm["avg_speed_mps"], 6.2 * 0.44704, places=2)
        self.assertEqual(norm["avg_heart_rate"], 148)
        self.assertEqual(norm["avg_cadence"], 164)
        self.assertEqual(norm["avg_incline_pct"], 2.0)

    def test_normalize_walk_threshold(self):
        client = SoleClient("test@example.com", "pass", token_dir=self.token_dir, sample_dir=self.sample_dir, walk_run_threshold_kmh=6.0)
        raw_walk = {
            "id": "workout_walk",
            "date": "2026-10-13T07:00:00Z",
            "distance": 3000,
            "duration": 2400,
            "avg_speed": 4.5,  # 4.5 km/h <= 6.0
        }
        norm = client._normalize_activity(raw_walk)
        self.assertIsNotNone(norm)
        self.assertEqual(norm["sport"], "WALKING")
        self.assertEqual(norm["sub_sport"], "INDOOR_WALKING")
        self.assertEqual(norm["name"], "Sole Treadmill Indoor Walking")

    def test_normalize_bike_activity(self):
        client = SoleClient("test@example.com", "pass", token_dir=self.token_dir, sample_dir=self.sample_dir)
        raw_workout = {
            "workoutUuid": "bike_001",
            "title": "Evening Spin",
            "startTime": "2026-10-13T18:00:00Z",
            "machineCategoryType": 2,
            "totalDistance": 12000,
            "totalTime": 2400,
            "totalCalories": 400,
            "avgSpeed": 18.0,
            "avgCadence": 85,
        }
        norm = client._normalize_activity(raw_workout)
        self.assertIsNotNone(norm)
        self.assertEqual(norm["sport"], "CYCLING")
        self.assertEqual(norm["sub_sport"], "INDOOR_CYCLING")
        self.assertEqual(norm["avg_cadence"], 85)

    def test_load_local_sample_json(self):
        client = SoleClient("test@example.com", "pass", token_dir=self.token_dir, sample_dir=self.sample_dir)
        sample_file = self.sample_dir / "workout.json"
        with open(sample_file, "w") as f:
            json.dump({
                "id": "sample_001",
                "workoutDate": "2026-10-12T10:00:00Z",
                "distance": 5000,
                "duration": 1500,
                "calories": 300,
                "avg_speed": 12.0,
            }, f)

        acts = client._load_local_activities()
        self.assertEqual(len(acts), 1)
        self.assertEqual(acts[0]["id"], "sample_001")
        self.assertEqual(acts[0]["sport"], "RUNNING")
        self.assertEqual(acts[0]["name"], "Sole Treadmill Indoor Run")

    def test_token_save_and_load(self):
        client = SoleClient("test@example.com", "pass", token_dir=self.token_dir, sample_dir=self.sample_dir)
        client.session_id = "sess_12345"
        client.access_token = "tok_abcde"
        client.user_id = "user_999"
        client.token_expiry = time.time() + 3600
        client._save_cached_token()

        client2 = SoleClient("test@example.com", "pass", token_dir=self.token_dir, sample_dir=self.sample_dir)
        self.assertEqual(client2.session_id, "sess_12345")
        self.assertEqual(client2.access_token, "tok_abcde")
        self.assertEqual(client2.user_id, "user_999")
        self.assertTrue(client2.ensure_authenticated())

    def test_extract_workout_uuids(self):
        client = SoleClient("test@example.com", "pass", token_dir=self.token_dir, sample_dir=self.sample_dir)
        data = {
            "success": True,
            "dataMap": {
                "records": [
                    {"workoutUuid": "uuid-1111-2222-3333"},
                    {"userWorkoutUuid": "uuid-4444-5555-6666"},
                    {"other": {"workout_uuid": "uuid-7777-8888-9999"}},
                ]
            }
        }
        uuids = client._extract_workout_uuids(data)
        self.assertIn("uuid-1111-2222-3333", uuids)
        self.assertIn("uuid-4444-5555-6666", uuids)
        self.assertIn("uuid-7777-8888-9999", uuids)
        self.assertEqual(len(uuids), 3)


if __name__ == "__main__":
    unittest.main()
