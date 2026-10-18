"""Unit tests for GarminClient activity type setting and duplicate checks."""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.garmin_client import GarminClient


class TestGarminClient(unittest.TestCase):

    def setUp(self):
        self.garmin = GarminClient(
            username="test@example.com",
            password="secretpassword",
            token_dir=Path("/tmp/test_tokens"),
        )
        self.mock_client = MagicMock()
        self.garmin._client = self.mock_client
        self.garmin._authenticated = True

    def test_set_activity_name_success(self):
        self.mock_client.set_activity_name.return_value = True
        res = self.garmin.set_activity_name("12345", "Sole Treadmill Indoor Walking")
        self.assertTrue(res)
        self.mock_client.set_activity_name.assert_called_once_with("12345", "Sole Treadmill Indoor Walking")

    def test_set_activity_type_with_api_cache(self):
        self.mock_client.get_activity_types.return_value = [
            {"typeId": 9, "typeKey": "walking", "parentTypeId": 17},
            {"typeId": 1, "typeKey": "running", "parentTypeId": 17},
        ]
        res = self.garmin.set_activity_type("12345", type_key="walking")
        self.assertTrue(res)
        self.mock_client.set_activity_type.assert_called_once_with(
            activity_id="12345",
            type_id=9,
            type_key="walking",
            parent_type_id=17,
        )

    def test_set_activity_type_fallback(self):
        self.mock_client.get_activity_types.side_effect = Exception("API error")
        res = self.garmin.set_activity_type("12345", type_key="walking")
        self.assertTrue(res)
        self.mock_client.set_activity_type.assert_called_once_with(
            activity_id="12345",
            type_id=9,
            type_key="walking",
            parent_type_id=17,
        )

    def test_try_rename_uploaded_activity_updates_type_and_name(self):
        self.mock_client.get_activities_by_date.return_value = [
            {
                "activityId": 998877,
                "activityName": "Running",
                "startTimeLocal": "2026-10-13 13:37:35",
                "activityType": {"typeId": 1, "typeKey": "running", "parentTypeId": 17},
            }
        ]
        self.mock_client.get_activity_types.return_value = [
            {"typeId": 9, "typeKey": "walking", "parentTypeId": 17},
        ]

        success = self.garmin._try_rename_uploaded_activity(
            activity_date_str="2026-10-13 13:37:35",
            desired_title="Sole Treadmill Indoor Walking",
            target_sport="WALKING",
            retries=1,
            delay_sec=0,
        )

        self.assertTrue(success)
        # Should have updated activity type to walking
        self.mock_client.set_activity_type.assert_called_once_with(
            activity_id="998877",
            type_id=9,
            type_key="walking",
            parent_type_id=17,
        )
        # Should have updated title
        self.mock_client.set_activity_name.assert_called_once_with("998877", "Sole Treadmill Indoor Walking")

    def test_try_rename_uploaded_activity_leaves_correct_activity(self):
        self.mock_client.get_activities_by_date.return_value = [
            {
                "activityId": 112233,
                "activityName": "Sole Treadmill Indoor Walking",
                "startTimeLocal": "2026-10-13 13:37:35",
                "activityType": {"typeId": 9, "typeKey": "walking", "parentTypeId": 17},
            }
        ]

        success = self.garmin._try_rename_uploaded_activity(
            activity_date_str="2026-10-13 13:37:35",
            desired_title="Sole Treadmill Indoor Walking",
            target_sport="WALKING",
            retries=1,
            delay_sec=0,
        )

        self.assertTrue(success)
        # No updates needed if both type and title are already correct
        self.mock_client.set_activity_type.assert_not_called()
        self.mock_client.set_activity_name.assert_not_called()


if __name__ == "__main__":
    unittest.main()
