"""Unit tests for synchronization engine."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.config import Config
from src.fit_builder import FitBuilder
from src.garmin_client import GarminClient
from src.sole_client import SoleClient
from src.sync_engine import SyncEngine
from src.sync_store import SyncStore


class TestSyncEngine(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.db_path = self.data_dir / "sync.db"
        self.store = SyncStore(self.db_path)

        self.config = Config(
            data_dir=self.data_dir,
            check_duplicates=True,
            duplicate_threshold_hours=2,
            sync_batch_size=5,
        )

        self.mock_sole = MagicMock(spec=SoleClient)
        self.mock_garmin = MagicMock(spec=GarminClient)
        self.builder = FitBuilder(device_product_id=3943)

        self.engine = SyncEngine(
            config=self.config,
            sole=self.mock_sole,
            garmin=self.mock_garmin,
            builder=self.builder,
            store=self.store,
        )

        self.sample_activities = [
            {
                "id": "sole_01",
                "name": "Sole 5K Run",
                "date": "2026-10-13T10:00:00Z",
                "sport": "RUNNING",
                "sub_sport": "TREADMILL",
                "duration_seconds": 1800.0,
                "distance_meters": 5000.0,
                "calories": 400,
                "avg_speed_mps": 2.77,
                "max_speed_mps": 3.2,
                "avg_heart_rate": 150,
                "max_heart_rate": 165,
                "avg_cadence": 165,
                "max_cadence": 172,
                "avg_incline_pct": 1.0,
                "total_ascent_meters": 50.0,
            },
            {
                "id": "sole_02",
                "name": "Sole 10K Run",
                "date": "2026-10-13T16:00:00Z",
                "sport": "RUNNING",
                "sub_sport": "TREADMILL",
                "duration_seconds": 3600.0,
                "distance_meters": 10000.0,
                "calories": 800,
                "avg_speed_mps": 2.77,
                "max_speed_mps": 3.2,
                "avg_heart_rate": 155,
                "max_heart_rate": 170,
                "avg_cadence": 165,
                "max_cadence": 172,
                "avg_incline_pct": 1.0,
                "total_ascent_meters": 100.0,
            },
        ]

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_run_sync_successful_upload(self):
        self.mock_sole.get_activities.return_value = self.sample_activities
        self.mock_garmin.check_duplicate.return_value = False
        self.mock_garmin.upload_activity.return_value = {"status": "uploaded"}

        stats = self.engine.run_sync()

        self.assertEqual(stats["total_found"], 2)
        self.assertEqual(stats["uploaded"], 2)
        self.assertEqual(stats["skipped_duplicate"], 0)
        self.assertEqual(stats["already_synced"], 0)

        # Database should record both
        self.assertTrue(self.store.is_synced("sole_01"))
        self.assertTrue(self.store.is_synced("sole_02"))

    def test_run_sync_skips_already_synced(self):
        self.store.record_activity("sole_01", "Sole 5K Run", "2026-10-13T10:00:00Z", "RUNNING", "UPLOADED")
        self.mock_sole.get_activities.return_value = self.sample_activities
        self.mock_garmin.check_duplicate.return_value = False
        self.mock_garmin.upload_activity.return_value = {"status": "uploaded"}

        stats = self.engine.run_sync()

        self.assertEqual(stats["total_found"], 2)
        self.assertEqual(stats["already_synced"], 1)
        self.assertEqual(stats["uploaded"], 1)

    def test_run_sync_handles_garmin_duplicate(self):
        self.mock_sole.get_activities.return_value = [self.sample_activities[0]]
        # Simulate Garmin duplicate detected
        self.mock_garmin.check_duplicate.return_value = True

        stats = self.engine.run_sync()

        self.assertEqual(stats["uploaded"], 0)
        self.assertEqual(stats["skipped_duplicate"], 1)
        self.assertTrue(self.store.is_synced("sole_01"))

        recent = self.store.get_recent_synced(1)
        self.assertEqual(recent[0]["garmin_status"], "SKIPPED_DUPLICATE")

    def test_run_sync_dry_run(self):
        self.mock_sole.get_activities.return_value = self.sample_activities

        stats = self.engine.run_sync(dry_run=True)

        self.assertEqual(stats["uploaded"], 2)
        self.assertFalse(self.store.is_synced("sole_01"))
        self.assertEqual(self.store.count(), 0)

    def test_run_sync_applies_walk_run_threshold(self):
        walk_activity = {
            "id": "walk_auto_01",
            "name": "Sole Treadmill Run",
            "date": "2026-10-13T12:00:00Z",
            "sport": "RUNNING",
            "sub_sport": "TREADMILL",
            "duration_seconds": 1800.0,
            "distance_meters": 2000.0,
            "avg_speed_mps": 1.11,  # 4.0 km/h <= 6.0 km/h
        }
        self.mock_sole.get_activities.return_value = [walk_activity]
        self.mock_garmin.check_duplicate.return_value = False
        self.mock_garmin.upload_activity.return_value = {"status": "uploaded"}

        stats = self.engine.run_sync()
        self.assertEqual(stats["uploaded"], 1)

        # Check recorded sport in sync store
        recent = self.store.get_recent_synced(1)
        self.assertEqual(recent[0]["sole_id"], "walk_auto_01")
        self.assertEqual(recent[0]["sport"], "WALKING")


if __name__ == "__main__":
    unittest.main()
