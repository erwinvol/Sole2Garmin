"""Unit tests for Garmin Connect client."""

import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.garmin_client import GarminClient


class TestGarminClient(unittest.TestCase):

    def setUp(self):
        self.garmin = GarminClient(
            username="test@example.com",
            password="password",
            token_dir=Path("/tmp/garmin_test_tokens"),
        )
        self.mock_client = MagicMock()
        self.mock_client.garmin_connect_activity = "/activity-service/activity"
        self.mock_client.client = MagicMock()
        self.garmin._client = self.mock_client
        self.garmin._authenticated = True

    def test_set_activity_name_success(self):
        self.mock_client.client.put.return_value = {"status": "ok"}
        res = self.garmin.set_activity_name("12345", "Sole Treadmill Indoor Walking")
        self.assertTrue(res)
        self.mock_client.client.put.assert_called_once_with(
            "connectapi",
            "/activity-service/activity/12345",
            json={"activityId": 12345, "activityName": "Sole Treadmill Indoor Walking"},
            api=True,
        )

    def test_set_activity_type_with_api_cache(self):
        self.mock_client.get_activity_types.return_value = [
            {"typeId": 9, "typeKey": "walking", "parentTypeId": 17},
            {"typeId": 1, "typeKey": "running", "parentTypeId": 17},
        ]
        self.mock_client.client.put.return_value = {"status": "ok"}
        res = self.garmin.set_activity_type("12345", type_key="walking")
        self.assertTrue(res)
        self.mock_client.client.put.assert_called_once_with(
            "connectapi",
            "/activity-service/activity/12345",
            json={
                "activityId": 12345,
                "activityTypeDTO": {
                    "typeId": 9,
                    "typeKey": "walking",
                    "parentTypeId": 17,
                },
            },
            api=True,
        )

    def test_update_activity_atomic_name_and_type(self):
        self.mock_client.get_activity_types.return_value = [
            {"typeId": 9, "typeKey": "walking", "parentTypeId": 17},
        ]
        self.mock_client.client.put.return_value = {"status": "ok"}
        res = self.garmin.update_activity(
            activity_id="998877",
            name="Sole Treadmill Indoor Walking",
            type_key="walking",
        )
        self.assertTrue(res)
        self.mock_client.client.put.assert_called_once_with(
            "connectapi",
            "/activity-service/activity/998877",
            json={
                "activityId": 998877,
                "activityName": "Sole Treadmill Indoor Walking",
                "activityTypeDTO": {
                    "typeId": 9,
                    "typeKey": "walking",
                    "parentTypeId": 17,
                },
            },
            api=True,
        )

    def test_try_rename_uploaded_activity_updates_type_and_name_atomically(self):
        self.mock_client.get_activities_by_date.return_value = [
            {
                "activityId": 998877,
                "activityName": "Running",
                "startTimeLocal": "2026-10-13 13:37:35",
                "startTimeGMT": "2026-10-13 17:37:35",
                "activityType": {"typeId": 1, "typeKey": "running", "parentTypeId": 17},
            }
        ]
        self.mock_client.get_activity_types.return_value = [
            {"typeId": 9, "typeKey": "walking", "parentTypeId": 17},
        ]
        self.mock_client.client.put.return_value = {"status": "ok"}

        success = self.garmin._try_rename_uploaded_activity(
            activity_date_str="2026-10-13T17:37:35+00:00",
            desired_title="Sole Treadmill Indoor Walking",
            target_sport="WALKING",
            retries=1,
            delay_sec=0,
            threshold_hours=2.0,
        )

        self.assertTrue(success)
        # Should have updated activity atomically with both name and type
        self.mock_client.client.put.assert_called_once_with(
            "connectapi",
            "/activity-service/activity/998877",
            json={
                "activityId": 998877,
                "activityName": "Sole Treadmill Indoor Walking",
                "activityTypeDTO": {
                    "typeId": 9,
                    "typeKey": "walking",
                    "parentTypeId": 17,
                },
            },
            api=True,
        )

    def test_try_rename_uploaded_activity_renames_when_named_walking(self):
        self.mock_client.get_activities_by_date.return_value = [
            {
                "activityId": 887766,
                "activityName": "Walking",
                "startTimeLocal": "2026-10-13 13:37:35",
                "startTimeGMT": "2026-10-13 17:37:35",
                "activityType": {"typeId": 9, "typeKey": "walking", "parentTypeId": 17},
            }
        ]
        self.mock_client.client.put.return_value = {"status": "ok"}

        success = self.garmin._try_rename_uploaded_activity(
            activity_date_str="2026-10-13T17:37:35+00:00",
            desired_title="Sole Treadmill Indoor Walking",
            target_sport="WALKING",
            retries=1,
            delay_sec=0,
        )

        self.assertTrue(success)
        # Should have renamed from Walking to Sole Treadmill Indoor Walking
        self.mock_client.client.put.assert_called_once_with(
            "connectapi",
            "/activity-service/activity/887766",
            json={
                "activityId": 887766,
                "activityName": "Sole Treadmill Indoor Walking",
            },
            api=True,
        )

    def test_try_rename_uploaded_activity_leaves_correct_activity(self):
        self.mock_client.get_activities_by_date.return_value = [
            {
                "activityId": 112233,
                "activityName": "Sole Treadmill Indoor Walking",
                "startTimeLocal": "2026-10-13 13:37:35",
                "startTimeGMT": "2026-10-13 17:37:35",
                "activityType": {"typeId": 9, "typeKey": "walking", "parentTypeId": 17},
            }
        ]

        success = self.garmin._try_rename_uploaded_activity(
            activity_date_str="2026-10-13T17:37:35+00:00",
            desired_title="Sole Treadmill Indoor Walking",
            target_sport="WALKING",
            retries=1,
            delay_sec=0,
        )

        self.assertTrue(success)
        # No updates needed if both type and title are already correct
        self.mock_client.client.put.assert_not_called()

    def test_try_rename_uploaded_activity_authenticates_if_needed(self):
        self.garmin._authenticated = False
        self.garmin.authenticate = MagicMock(return_value=True)
        self.mock_client.get_activities_by_date.return_value = []

        self.garmin._try_rename_uploaded_activity(
            activity_date_str="2026-10-13T17:37:35+00:00",
            desired_title="Sole Treadmill Indoor Walking",
            retries=1,
            delay_sec=0,
        )

        self.garmin.authenticate.assert_called_once()

    def test_try_rename_uploaded_activity_matches_with_begin_timestamp(self):
        # 2026-10-13 17:37:35 UTC = epoch 1791913055
        self.mock_client.get_activities_by_date.return_value = [
            {
                "activityId": 445566,
                "activityName": "Walking",
                "beginTimestamp": 1791913055000,
                "activityType": {"typeId": 9, "typeKey": "walking", "parentTypeId": 17},
            }
        ]
        self.mock_client.client.put.return_value = {"status": "ok"}

        success = self.garmin._try_rename_uploaded_activity(
            activity_date_str="2026-10-13T17:37:35+00:00",
            desired_title="Sole Treadmill Indoor Walking",
            target_sport="WALKING",
            retries=1,
            delay_sec=0,
        )

        self.assertTrue(success)
        self.mock_client.client.put.assert_called_once_with(
            "connectapi",
            "/activity-service/activity/445566",
            json={
                "activityId": 445566,
                "activityName": "Sole Treadmill Indoor Walking",
            },
            api=True,
        )

    def test_parse_activity_datetime(self):
        from datetime import datetime, timezone
        from unittest.mock import patch
        from src.garmin_client import get_host_timezone, parse_activity_datetime

        # 1. Naive string localized to host timezone
        dt_naive = parse_activity_datetime("2026-10-13 13:37:35")
        self.assertIsNotNone(dt_naive)
        self.assertEqual(dt_naive.tzinfo, timezone.utc)
        host_tz = get_host_timezone()
        expected_utc = datetime(2026, 10, 13, 13, 37, 35, tzinfo=host_tz).astimezone(timezone.utc)
        self.assertEqual(dt_naive, expected_utc)

        # 2. Testing explicit timezone overrides via TZ environment variable
        with patch.dict("os.environ", {"TZ": "America/New_York"}):
            dt_ny = parse_activity_datetime("2026-10-13 13:37:35")
            self.assertIsNotNone(dt_ny)
            self.assertEqual(dt_ny.hour, 17)
            self.assertEqual(dt_ny.minute, 37)
            self.assertEqual(dt_ny.second, 35)

        with patch.dict("os.environ", {"TZ": "Europe/Amsterdam"}):
            dt_ams = parse_activity_datetime("2026-10-13 13:37:35")
            self.assertIsNotNone(dt_ams)
            self.assertEqual(dt_ams.hour, 11)

        # 3. Explicit ISO UTC string with 'Z'
        dt_utc = parse_activity_datetime("2026-10-13T17:37:35Z")
        self.assertIsNotNone(dt_utc)
        self.assertEqual(dt_utc.hour, 17)

        # 4. Explicit ISO string with offset
        dt_offset = parse_activity_datetime("2026-10-13T13:37:35-04:00")
        self.assertIsNotNone(dt_offset)
        self.assertEqual(dt_offset.hour, 17)

    def test_format_local_datetime(self):
        from src.garmin_client import format_local_datetime
        formatted = format_local_datetime("2026-10-13T17:37:35+00:00")
        self.assertTrue(len(formatted) > 0)

    def test_upload_activity_success_with_name_and_sport(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".fit", delete=False) as tf:
            tf.write(b"mock fit data")
            fit_path = tf.name

        try:
            self.mock_client.upload_activity.return_value = {"uploadId": 123456}
            with patch.object(self.garmin, "_try_rename_uploaded_activity", return_value=True) as mock_rename:
                res = self.garmin.upload_activity(
                    fit_path,
                    activity_name="Sole Treadmill Indoor Walking",
                    activity_date_str="2026-10-20 11:33:42",
                    target_sport="WALKING",
                )
                self.assertEqual(res["status"], "uploaded")
                mock_rename.assert_called_once_with(
                    activity_date_str="2026-10-20 11:33:42",
                    desired_title="Sole Treadmill Indoor Walking",
                    target_sport="WALKING",
                )
        finally:
            if os.path.exists(fit_path):
                os.remove(fit_path)

    def test_upload_activity_http_409_conflict(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".fit", delete=False) as tf:
            tf.write(b"mock fit data")
            fit_path = tf.name

        try:
            self.mock_client.upload_activity.side_effect = Exception("HTTP 409 Conflict: Activity duplicate")
            res = self.garmin.upload_activity(
                fit_path,
                activity_name="Sole Treadmill Indoor Walking",
            )
            self.assertEqual(res["status"], "skipped_duplicate")
        finally:
            if os.path.exists(fit_path):
                os.remove(fit_path)

    def test_try_rename_uploaded_activity_selects_closest_match(self):
        # Two activities: one 1.5 hours away, one 5 minutes away
        self.mock_client.get_activities_by_date.return_value = [
            {
                "activityId": 1001,
                "activityName": "Earlier Run",
                "startTimeGMT": "2026-10-13 15:30:00",
                "activityType": {"typeId": 1, "typeKey": "running"},
            },
            {
                "activityId": 1002,
                "activityName": "Exact Run",
                "startTimeGMT": "2026-10-13 17:05:00",
                "activityType": {"typeId": 1, "typeKey": "running"},
            },
        ]
        self.mock_client.client.put.return_value = {"status": "ok"}

        success = self.garmin._try_rename_uploaded_activity(
            activity_date_str="2026-10-13T17:00:00Z",
            desired_title="Sole Treadmill Indoor Run",
            target_sport="RUNNING",
            retries=1,
            delay_sec=0,
            threshold_hours=2.0,
        )

        self.assertTrue(success)
        # Should have updated activity 1002 (5 min diff), NOT 1001 (90 min diff)
        self.mock_client.client.put.assert_called_once_with(
            "connectapi",
            "/activity-service/activity/1002",
            json={
                "activityId": 1002,
                "activityName": "Sole Treadmill Indoor Run",
            },
            api=True,
        )

    def test_try_rename_uploaded_activity_does_not_convert_distant_run_to_walk(self):
        # A run that is 1 hour away should NOT be overwritten as walking
        self.mock_client.get_activities_by_date.return_value = [
            {
                "activityId": 2001,
                "activityName": "Morning 5K",
                "startTimeGMT": "2026-10-13 16:00:00",
                "activityType": {"typeId": 1, "typeKey": "running"},
            }
        ]
        self.mock_client.get_activity_types.return_value = [
            {"typeId": 9, "typeKey": "walking", "parentTypeId": 17},
        ]

        success = self.garmin._try_rename_uploaded_activity(
            activity_date_str="2026-10-13T17:15:00Z",  # 1.25 hours away
            desired_title="Sole Treadmill Indoor Walking",
            target_sport="WALKING",
            retries=1,
            delay_sec=0,
            threshold_hours=2.0,
        )

        self.assertTrue(success)
        # Should NOT have updated type_key to walking because best_diff > 0.5h
        self.mock_client.client.put.assert_called_once_with(
            "connectapi",
            "/activity-service/activity/2001",
            json={
                "activityId": 2001,
                "activityName": "Sole Treadmill Indoor Walking",
            },
            api=True,
        )

    def test_try_rename_uploaded_activity_upgrades_mistagged_walk_to_run(self):
        # A workout mislabeled as walking should be upgraded to running
        self.mock_client.get_activities_by_date.return_value = [
            {
                "activityId": 3001,
                "activityName": "Sole Treadmill Indoor Walking",
                "startTimeGMT": "2026-10-13 17:00:00",
                "activityType": {"typeId": 9, "typeKey": "walking", "parentTypeId": 17},
            }
        ]
        self.mock_client.get_activity_types.return_value = [
            {"typeId": 1, "typeKey": "running", "parentTypeId": 17},
        ]
        self.mock_client.client.put.return_value = {"status": "ok"}

        success = self.garmin._try_rename_uploaded_activity(
            activity_date_str="2026-10-13T17:00:00Z",
            desired_title="Sole Treadmill Indoor Run",
            target_sport="RUNNING",
            retries=1,
            delay_sec=0,
            threshold_hours=2.0,
        )

        self.assertTrue(success)
        self.mock_client.client.put.assert_called_once_with(
            "connectapi",
            "/activity-service/activity/3001",
            json={
                "activityId": 3001,
                "activityName": "Sole Treadmill Indoor Run",
                "activityTypeDTO": {
                    "typeId": 1,
                    "typeKey": "running",
                    "parentTypeId": 17,
                },
            },
            api=True,
        )


if __name__ == "__main__":
    unittest.main()
