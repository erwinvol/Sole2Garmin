"""Unit tests for configuration management."""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from src.config import Config


class TestConfig(unittest.TestCase):

    def test_default_config(self):
        config = Config()
        self.assertEqual(config.sync_interval_minutes, 60)
        self.assertEqual(config.sync_batch_size, 10)
        self.assertTrue(config.check_duplicates)
        self.assertEqual(config.duplicate_threshold_hours, 2)
        self.assertTrue(config.spoof_garmin_device)
        self.assertEqual(config.device_product_id, 3943)  # Epix Gen 2
        self.assertEqual(config.default_sport, "RUNNING")
        self.assertEqual(config.default_sub_sport, "TREADMILL")
        self.assertEqual(config.walk_run_threshold_kmh, 6.0)
        self.assertEqual(config.ingestion_provider, "cloud")
        self.assertTrue(config.webhook_enabled)
        self.assertEqual(config.webhook_host, "0.0.0.0")
        self.assertEqual(config.webhook_port, 8080)
        self.assertEqual(config.webhook_token, "")

    @patch.dict(os.environ, {
        "SOLE_EMAIL": "runner@example.com",
        "SOLE_PASSWORD": "solepassword",
        "GARMIN_USERNAME": "garmin@example.com",
        "GARMIN_PASSWORD": "garminpassword",
        "DEVICE_PRODUCT_ID": "4315",  # Forerunner 965
        "SYNC_INTERVAL_MINUTES": "30",
        "CHECK_DUPLICATES": "false",
        "DEFAULT_SPORT": "CYCLING",
        "DEFAULT_SUB_SPORT": "INDOOR_CYCLING",
        "WALK_RUN_THRESHOLD_KMH": "5.5",
        "WEBHOOK_ENABLED": "true",
        "WEBHOOK_PORT": "9090",
        "WEBHOOK_HOST": "127.0.0.1",
        "WEBHOOK_TOKEN": "secret_ha_token",
    })
    def test_from_env(self):
        config = Config.from_env()
        self.assertEqual(config.sole_email, "runner@example.com")
        self.assertEqual(config.sole_password, "solepassword")
        self.assertEqual(config.garmin_username, "garmin@example.com")
        self.assertEqual(config.garmin_password, "garminpassword")
        self.assertEqual(config.device_product_id, 4315)
        self.assertEqual(config.sync_interval_minutes, 30)
        self.assertFalse(config.check_duplicates)
        self.assertEqual(config.default_sport, "CYCLING")
        self.assertEqual(config.default_sub_sport, "INDOOR_CYCLING")
        self.assertEqual(config.walk_run_threshold_kmh, 5.5)
        self.assertEqual(config.ingestion_provider, "cloud")
        self.assertTrue(config.webhook_enabled)
        self.assertEqual(config.webhook_port, 9090)
        self.assertEqual(config.webhook_host, "127.0.0.1")
        self.assertEqual(config.webhook_token, "secret_ha_token")

    @patch.dict(os.environ, {
        "INGESTION_PROVIDER": "strava",
    })
    def test_from_env_provider_override(self):
        config = Config.from_env()
        self.assertEqual(config.ingestion_provider, "strava")


if __name__ == "__main__":
    unittest.main()
