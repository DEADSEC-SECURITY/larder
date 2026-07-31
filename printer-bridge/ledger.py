from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path


class PrintLedger:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS printed_jobs (
                    job_id TEXT PRIMARY KEY,
                    payload_digest TEXT NOT NULL,
                    printed_at TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def printed_digest(self, job_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_digest FROM printed_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return row[0] if row else None

    def mark_printed(self, job_id: str, payload_digest: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO printed_jobs (job_id, payload_digest, printed_at)
                VALUES (?, ?, ?)
                ON CONFLICT (job_id) DO NOTHING
                """,
                (job_id, payload_digest, datetime.now(UTC).isoformat()),
            )
