"""SQLite append-only persistence for the Chronos Log event stream."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from chronos.agent.models import ChronosLogEntry
from chronos.agent.serialization import log_entry_from_dict, log_entry_to_dict


class SQLiteChronosLogRepository:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS chronos_log_entries (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    entry_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    operation_id TEXT,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS chronos_log_operation
                    ON chronos_log_entries(operation_id, sequence);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection

    def append(self, entry: ChronosLogEntry) -> None:
        payload = json.dumps(log_entry_to_dict(entry), ensure_ascii=False)
        with closing(self._connect()) as connection, connection:
            try:
                connection.execute(
                    """
                    INSERT INTO chronos_log_entries (
                        entry_id, event_type, operation_id, occurred_at, payload_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        entry.id,
                        entry.event_type.value,
                        entry.operation_id,
                        entry.occurred_at.isoformat(),
                        payload,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError(f"Chronos Log entry already exists: {entry.id}") from error

    def list(self, limit: int = 200) -> list[ChronosLogEntry]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM chronos_log_entries
                ORDER BY sequence DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [log_entry_from_dict(json.loads(row["payload_json"])) for row in rows]

    def has_operation(self, operation_id: str) -> bool:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT 1 FROM chronos_log_entries WHERE operation_id = ? LIMIT 1",
                (operation_id,),
            ).fetchone()
        return row is not None
