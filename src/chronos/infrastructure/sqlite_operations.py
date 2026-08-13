"""SQLite persistence for full, versioned Agent Operation snapshots."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from chronos.agent.models import AgentOperation, OperationState
from chronos.agent.ports import OperationVersionConflictError
from chronos.agent.serialization import operation_from_dict, operation_to_dict


class SQLiteAgentOperationRepository:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_operations (
                    operation_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS agent_operations_state_updated
                    ON agent_operations(state, updated_at DESC);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection

    def create(self, operation: AgentOperation) -> None:
        payload = json.dumps(operation_to_dict(operation), ensure_ascii=False)
        with closing(self._connect()) as connection, connection:
            try:
                connection.execute(
                    "INSERT INTO agent_operations VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        operation.id,
                        operation.state.value,
                        operation.version,
                        payload,
                        operation.created_at.isoformat(),
                        operation.updated_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError(f"Agent Operation already exists: {operation.id}") from error

    def get(self, operation_id: str) -> AgentOperation | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload_json FROM agent_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        return operation_from_dict(json.loads(row["payload_json"])) if row else None

    def save(self, operation: AgentOperation, expected_version: int) -> None:
        payload = json.dumps(operation_to_dict(operation), ensure_ascii=False)
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                UPDATE agent_operations
                SET state = ?, version = ?, payload_json = ?, updated_at = ?
                WHERE operation_id = ? AND version = ?
                """,
                (
                    operation.state.value,
                    operation.version,
                    payload,
                    operation.updated_at.isoformat(),
                    operation.id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise OperationVersionConflictError(operation.id)

    def list(self, states: tuple[OperationState, ...] | None = None) -> list[AgentOperation]:
        query = "SELECT payload_json FROM agent_operations"
        parameters: tuple[object, ...] = ()
        if states is not None:
            if not states:
                return []
            query += f" WHERE state IN ({','.join('?' for _ in states)})"
            parameters = tuple(item.value for item in states)
        query += " ORDER BY updated_at DESC, operation_id"
        with closing(self._connect()) as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [operation_from_dict(json.loads(row["payload_json"])) for row in rows]
