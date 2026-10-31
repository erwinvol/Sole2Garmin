"""Unit tests for FIT builder and device enhancer."""

import os
import tempfile
import unittest
from datetime import datetime, timezone

from fit_tool.fit_file import FitFile
from fit_tool.profile.messages.activity_message import ActivityMessage
from fit_tool.profile.messages.device_info_message import DeviceInfoMessage
from fit_tool.profile.messages.event_message import EventMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.messages.sport_message import SportMessage
from fit_tool.profile.profile_type import Activity, Manufacturer, Sport, SubSport

from src.fit_builder import FitBuilder, parse_timestamp_ms


class TestFitBuilder(unittest.TestCase):

    def setUp(self):
        self.builder = FitBuilder(
            spoof_device=True,
            device_product_id=3943,  # Epix Gen 2
            software_version=18.16,
        )

    def test_parse_timestamp_ms(self):
        ms = parse_timestamp_ms("2026-10-13T12:00:00Z")
        self.assertGreater(ms, 1700000000000)

    def test_build_fit_from_activity_treadmill(self):
        activity = {
            "id": "act_treadmill_01",
            "name": "Sole F80 Run",
            "date": "2026-10-13T10:00:00Z",
            "sport": "RUNNING",
            "sub_sport": "TREADMILL",
            "duration_seconds": 600.0,
            "distance_meters": 1500.0,
            "calories": 120,
            "avg_speed_mps": 2.5,
            "max_speed_mps": 2.8,
            "avg_heart_rate": 145,
            "max_heart_rate": 158,
            "avg_cadence": 162,
            "max_cadence": 168,
            "avg_incline_pct": 1.5,
            "total_ascent_meters": 22.5,
        }

        with tempfile.NamedTemporaryFile(suffix=".fit", delete=False) as tf:
            out_path = tf.name

        try:
            res_path = self.builder.build_fit_from_activity(activity, output_path=out_path)
            self.assertEqual(res_path, out_path)
            self.assertTrue(os.path.exists(out_path))
            self.assertGreater(os.path.getsize(out_path), 100)

            # Parse and verify messages
            with open(out_path, "rb") as f:
                raw_bytes = f.read()

            fit_file = FitFile.from_bytes(raw_bytes, check_crc=True)

            found_file_id = False
            found_sport = False
            found_session = False
            found_lap = False
            found_activity = False

            for r in fit_file.records:
                msg = r.message
                if isinstance(msg, FileIdMessage):
                    found_file_id = True
                    self.assertEqual(msg.manufacturer, Manufacturer.GARMIN.value)
                    self.assertEqual(msg.product, 3943)
                elif isinstance(msg, SportMessage):
                    found_sport = True
                    self.assertIn(msg.sport, (Sport.RUNNING, Sport.RUNNING.value))
                    self.assertIn(msg.sub_sport, (SubSport.TREADMILL, SubSport.TREADMILL.value))
                elif isinstance(msg, LapMessage):
                    found_lap = True
                    self.assertIsNotNone(msg.timestamp)
                    self.assertIsNotNone(msg.start_time)
                    self.assertGreater(msg.timestamp, msg.start_time)
                elif isinstance(msg, SessionMessage):
                    found_session = True
                    self.assertIn(msg.sport, (Sport.RUNNING, Sport.RUNNING.value))
                    self.assertIn(msg.sub_sport, (SubSport.TREADMILL, SubSport.TREADMILL.value))
                    self.assertAlmostEqual(msg.total_distance, 1500.0, places=1)
                    self.assertEqual(msg.total_calories, 120)
                    self.assertIsNotNone(msg.timestamp)
                    self.assertIsNotNone(msg.start_time)
                    self.assertGreater(msg.timestamp, msg.start_time)
                elif isinstance(msg, ActivityMessage):
                    found_activity = True
                    self.assertIsNotNone(msg.timestamp)
                    self.assertEqual(msg.num_sessions, 1)
                    self.assertIn(msg.type, (Activity.MANUAL, Activity.MANUAL.value))
                    self.assertIsNotNone(msg.local_timestamp)
                    FIT_EPOCH_S = 631065600
                    utc_fit_s = int(msg.timestamp / 1000.0) - FIT_EPOCH_S
                    from src.garmin_client import get_host_timezone
                    host_tz = get_host_timezone()
                    end_dt_utc = datetime.fromtimestamp(msg.timestamp / 1000.0, tz=timezone.utc)
                    end_dt_local = end_dt_utc.astimezone(host_tz)
                    expected_offset = int(end_dt_local.utcoffset().total_seconds()) if end_dt_local.utcoffset() else 0
                    self.assertEqual(msg.local_timestamp - utc_fit_s, expected_offset)

            self.assertTrue(found_file_id)
            self.assertTrue(found_sport)
            self.assertTrue(found_lap)
            self.assertTrue(found_session)
            self.assertTrue(found_activity)

        finally:
            FitBuilder.cleanup_file(out_path)

    def test_enhance_existing_fit_file(self):
        # Create an initial FIT file
        activity = {
            "id": "init_01",
            "date": "2026-10-13T10:00:00Z",
            "sport": "RUNNING",
            "sub_sport": "TREADMILL",
            "duration_seconds": 60.0,
            "distance_meters": 100.0,
        }
        with tempfile.NamedTemporaryFile(suffix=".fit", delete=False) as tf1:
            initial_path = tf1.name

        with tempfile.NamedTemporaryFile(suffix=".fit", delete=False) as tf2:
            enhanced_path = tf2.name

        try:
            self.builder.build_fit_from_activity(activity, output_path=initial_path)

            # Change builder product to Forerunner 965
            forerunner_builder = FitBuilder(device_product_id=4315)
            forerunner_builder.enhance(initial_path, output_path=enhanced_path)

            with open(enhanced_path, "rb") as f:
                fit_file = FitFile.from_bytes(f.read(), check_crc=True)

            found_file_id = False
            for r in fit_file.records:
                msg = r.message
                if isinstance(msg, FileIdMessage):
                    found_file_id = True
                    self.assertEqual(msg.product, 4315)

            self.assertTrue(found_file_id)
        finally:
            FitBuilder.cleanup_file(initial_path)
            FitBuilder.cleanup_file(enhanced_path)

    def test_build_fit_from_activity_walking(self):
        activity = {
            "id": "act_walk_01",
            "name": "Sole Treadmill Indoor Walking",
            "date": "2026-10-13T10:00:00Z",
            "sport": "WALKING",
            "sub_sport": "INDOOR_WALKING",
            "duration_seconds": 1800.0,
            "distance_meters": 2000.0,
            "calories": 150,
            "avg_speed_mps": 1.11,
            "avg_incline_pct": 2.0,
        }
        with tempfile.NamedTemporaryFile(suffix=".fit", delete=False) as tf:
            out_path = tf.name

        try:
            res_path = self.builder.build_fit_from_activity(activity, output_path=out_path)
            self.assertTrue(os.path.exists(res_path))

            with open(res_path, "rb") as f:
                fit_file = FitFile.from_bytes(f.read(), check_crc=True)

            found_sport = False
            found_session = False
            for r in fit_file.records:
                msg = r.message
                if isinstance(msg, SportMessage):
                    found_sport = True
                    self.assertIn(msg.sport, (Sport.WALKING, Sport.WALKING.value))
                    self.assertIn(msg.sub_sport, (SubSport.GENERIC, SubSport.GENERIC.value))
                    self.assertEqual(msg.sport_name, "Sole Treadmill Indoor Walking")
                elif isinstance(msg, SessionMessage):
                    found_session = True
                    self.assertIn(msg.sport, (Sport.WALKING, Sport.WALKING.value))
                    self.assertIn(msg.sub_sport, (SubSport.GENERIC, SubSport.GENERIC.value))
                    self.assertEqual(msg.sport_profile_name, "Sole Treadmill Indoor Walking")

            self.assertTrue(found_sport)
            self.assertTrue(found_session)
        finally:
            FitBuilder.cleanup_file(out_path)

    def test_build_fit_from_activity_running(self):
        activity = {
            "id": "act_run_01",
            "name": "Sole Treadmill Indoor Run",
            "date": "2026-10-13T10:00:00Z",
            "sport": "RUNNING",
            "sub_sport": "INDOOR_RUNNING",
            "duration_seconds": 1500.0,
            "distance_meters": 5000.0,
            "calories": 400,
            "avg_speed_mps": 3.33,
        }
        with tempfile.NamedTemporaryFile(suffix=".fit", delete=False) as tf:
            out_path = tf.name

        try:
            res_path = self.builder.build_fit_from_activity(activity, output_path=out_path)
            self.assertTrue(os.path.exists(res_path))

            with open(res_path, "rb") as f:
                fit_file = FitFile.from_bytes(f.read(), check_crc=True)

            found_sport = False
            found_session = False
            for r in fit_file.records:
                msg = r.message
                if isinstance(msg, SportMessage):
                    found_sport = True
                    self.assertIn(msg.sport, (Sport.RUNNING, Sport.RUNNING.value))
                    self.assertIn(msg.sub_sport, (SubSport.INDOOR_RUNNING, SubSport.INDOOR_RUNNING.value))
                    self.assertEqual(msg.sport_name, "Sole Treadmill Indoor Run")
                elif isinstance(msg, SessionMessage):
                    found_session = True
                    self.assertIn(msg.sport, (Sport.RUNNING, Sport.RUNNING.value))
                    self.assertIn(msg.sub_sport, (SubSport.INDOOR_RUNNING, SubSport.INDOOR_RUNNING.value))

            self.assertTrue(found_sport)
            self.assertTrue(found_session)
        finally:
            FitBuilder.cleanup_file(out_path)

    def test_build_fit_from_activity_naive_date(self):
        """Test naive date string from Sole console (e.g. 13:37:35) is localized to host timezone and converted to UTC."""
        builder = FitBuilder(device_product_id=3943)
        activity = {
            "id": "act_naive_01",
            "name": "Sole Treadmill Indoor Walk",
            "date": "2026-10-13 13:37:35",
            "sport": "WALKING",
            "sub_sport": "GENERIC",
            "duration_seconds": 1800.0,
            "distance_meters": 2500.0,
        }
        with tempfile.NamedTemporaryFile(suffix=".fit", delete=False) as tf:
            out_path = tf.name

        try:
            builder.build_fit_from_activity(activity, output_path=out_path)
            with open(out_path, "rb") as f:
                fit_file = FitFile.from_bytes(f.read(), check_crc=True)

            file_id = next(r.message for r in fit_file.records if isinstance(r.message, FileIdMessage))
            from datetime import datetime, timezone
            from src.garmin_client import get_host_timezone
            host_tz = get_host_timezone()
            expected_dt = datetime(2026, 10, 13, 13, 37, 35, tzinfo=host_tz).astimezone(timezone.utc)
            dt = datetime.fromtimestamp(file_id.time_created / 1000.0, tz=timezone.utc)
            self.assertEqual(dt, expected_dt)

            act_msg = next(r.message for r in fit_file.records if isinstance(r.message, ActivityMessage))
            self.assertIsNotNone(act_msg.local_timestamp)
            FIT_EPOCH_S = 631065600
            utc_fit_s = int(act_msg.timestamp / 1000.0) - FIT_EPOCH_S
            end_dt_local = datetime.fromtimestamp(act_msg.timestamp / 1000.0, tz=timezone.utc).astimezone(host_tz)
            expected_offset = int(end_dt_local.utcoffset().total_seconds()) if end_dt_local.utcoffset() else 0
            self.assertEqual(act_msg.local_timestamp - utc_fit_s, expected_offset)
        finally:
            FitBuilder.cleanup_file(out_path)


if __name__ == "__main__":
    unittest.main()
