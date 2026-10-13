"""Unit tests for SQLite synchronization store."""

import tempfile
import unittest
from pathlib import Path

from src.sync_store import SyncStore


class TestSyncStore(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_sync.db"
        self.store = SyncStore(db_path=self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_record_and_is_synced(self):
        self.assertFalse(self.store.is_synced("act_1"))
        self.assertEqual(self.store.count(), 0)

        self.store.record_activity(
            sole_id="act_1",
            name="Sole 5K Run",
            activity_date="2026-10-13T09:00:00Z",
            sport="RUNNING",
            status="UPLOADED",
            notes="Uploaded with Epix Gen 2 spoof",
        )

        self.assertTrue(self.store.is_synced("act_1"))
        self.assertEqual(self.store.count(), 1)
        self.assertIn("act_1", self.store.get_synced_ids())

        recent = self.store.get_recent_synced(10)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["sole_id"], "act_1")
        self.assertEqual(recent[0]["garmin_status"], "UPLOADED")

    def test_update_existing_record(self):
        self.store.record_activity(
            sole_id="act_1",
            name="Run",
            activity_date="2026-10-13",
            sport="RUNNING",
            status="SKIPPED_DUPLICATE",
        )
        self.assertEqual(self.store.count(), 1)

        # Update status
        self.store.record_activity(
            sole_id="act_1",
            name="Run",
            activity_date="2026-10-13",
            sport="RUNNING",
            status="UPLOADED",
            notes="Forced upload",
        )
        self.assertEqual(self.store.count(), 1)
        recent = self.store.get_recent_synced(1)
        self.assertEqual(recent[0]["garmin_status"], "UPLOADED")


if __name__ == "__main__":
    unittest.main()
