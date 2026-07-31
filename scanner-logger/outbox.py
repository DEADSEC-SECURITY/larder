from __future__ import annotations

import json
import sqlite3
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class PendingScan:
    event_id: str
    scanner_code: str
    payload: str
    received_at: str
    attempts: int


class ScanOutbox:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS scan_outbox (
                    event_id TEXT PRIMARY KEY,
                    scanner_code TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL NOT NULL DEFAULT 0,
                    last_error TEXT,
                    delivered_at TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ix_scan_outbox_due "
                "ON scan_outbox (delivered_at, next_attempt_at)"
            )

    def enqueue(self, scanner_code: str, payload: str) -> PendingScan:
        scan = PendingScan(
            event_id=str(uuid4()),
            scanner_code=scanner_code,
            payload=payload,
            received_at=datetime.now(UTC).isoformat(),
            attempts=0,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO scan_outbox
                    (event_id, scanner_code, payload, received_at, next_attempt_at)
                VALUES (?, ?, ?, ?, 0)
                """,
                (scan.event_id, scan.scanner_code, scan.payload, scan.received_at),
            )
        return scan

    def due(self, *, limit: int = 50) -> list[PendingScan]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_id, scanner_code, payload, received_at, attempts
                FROM scan_outbox
                WHERE delivered_at IS NULL AND next_attempt_at <= ?
                ORDER BY received_at, event_id
                LIMIT ?
                """,
                (time.time(), limit),
            ).fetchall()
        return [PendingScan(*row) for row in rows]

    def mark_delivered(self, event_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE scan_outbox SET delivered_at = ?, last_error = NULL WHERE event_id = ?",
                (datetime.now(UTC).isoformat(), event_id),
            )

    def mark_retry(self, event_id: str, attempts: int, error: str) -> float:
        next_attempt = time.time() + min(300.0, 2.0 ** min(attempts, 8))
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE scan_outbox
                SET attempts = ?, next_attempt_at = ?, last_error = ?
                WHERE event_id = ? AND delivered_at IS NULL
                """,
                (attempts, next_attempt, error[:500], event_id),
            )
        return next_attempt

    def pending_count(self) -> int:
        with self._connect() as connection:
            return int(
                connection.execute(
                    "SELECT count(*) FROM scan_outbox WHERE delivered_at IS NULL"
                ).fetchone()[0]
            )


def post_scan(scan: PendingScan, api_url: str, token: str, timeout: float) -> dict:
    request = urllib.request.Request(
        api_url,
        data=json.dumps(
            {
                "event_id": scan.event_id,
                "scanner_code": scan.scanner_code,
                "payload": scan.payload,
                "received_at": scan.received_at,
            }
        ).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def run_sender(
    outbox: ScanOutbox,
    api_url: str,
    token: str,
    stop: threading.Event,
    emit: Callable[..., None],
    *,
    request_timeout: float = 10.0,
    idle_seconds: float = 1.0,
) -> None:
    while not stop.is_set():
        scans = outbox.due()
        if not scans:
            stop.wait(idle_seconds)
            continue
        for scan in scans:
            if stop.is_set():
                return
            try:
                result = post_scan(scan, api_url, token, request_timeout)
                outbox.mark_delivered(scan.event_id)
                emit(
                    "scan_delivered",
                    event_id=scan.event_id,
                    scanner_code=scan.scanner_code,
                    result=result.get("result"),
                    duplicate=result.get("duplicate", False),
                    pending=outbox.pending_count(),
                )
            except Exception as exc:
                attempts = scan.attempts + 1
                next_attempt = outbox.mark_retry(
                    scan.event_id, attempts, f"{type(exc).__name__}: {exc}"
                )
                emit(
                    "scan_delivery_retry",
                    event_id=scan.event_id,
                    scanner_code=scan.scanner_code,
                    attempts=attempts,
                    retry_at=datetime.fromtimestamp(next_attempt, UTC).isoformat(),
                    error=type(exc).__name__,
                )
