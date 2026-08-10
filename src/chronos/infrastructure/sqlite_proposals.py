"""SQLite persistence for reviewable Schedule proposals."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path


class SQLiteProposalRepository:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schedule_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    request_text TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection

    def save(self, proposal: dict[str, object]) -> dict[str, object]:
        now = datetime.now(UTC).isoformat()
        created_at = str(proposal.get("created_at") or now)
        stored = {**proposal, "created_at": created_at, "updated_at": now}
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO schedule_proposals VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(proposal_id) DO UPDATE SET
                    status=excluded.status,
                    request_text=excluded.request_text,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (
                    stored["proposal_id"],
                    stored["status"],
                    stored["request_text"],
                    json.dumps(stored, ensure_ascii=False),
                    created_at,
                    now,
                ),
            )
        return stored

    def get(self, proposal_id: str) -> dict[str, object] | None:
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT payload_json FROM schedule_proposals WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def list(self, limit: int = 100) -> list[dict[str, object]]:
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM schedule_proposals
                ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]
