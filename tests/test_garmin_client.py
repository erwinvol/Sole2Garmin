"""Unit tests for GarminClient activity type setting and duplicate checks."""

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.garmin_client import GarminClient


class TestGarminClient(unittest.TestCase):

    def setUp(self):
        self.garmin = GarminClient(
            username="test@example.com",
            password="secretpassword",
            token_dir=Path("/tmp/test_tokens"),
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


if __name__ == "__main__":
    unittest.main()
