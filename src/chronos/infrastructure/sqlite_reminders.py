from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from chronos.reminders.models import Reminder, ReminderStatus


class SQLiteReminderRepository:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS reminders (
                reminder_id TEXT PRIMARY KEY, title TEXT NOT NULL,
                trigger_type TEXT NOT NULL, trigger_at TEXT,
                window_start TEXT, window_end TEXT, delivery TEXT NOT NULL,
                priority INTEGER NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, source TEXT NOT NULL)"""
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection

    def list(self) -> list[Reminder]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM reminders ORDER BY COALESCE(trigger_at, window_start), created_at"
            ).fetchall()
        return [_from_row(row) for row in rows]

    def get(self, reminder_id: str) -> Reminder | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM reminders WHERE reminder_id = ?", (reminder_id,)
            ).fetchone()
        return _from_row(row) if row else None

    def save(self, reminder: Reminder) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """INSERT INTO reminders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(reminder_id) DO UPDATE SET title=excluded.title,
                trigger_type=excluded.trigger_type, trigger_at=excluded.trigger_at,
                window_start=excluded.window_start, window_end=excluded.window_end,
                delivery=excluded.delivery, priority=excluded.priority,
                status=excluded.status, source=excluded.source""",
                (
                    reminder.reminder_id, reminder.title, reminder.trigger_type,
                    reminder.trigger_at.isoformat() if reminder.trigger_at else None,
                    reminder.window_start.isoformat() if reminder.window_start else None,
                    reminder.window_end.isoformat() if reminder.window_end else None,
                    reminder.delivery, reminder.priority, reminder.status.value,
                    reminder.created_at.isoformat(), reminder.source,
                ),
            )

    def delete(self, reminder_id: str) -> bool:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "DELETE FROM reminders WHERE reminder_id = ?", (reminder_id,)
            )
            return cursor.rowcount > 0


def _from_row(row: sqlite3.Row) -> Reminder:
    def parse(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value else None
    return Reminder(
        reminder_id=row["reminder_id"], title=row["title"],
        trigger_type=row["trigger_type"], trigger_at=parse(row["trigger_at"]),
        window_start=parse(row["window_start"]), window_end=parse(row["window_end"]),
        delivery=row["delivery"], priority=int(row["priority"]),
        status=ReminderStatus(row["status"]), created_at=datetime.fromisoformat(row["created_at"]),
        source=row["source"],
    )
