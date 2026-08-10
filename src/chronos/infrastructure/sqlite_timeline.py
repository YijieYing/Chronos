"""SQLite persistence for timeline tasks and recurring task series."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


TASK_TYPES = {
    "creative",
    "coding",
    "research",
    "communication",
    "execution",
    "meeting",
    "recovery",
}
TASK_SOURCES = {"user", "agent", "schedule"}


class SQLiteTimelineRepository:
    """Store one row per task or recurring series, never expanded occurrences."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS timeline_tasks (
                    task_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    start_at_ms INTEGER NOT NULL,
                    end_at_ms INTEGER NOT NULL,
                    predicted_end_at_ms INTEGER NOT NULL,
                    cognitive_intensity REAL NOT NULL,
                    spectrum REAL NOT NULL,
                    fixed INTEGER NOT NULL,
                    task_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    recurrence_frequency TEXT,
                    recurrence_weekdays TEXT,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS timeline_tasks_start
                    ON timeline_tasks(start_at_ms);
                """
            )

    def list_tasks(self) -> list[dict[str, object]]:
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                "SELECT * FROM timeline_tasks ORDER BY start_at_ms, created_at_ms"
            ).fetchall()
        return [_task_from_row(row) for row in rows]

    def get_task(self, task_id: str) -> dict[str, object] | None:
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM timeline_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        return _task_from_row(row) if row else None

    def create_task(self, payload: dict[str, Any]) -> dict[str, object]:
        task = validate_timeline_task(payload)
        with closing(self._connect()) as connection, connection:
            try:
                connection.execute(
                    """
                    INSERT INTO timeline_tasks VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    _task_values(task),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError(f"timeline task already exists: {task['id']}") from error
        return task

    def save_task(self, payload: dict[str, Any]) -> dict[str, object]:
        task = validate_timeline_task(payload)
        existing = self.get_task(str(task["id"]))
        if existing is not None:
            task["created_at"] = existing["created_at"]
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO timeline_tasks VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(task_id) DO UPDATE SET
                    title=excluded.title,
                    start_at_ms=excluded.start_at_ms,
                    end_at_ms=excluded.end_at_ms,
                    predicted_end_at_ms=excluded.predicted_end_at_ms,
                    cognitive_intensity=excluded.cognitive_intensity,
                    spectrum=excluded.spectrum,
                    fixed=excluded.fixed,
                    task_type=excluded.task_type,
                    source=excluded.source,
                    recurrence_frequency=excluded.recurrence_frequency,
                    recurrence_weekdays=excluded.recurrence_weekdays,
                    updated_at_ms=excluded.updated_at_ms
                """,
                _task_values(task),
            )
        return task

    def delete_task(self, task_id: str) -> bool:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "DELETE FROM timeline_tasks WHERE task_id = ?", (task_id,)
            )
            return cursor.rowcount > 0


def validate_timeline_task(payload: dict[str, Any]) -> dict[str, object]:
    now = int(payload.get("updated_at", payload.get("created_at", 0)))
    task_id = str(payload["id"]).strip()
    title = str(payload["title"]).strip()
    start = int(payload["start"])
    end = int(payload["end"])
    predicted_end = int(payload.get("predicted_end", end))
    intensity = float(payload["intensity"])
    spectrum = float(payload["spectrum"])
    task_type = str(payload["task_type"])
    source = str(payload.get("source", "user"))
    recurrence = _validate_recurrence(payload.get("recurrence"))

    if not task_id or not title:
        raise ValueError("timeline task id and title are required")
    if end <= start or predicted_end < start:
        raise ValueError("timeline task must have positive duration")
    if not 0 <= intensity <= 1 or not 0 <= spectrum <= 1:
        raise ValueError("intensity and spectrum must be between 0 and 1")
    if task_type not in TASK_TYPES:
        raise ValueError(f"unsupported task type: {task_type}")
    if source not in TASK_SOURCES:
        raise ValueError(f"unsupported task source: {source}")
    if now <= 0:
        raise ValueError("created_at and updated_at are required")

    return {
        "id": task_id,
        "title": title,
        "start": start,
        "end": end,
        "predicted_end": predicted_end,
        "intensity": intensity,
        "spectrum": spectrum,
        "fixed": bool(payload.get("fixed", False)),
        "task_type": task_type,
        "source": source,
        "recurrence": recurrence,
        "created_at": int(payload.get("created_at", now)),
        "updated_at": now,
    }


def _validate_recurrence(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("recurrence must be an object")
    frequency = str(value.get("frequency", ""))
    if frequency == "daily":
        return {"frequency": "daily"}
    if frequency != "weekly":
        raise ValueError("recurrence frequency must be daily or weekly")
    raw_weekdays = value.get("weekdays")
    if not isinstance(raw_weekdays, list):
        raise ValueError("weekly recurrence requires weekdays")
    weekdays = sorted({int(day) for day in raw_weekdays})
    if not weekdays or any(day < 0 or day > 6 for day in weekdays):
        raise ValueError("weekdays must contain values from 0 to 6")
    return {"frequency": "weekly", "weekdays": weekdays}


def _task_values(task: dict[str, object]) -> tuple[object, ...]:
    recurrence = task["recurrence"]
    frequency = recurrence["frequency"] if isinstance(recurrence, dict) else None
    weekdays = (
        json.dumps(recurrence.get("weekdays", []))
        if isinstance(recurrence, dict) and frequency == "weekly"
        else None
    )
    return (
        task["id"],
        task["title"],
        task["start"],
        task["end"],
        task["predicted_end"],
        task["intensity"],
        task["spectrum"],
        int(bool(task["fixed"])),
        task["task_type"],
        task["source"],
        frequency,
        weekdays,
        task["created_at"],
        task["updated_at"],
    )


def _task_from_row(row: sqlite3.Row) -> dict[str, object]:
    recurrence: dict[str, object] | None = None
    if row["recurrence_frequency"] == "daily":
        recurrence = {"frequency": "daily"}
    elif row["recurrence_frequency"] == "weekly":
        recurrence = {
            "frequency": "weekly",
            "weekdays": json.loads(row["recurrence_weekdays"] or "[]"),
        }
    return {
        "id": row["task_id"],
        "title": row["title"],
        "start": int(row["start_at_ms"]),
        "end": int(row["end_at_ms"]),
        "predicted_end": int(row["predicted_end_at_ms"]),
        "intensity": float(row["cognitive_intensity"]),
        "spectrum": float(row["spectrum"]),
        "fixed": bool(row["fixed"]),
        "task_type": row["task_type"],
        "source": row["source"],
        "recurrence": recurrence,
        "created_at": int(row["created_at_ms"]),
        "updated_at": int(row["updated_at_ms"]),
    }
