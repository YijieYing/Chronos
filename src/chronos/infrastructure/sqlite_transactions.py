"""SQLite persistence for Runtime adjustment transactions."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from chronos.agent.models import AdjustmentTransaction
from chronos.agent.transaction_serialization import transaction_from_dict, transaction_to_dict


class SQLiteAdjustmentTransactionRepository:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS adjustment_transactions (
                transaction_id TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL)"""
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection

    def save(self, transaction: AdjustmentTransaction) -> None:
        payload = json.dumps(transaction_to_dict(transaction), ensure_ascii=False)
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """INSERT INTO adjustment_transactions VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(transaction_id) DO UPDATE SET
                status=excluded.status, payload_json=excluded.payload_json""",
                (
                    transaction.id,
                    transaction.operation_id,
                    transaction.status.value,
                    payload,
                    transaction.created_at.isoformat(),
                ),
            )

    def get(self, transaction_id: str) -> AdjustmentTransaction | None:
        return self._find("transaction_id", transaction_id)

    def get_by_operation(self, operation_id: str) -> AdjustmentTransaction | None:
        return self._find("operation_id", operation_id)

    def _find(self, field: str, value: str) -> AdjustmentTransaction | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                f"SELECT payload_json FROM adjustment_transactions WHERE {field} = ?",
                (value,),
            ).fetchone()
        return transaction_from_dict(json.loads(row["payload_json"])) if row else None
