"""SQLite persistence for Interpreter capability gaps."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from chronos.agent.meaning import ResidueStatus
from chronos.agent.residue import Record


class SQLiteResidueRepository:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_residue (
                    record_id TEXT PRIMARY KEY,
                    operation_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    snapshot_version INTEGER NOT NULL,
                    event_id TEXT,
                    item_id TEXT NOT NULL,
                    start_offset INTEGER NOT NULL,
                    end_offset INTEGER NOT NULL,
                    source_text TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    hint TEXT,
                    interpreter_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS agent_residue_status_created
                    ON agent_residue(status, created_at DESC);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection

    def add(self, records: tuple[Record, ...]) -> int:
        created = 0
        with closing(self._connect()) as connection, connection:
            for item in records:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO agent_residue VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        item.id,
                        item.operation_id,
                        item.snapshot_id,
                        item.snapshot_version,
                        item.event_id,
                        item.item_id,
                        item.start,
                        item.end,
                        item.text,
                        item.reason,
                        item.hint,
                        item.interpreter_version,
                        item.status.value,
                        item.created_at.isoformat(),
                    ),
                )
                created += cursor.rowcount
        return created

    def list(
        self, status: ResidueStatus | None = None, limit: int = 500
    ) -> list[Record]:
        if limit <= 0:
            raise ValueError("Residue limit must be positive")
        query = "SELECT * FROM agent_residue"
        values: tuple[object, ...]
        if status is None:
            values = (limit,)
        else:
            query += " WHERE status = ?"
            values = (status.value, limit)
        query += " ORDER BY created_at DESC, record_id LIMIT ?"
        with closing(self._connect()) as connection:
            rows = connection.execute(query, values).fetchall()
        return [_record(row) for row in rows]

    def update(self, record_id: str, status: ResidueStatus) -> Record:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "UPDATE agent_residue SET status = ? WHERE record_id = ?",
                (status.value, record_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(record_id)
            row = connection.execute(
                "SELECT * FROM agent_residue WHERE record_id = ?", (record_id,)
            ).fetchone()
        assert row is not None
        return _record(row)


def _record(row: sqlite3.Row) -> Record:
    return Record(
        id=str(row["record_id"]),
        operation_id=str(row["operation_id"]),
        snapshot_id=str(row["snapshot_id"]),
        snapshot_version=int(row["snapshot_version"]),
        event_id=str(row["event_id"]) if row["event_id"] is not None else None,
        item_id=str(row["item_id"]),
        start=int(row["start_offset"]),
        end=int(row["end_offset"]),
        text=str(row["source_text"]),
        reason=str(row["reason"]),
        hint=str(row["hint"]) if row["hint"] is not None else None,
        interpreter_version=str(row["interpreter_version"]),
        status=ResidueStatus(str(row["status"])),
        created_at=datetime.fromisoformat(str(row["created_at"])),
    )
