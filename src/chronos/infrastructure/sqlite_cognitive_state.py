"""SQLite persistence for bounded cognitive-state history."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from chronos.monitor.cognitive import CognitiveStatePoint, RecoveryState


class SQLiteCognitiveStateRepository:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS cognitive_state_points (
                    device_id TEXT NOT NULL,
                    bucket_start TEXT NOT NULL,
                    cognitive_load REAL NOT NULL,
                    mental_fatigue REAL NOT NULL,
                    focus REAL NOT NULL,
                    task_type TEXT,
                    task_confidence REAL NOT NULL,
                    recovery_state TEXT NOT NULL,
                    source TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (device_id, bucket_start)
                );
                CREATE INDEX IF NOT EXISTS cognitive_points_time
                    ON cognitive_state_points(bucket_start);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection

    def upsert(self, point: CognitiveStatePoint) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO cognitive_state_points VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_id, bucket_start) DO UPDATE SET
                    cognitive_load=excluded.cognitive_load,
                    mental_fatigue=excluded.mental_fatigue,
                    focus=excluded.focus,
                    task_type=excluded.task_type,
                    task_confidence=excluded.task_confidence,
                    recovery_state=excluded.recovery_state,
                    source=excluded.source,
                    model_version=excluded.model_version,
                    revision=excluded.revision,
                    updated_at=excluded.updated_at
                """,
                (
                    point.device_id,
                    point.time.isoformat(),
                    point.cognitive_load,
                    point.mental_fatigue,
                    point.focus,
                    point.task_type,
                    point.task_confidence,
                    point.recovery_state.value,
                    point.source,
                    point.model_version,
                    point.revision,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def latest(self) -> CognitiveStatePoint | None:
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM cognitive_state_points ORDER BY bucket_start DESC LIMIT 1"
            ).fetchone()
        return _point_from_row(row) if row else None

    def between(self, start: datetime, end: datetime, limit: int = 288) -> list[CognitiveStatePoint]:
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                """
                SELECT * FROM cognitive_state_points
                WHERE bucket_start >= ? AND bucket_start <= ?
                ORDER BY bucket_start DESC LIMIT ?
                """,
                (start.astimezone(UTC).isoformat(), end.astimezone(UTC).isoformat(), limit),
            ).fetchall()
        return [_point_from_row(row) for row in reversed(rows)]

    def prune(self, now: datetime, retention: timedelta = timedelta(hours=48)) -> int:
        cutoff = now.astimezone(UTC) - retention
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "DELETE FROM cognitive_state_points WHERE bucket_start < ?",
                (cutoff.isoformat(),),
            )
            return cursor.rowcount


def _point_from_row(row: sqlite3.Row) -> CognitiveStatePoint:
    return CognitiveStatePoint(
        device_id=str(row["device_id"]),
        time=datetime.fromisoformat(str(row["bucket_start"])),
        cognitive_load=float(row["cognitive_load"]),
        mental_fatigue=float(row["mental_fatigue"]),
        focus=float(row["focus"]),
        task_type=str(row["task_type"]) if row["task_type"] is not None else None,
        task_confidence=float(row["task_confidence"]),
        recovery_state=RecoveryState(str(row["recovery_state"])),
        source=str(row["source"]),
        model_version=str(row["model_version"]),
        revision=int(row["revision"]),
    )
