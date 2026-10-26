"""CLI entrypoint and background daemon scheduler for Sole2Garmin service."""

import argparse
import logging
import signal
import sys
import time
from typing import Any, NoReturn, Optional

from src import __version__
from src.config import Config
from src.garmin_client import format_local_datetime
from src.sync_engine import SyncEngine
from src.webhook_server import WebhookServer

logger = logging.getLogger(__name__)
_running = True


def _signal_handler(sig: int, frame: Any = None) -> None:
    global _running
    logger.info("Received termination signal (%d). Shutting down Sole2Garmin...", sig)
    _running = False


def print_status(config: Config) -> None:
    """Print database statistics and recent synced activities."""
    from src.sync_store import SyncStore

    db_path = config.data_dir / "sync_state.db"
    if not db_path.exists():
        print("No sync database found. No activities have been processed yet.")
        return

    store = SyncStore(db_path=db_path)
    total = store.count()
    recent = store.get_recent_synced(limit=10)

    print("==================================================")
    print(f" Sole2Garmin Status — Total Processed: {total}")
    print("==================================================")

    if not recent:
        print("Database is initialized, but no activities recorded yet.")
        return

    print(f"{'Activity Name':<30} | {'Sport':<12} | {'Activity Date':<20} | {'Status':<18}")
    print("-" * 88)
    for row in recent:
        act_name = (row.get("name") or "Unnamed")[:28]
        sport = (row.get("sport") or "RUNNING")[:10]
        act_date = format_local_datetime(row.get("activity_date"))
        status = row.get("garmin_status") or "UNKNOWN"
        print(f"{act_name:<30} | {sport:<12} | {act_date:<20} | {status:<18}")
    print("==================================================")


def run_daemon(engine: SyncEngine, interval_minutes: int, config: Optional[Config] = None) -> None:
    """Run synchronization continuously in a background loop and/or webhook server."""
    webhook_srv: Optional[WebhookServer] = None
    if config and config.webhook_enabled:
        try:
            webhook_srv = WebhookServer(
                engine=engine,
                host=config.webhook_host,
                port=config.webhook_port,
                webhook_token=config.webhook_token,
            )
            webhook_srv.start_background()
        except Exception as exc:
            logger.error("Failed to start webhook server: %s", exc)

    if interval_minutes <= 0:
        logger.info(
            "Periodic polling disabled (SYNC_INTERVAL_MINUTES=%d). Operating exclusively in on-demand webhook mode.",
            interval_minutes,
        )
        while _running:
            time.sleep(1)
    else:
        interval_seconds = max(interval_minutes * 60, 60)
        logger.info("Starting Sole2Garmin background daemon (Interval: %d min)...", interval_minutes)

        while _running:
            try:
                logger.info("Starting scheduled sync cycle...")
                engine.run_sync()
            except Exception as exc:
                logger.exception("Unexpected error in daemon sync cycle: %s", exc)

            logger.info("Sleeping for %d minutes until next poll cycle...", interval_minutes)
            slept = 0
            while _running and slept < interval_seconds:
                time.sleep(1)
                slept += 1

    if webhook_srv:
        logger.info("Stopping webhook server...")
        webhook_srv.stop()

    logger.info("Sole2Garmin daemon terminated cleanly.")


def main() -> None:
    """Main CLI entrypoint."""
    parser = argparse.ArgumentParser(
        prog="sole2garmin",
        description="Automated synchronization from Sole Fitness (Dyaco / SOLE+) to Garmin Connect with Physio TrueUp.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--sync-latest", action="store_true", help="Sync only the single most recent activity and exit.")
    parser.add_argument("--batch", type=int, default=None, metavar="N", help="Sync up to N recent activities and exit.")
    parser.add_argument("--status", action="store_true", help="Display local sync database history and exit.")
    parser.add_argument("--re-sync", action="store_true", help="Clear sync database history and trigger a fresh sync run.")
    parser.add_argument("--dry-run", action="store_true", help="Simulate sync without uploading to Garmin Connect or persisting.")
    parser.add_argument("--update-titles", action="store_true", help="Retroactively update titles and categories for previously synced activities on Garmin Connect.")
    parser.add_argument("--daemon", action="store_true", help="Force daemon polling mode (default when no one-off action is passed).")
    parser.add_argument("--webhook", action="store_true", help="Run standalone webhook server for Home Assistant triggers.")

    args = parser.parse_args()

    config = Config.from_env()

    # Logging setup
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(level=getattr(logging, config.log_level, logging.INFO), format=log_format)

    if args.status:
        print_status(config)
        return

    engine = SyncEngine(config=config)

    # Signal handlers
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    if args.update_titles:
        count = engine.sync_previously_synced_activities(limit=args.batch or 50)
        logger.info("Title update completed: %d activities processed.", count)
        return

    if args.re_sync:
        cleared = engine.store.clear_all()
        logger.info("Cleared %d activities from sync database. Triggering fresh sync...", cleared)
        engine.run_sync(limit=args.batch, dry_run=args.dry_run)
        return

    if args.sync_latest:
        engine.run_sync(sync_latest=True, dry_run=args.dry_run)
        return

    if args.batch is not None:
        engine.run_sync(limit=args.batch, dry_run=args.dry_run)
        return

    if args.dry_run:
        engine.run_sync(dry_run=True)
        return

    if args.webhook:
        server = WebhookServer(
            engine=engine,
            host=config.webhook_host,
            port=config.webhook_port,
            webhook_token=config.webhook_token,
        )
        logger.info(
            "Starting standalone webhook server on http://%s:%d (auth: %s)...",
            config.webhook_host,
            config.webhook_port,
            "configured" if config.webhook_token else "none (open)",
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.stop()
        return

    # Default to daemon mode (handles polling and/or background webhook)
    run_daemon(engine, config.sync_interval_minutes, config=config)


if __name__ == "__main__":
    main()
