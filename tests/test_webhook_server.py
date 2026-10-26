"""Unit tests for the webhook HTTP server."""

import json
import time
import unittest
from unittest.mock import MagicMock
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from src.webhook_server import WebhookServer


class TestWebhookServer(unittest.TestCase):
    """Test suite covering the on-demand webhook HTTP server."""

    def setUp(self):
        self.mock_engine = MagicMock()
        self.mock_engine.run_sync.return_value = {
            "total_found": 1,
            "uploaded": 1,
            "skipped_duplicate": 0,
            "failed": 0,
        }
        # Use an opener without proxies to avoid sandbox proxy interception of localhost
        self.opener = build_opener(ProxyHandler({}))

        # Use port 0 to bind to an ephemeral available port
        self.server = WebhookServer(
            engine=self.mock_engine,
            host="127.0.0.1",
            port=0,
            webhook_token="test_secret_token",
        )
        self.port = self.server.server_address[1]
        self.server.start_background()
        self.base_url = f"http://127.0.0.1:{self.port}"
        # Small wait for thread to begin listening
        time.sleep(0.05)

    def tearDown(self):
        self.server.stop()

    def test_root_endpoint(self):
        req = Request(f"{self.base_url}/")
        with self.opener.open(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode())
            self.assertEqual(data["service"], "Sole2Garmin")
            self.assertIn("endpoints", data)

    def test_health_endpoint(self):
        req = Request(f"{self.base_url}/health")
        with self.opener.open(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode())
            self.assertEqual(data["status"], "ok")
            self.assertFalse(data["is_syncing"])
            self.assertIn("uptime_seconds", data)

    def test_sync_unauthorized_missing_token(self):
        req = Request(f"{self.base_url}/sync", method="POST", data=b"{}")
        with self.assertRaises(HTTPError) as ctx:
            self.opener.open(req)
        self.assertEqual(ctx.exception.code, 401)
        err_data = json.loads(ctx.exception.read().decode())
        self.assertEqual(err_data["error"], "Unauthorized")

    def test_sync_unauthorized_wrong_token(self):
        req = Request(
            f"{self.base_url}/sync",
            method="POST",
            data=b"{}",
            headers={"X-Webhook-Token": "wrong_secret"},
        )
        with self.assertRaises(HTTPError) as ctx:
            self.opener.open(req)
        self.assertEqual(ctx.exception.code, 401)

    def test_sync_authorized_header(self):
        req = Request(
            f"{self.base_url}/sync",
            method="POST",
            data=json.dumps({"sync_latest": True, "dry_run": False}).encode(),
            headers={"X-Webhook-Token": "test_secret_token", "Content-Type": "application/json"},
        )
        with self.opener.open(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode())
            self.assertEqual(data["status"], "success")
            self.assertEqual(data["stats"]["uploaded"], 1)

        self.mock_engine.run_sync.assert_called_with(sync_latest=True, dry_run=False)

    def test_sync_authorized_bearer(self):
        req = Request(
            f"{self.base_url}/sync",
            method="POST",
            data=b"{}",
            headers={"Authorization": "Bearer test_secret_token"},
        )
        with self.opener.open(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode())
            self.assertEqual(data["status"], "success")

    def test_sync_authorized_query_param(self):
        req = Request(
            f"{self.base_url}/sync?token=test_secret_token",
            method="POST",
            data=b"{}",
        )
        with self.opener.open(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode())
            self.assertEqual(data["status"], "success")

    def test_sync_async_accepted(self):
        # When wait=false, it runs asynchronously in background and returns 202
        req = Request(
            f"{self.base_url}/sync?wait=false",
            method="POST",
            data=b"{}",
            headers={"X-Webhook-Token": "test_secret_token"},
        )
        with self.opener.open(req) as resp:
            self.assertEqual(resp.status, 202)
            data = json.loads(resp.read().decode())
            self.assertEqual(data["status"], "accepted")

    def test_sync_concurrency_lock_busy(self):
        # Simulate active lock
        self.server.is_syncing = True
        req = Request(
            f"{self.base_url}/sync",
            method="POST",
            data=b"{}",
            headers={"X-Webhook-Token": "test_secret_token"},
        )
        with self.assertRaises(HTTPError) as ctx:
            self.opener.open(req)
        self.assertEqual(ctx.exception.code, 429)
        data = json.loads(ctx.exception.read().decode())
        self.assertEqual(data["status"], "busy")

    def test_not_found(self):
        req = Request(f"{self.base_url}/nonexistent")
        with self.assertRaises(HTTPError) as ctx:
            self.opener.open(req)
        self.assertEqual(ctx.exception.code, 404)


class TestWebhookServerOpenMode(unittest.TestCase):
    """Test webhook server behavior when no token is configured (open mode)."""

    def setUp(self):
        self.mock_engine = MagicMock()
        self.mock_engine.run_sync.return_value = {"uploaded": 1}
        self.opener = build_opener(ProxyHandler({}))
        self.server = WebhookServer(
            engine=self.mock_engine,
            host="127.0.0.1",
            port=0,
            webhook_token="",  # No token
        )
        self.port = self.server.server_address[1]
        self.server.start_background()
        self.base_url = f"http://127.0.0.1:{self.port}"
        time.sleep(0.05)

    def tearDown(self):
        self.server.stop()

    def test_open_mode_allows_sync_without_token(self):
        req = Request(f"{self.base_url}/sync", method="POST", data=b"{}")
        with self.opener.open(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode())
            self.assertEqual(data["status"], "success")


if __name__ == "__main__":
    unittest.main()
