"""Lightweight HTTP webhook server for on-demand sync triggers (e.g., from Home Assistant)."""

import hmac
import json
import logging
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from src import __version__
from src.sync_engine import SyncEngine

logger = logging.getLogger(__name__)


class WebhookHandler(BaseHTTPRequestHandler):
    """HTTP request handler for Home Assistant sync triggers and health checks."""

    server: "WebhookServer"  # Type hint for parent server reference

    def log_message(self, format: str, *args: Any) -> None:
        """Route HTTP server logs through Python logging instead of sys.stderr."""
        logger.debug("%s - - [%s] " + format, self.address_string(), self.log_date_time_string(), *args)

    def _send_json(self, status_code: int, data: Dict[str, Any]) -> None:
        """Helper to send a JSON response."""
        response_bytes = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.send_header("X-Service", f"Sole2Garmin-Webhook/{__version__}")
        self.end_headers()
        self.wfile.write(response_bytes)

    def _get_auth_token(self, query_params: Dict[str, Any]) -> Optional[str]:
        """Extract authentication token from headers or query parameters."""
        # 1. Check X-Webhook-Token header
        header_token = self.headers.get("X-Webhook-Token")
        if header_token:
            return header_token.strip()

        # 2. Check Authorization: Bearer <token>
        auth_header = self.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:].strip()

        # 3. Check ?token= query parameter
        if "token" in query_params and query_params["token"]:
            return str(query_params["token"][0]).strip()

        return None

    def _is_authorized(self, query_params: Dict[str, Any]) -> bool:
        """Verify request authorization against configured webhook token."""
        expected_token = getattr(self.server, "webhook_token", "")
        if not expected_token:
            # If no token is configured, webhook is open on local network
            return True

        provided_token = self._get_auth_token(query_params)
        if not provided_token:
            return False

        return hmac.compare_digest(provided_token.encode("utf-8"), expected_token.encode("utf-8"))

    def do_GET(self) -> None:
        """Handle GET requests (health, status, service info)."""
        parsed_url = urlparse(self.path)
        path = parsed_url.path.rstrip("/")

        if path in ("", "/info"):
            self._send_json(200, {
                "service": "Sole2Garmin",
                "version": __version__,
                "status": "running",
                "endpoints": {
                    "POST /sync": "Trigger on-demand synchronization",
                    "GET /health": "Service health check",
                    "GET /status": "Sync status and recent results",
                },
            })
            return

        if path in ("/health", "/status"):
            uptime = time.time() - getattr(self.server, "start_time", time.time())
            self._send_json(200, {
                "status": "ok",
                "version": __version__,
                "is_syncing": self.server.is_syncing,
                "uptime_seconds": round(uptime, 1),
                "last_sync_time": self.server.last_sync_time,
                "last_sync_result": self.server.last_sync_result,
            })
            return

        self._send_json(404, {"error": "Not Found", "path": self.path})

    def do_POST(self) -> None:
        """Handle POST requests (/sync trigger)."""
        parsed_url = urlparse(self.path)
        path = parsed_url.path.rstrip("/")
        query_params = parse_qs(parsed_url.query)

        if path != "/sync":
            self._send_json(404, {"error": "Not Found", "path": self.path})
            return

        # Check authorization
        if not self._is_authorized(query_params):
            logger.warning("Unauthorized /sync request from %s", self.address_string())
            self._send_json(401, {
                "error": "Unauthorized",
                "message": "Missing or invalid webhook token. Provide via 'X-Webhook-Token', 'Authorization: Bearer <token>', or '?token='",
            })
            return

        # Check request body if JSON
        body_data = {}
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > 0:
            try:
                raw_body = self.rfile.read(content_length)
                body_data = json.loads(raw_body.decode("utf-8"))
            except Exception as e:
                logger.debug("Could not parse request body as JSON: %s", e)

        # Parse options from body or query params
        def _parse_bool(val: Any, default: bool) -> bool:
            if val is None:
                return default
            if isinstance(val, bool):
                return val
            if isinstance(val, list) and val:
                val = val[0]
            return str(val).lower() in ("true", "1", "yes")

        sync_latest = _parse_bool(body_data.get("sync_latest", query_params.get("sync_latest")), True)
        dry_run = _parse_bool(body_data.get("dry_run", query_params.get("dry_run")), False)
        wait_for_completion = _parse_bool(body_data.get("wait", query_params.get("wait")), True)

        # Concurrency / debounce guard
        if not self.server.acquire_sync_lock():
            logger.warning("Sync request received from %s while another sync is in progress. Rejecting.", self.address_string())
            self._send_json(429, {
                "status": "busy",
                "message": "A synchronization is already in progress. Please retry after it completes.",
            })
            return

        logger.info(
            "On-demand sync triggered via webhook by %s (sync_latest=%s, dry_run=%s, wait=%s)",
            self.address_string(),
            sync_latest,
            dry_run,
            wait_for_completion,
        )

        def _execute_sync() -> Dict[str, Any]:
            try:
                stats = self.server.engine.run_sync(sync_latest=sync_latest, dry_run=dry_run)
                res = {
                    "status": "success",
                    "message": "Sync completed successfully",
                    "stats": stats,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            except Exception as exc:
                logger.exception("Error executing on-demand sync: %s", exc)
                res = {
                    "status": "error",
                    "message": f"Sync failed: {exc}",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            finally:
                self.server.release_sync_lock(res)
            return res

        if wait_for_completion:
            res = _execute_sync()
            status_code = 200 if res.get("status") == "success" else 500
            self._send_json(status_code, res)
        else:
            # Run asynchronously in background thread
            bg_thread = threading.Thread(target=_execute_sync, daemon=True)
            bg_thread.start()
            self._send_json(202, {
                "status": "accepted",
                "message": "Sync initiated in background",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })


class WebhookServer(ThreadingHTTPServer):
    """Threading HTTP Server holding synchronization state and references."""

    def __init__(
        self,
        engine: SyncEngine,
        host: str = "0.0.0.0",
        port: int = 8080,
        webhook_token: str = "",
    ):
        self.engine = engine
        self.webhook_token = webhook_token or ""
        self.start_time = time.time()
        self.is_syncing = False
        self.last_sync_time: Optional[str] = None
        self.last_sync_result: Optional[Dict[str, Any]] = None
        self._lock = threading.Lock()
        self._server_thread: Optional[threading.Thread] = None

        super().__init__((host, port), WebhookHandler)

    def acquire_sync_lock(self) -> bool:
        """Try to acquire sync execution lock."""
        with self._lock:
            if self.is_syncing:
                return False
            self.is_syncing = True
            return True

    def release_sync_lock(self, result: Dict[str, Any]) -> None:
        """Release sync execution lock and record result."""
        with self._lock:
            self.is_syncing = False
            self.last_sync_time = datetime.now(timezone.utc).isoformat()
            self.last_sync_result = result

    def start_background(self) -> None:
        """Start listening in a background daemon thread."""
        if self._server_thread is not None and self._server_thread.is_alive():
            return
        self._server_thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._server_thread.start()
        logger.info(
            "Webhook server listening on http://%s:%d (auth: %s)",
            self.server_address[0],
            self.server_address[1],
            "configured" if self.webhook_token else "none (open)",
        )

    def stop(self) -> None:
        """Shut down the HTTP server."""
        try:
            self.shutdown()
            self.server_close()
        except Exception as e:
            logger.debug("Error during webhook server shutdown: %s", e)
