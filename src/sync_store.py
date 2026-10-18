"""Persistent SQLite storage for tracking synchronized Sole activities."""

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class SyncStore:
    """Manages SQLite database of processed Sole activities."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create database tables and indices if not already present."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS synced_activities (
                    sole_id TEXT PRIMARY KEY,
                    name TEXT,
                    activity_date TEXT,
                    sport TEXT,
                    synced_at TEXT NOT NULL,
                    garmin_status TEXT NOT NULL,
                    notes TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_activity_date
                ON synced_activities(activity_date)
                """
            )
            conn.commit()
        logger.debug("Initialized sync store at %s", self.db_path)

    def is_synced(self, sole_id: str) -> bool:
        """Check if an activity has already been synced or handled."""
        if not sole_id:
            return False
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM synced_activities WHERE sole_id = ?",
                (sole_id,),
            )
            return cursor.fetchone() is not None

    def get_synced_ids(self) -> Set[str]:
        """Return a set of all previously handled Sole activity IDs."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT sole_id FROM synced_activities")
            return {row["sole_id"] for row in cursor.fetchall()}

    def record_activity(
        self,
        sole_id: str,
        name: Optional[str],
        activity_date: Optional[str],
        sport: Optional[str],
        status: str,
        notes: Optional[str] = None,
    ) -> None:
        """Record or update the sync status of an activity."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO synced_activities (
                    sole_id, name, activity_date, sport, synced_at, garmin_status, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sole_id) DO UPDATE SET
                    name = excluded.name,
                    activity_date = excluded.activity_date,
                    sport = excluded.sport,
                    garmin_status = excluded.garmin_status,
                    synced_at = excluded.synced_at,
                    notes = excluded.notes
                """,
                (sole_id, name, activity_date, sport, now_iso, status, notes),
            )
            conn.commit()
        logger.info("Recorded activity %s as '%s' in sync database.", sole_id, status)

    def get_recent_synced(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Return recently recorded synced activities."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT sole_id, name, activity_date, sport, synced_at, garmin_status, notes
                FROM synced_activities
                ORDER BY synced_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_all_synced(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return previously synced activities with status UPLOADED or SKIPPED_DUPLICATE."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT sole_id, name, activity_date, sport, synced_at, garmin_status, notes
                FROM synced_activities
                WHERE garmin_status IN ('UPLOADED', 'SKIPPED_DUPLICATE')
                ORDER BY synced_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def count(self) -> int:
        """Count total synced activities."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM synced_activities")
            return cursor.fetchone()[0]

    def clear_all(self) -> int:
        """Clear all recorded activities from the database."""
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM synced_activities")
            count = cursor.rowcount
            conn.commit()
        logger.info("Cleared %d activities from sync database.", count)
        return count
