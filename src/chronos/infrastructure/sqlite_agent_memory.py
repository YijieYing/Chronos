"""SQLite persistence for imported personal-context candidates."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


class SQLiteAgentMemoryRepository:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_imports (
                    import_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    archive_name TEXT NOT NULL,
                    archive_path TEXT NOT NULL,
                    archive_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    messages_scanned INTEGER NOT NULL,
                    candidates_created INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(source, archive_hash)
                );
                CREATE TABLE IF NOT EXISTS agent_memory_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL,
                    import_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    reviewed_at TEXT,
                    UNIQUE(source, fingerprint)
                );
                CREATE TABLE IF NOT EXISTS agent_context_items (
                    context_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL UNIQUE,
                    source TEXT NOT NULL,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_context_revisions (
                    revision_id TEXT PRIMARY KEY,
                    context_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            _ensure_column(
                connection,
                "agent_memory_candidates",
                "change_type",
                "TEXT NOT NULL DEFAULT 'new'",
            )
            _ensure_column(
                connection, "agent_memory_candidates", "related_context_id", "TEXT"
            )
            _ensure_column(
                connection, "agent_memory_candidates", "related_content", "TEXT"
            )
            _ensure_column(
                connection,
                "agent_context_items",
                "revision",
                "INTEGER NOT NULL DEFAULT 1",
            )
            _ensure_column(connection, "agent_context_items", "deleted_at", "TEXT")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection

    def get_import_by_hash(self, source: str, archive_hash: str) -> dict[str, object] | None:
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM agent_imports WHERE source = ? AND archive_hash = ?",
                (source, archive_hash),
            ).fetchone()
        return dict(row) if row else None

    def save_import(self, item: dict[str, object]) -> dict[str, object]:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO agent_imports VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["import_id"], item["source"], item["archive_name"],
                    item["archive_path"], item["archive_hash"], item["status"],
                    item["messages_scanned"], item["candidates_created"], item["created_at"],
                ),
            )
        return item

    def list_imports(self, limit: int = 50) -> list[dict[str, object]]:
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                "SELECT * FROM agent_imports ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def add_candidates(self, items: list[dict[str, object]]) -> int:
        created = 0
        with closing(self._connect()) as connection, connection:
            for item in items:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO agent_memory_candidates (
                        candidate_id, source, fingerprint, category, content, evidence,
                        source_ref, confidence, status, import_id, created_at, reviewed_at,
                        change_type, related_context_id, related_content
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
                    """,
                    (
                        item["candidate_id"], item["source"], item["fingerprint"],
                        item["category"], item["content"], item["evidence"],
                        item["source_ref"], item["confidence"], "pending",
                        item["import_id"], item["created_at"],
                        item.get("change_type", "new"), item.get("related_context_id"),
                        item.get("related_content"),
                    ),
                )
                created += cursor.rowcount
        return created

    def list_candidates(self, status: str = "pending", limit: int = 300) -> list[dict[str, object]]:
        if status not in {"pending", "accepted", "ignored", "all"}:
            raise ValueError("unknown candidate status")
        where = "" if status == "all" else "WHERE status = ?"
        values: tuple[object, ...] = (limit,) if status == "all" else (status, limit)
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                f"SELECT * FROM agent_memory_candidates {where} ORDER BY created_at DESC LIMIT ?",
                values,
            ).fetchall()
        return [dict(row) for row in rows]

    def review(self, candidate_id: str, accepted: bool) -> dict[str, object]:
        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM agent_memory_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            if row is None:
                raise KeyError(candidate_id)
            if row["status"] != "pending":
                raise ValueError("only pending memory candidates can be reviewed")
            status = "accepted" if accepted else "ignored"
            connection.execute(
                "UPDATE agent_memory_candidates SET status = ?, reviewed_at = ? WHERE candidate_id = ?",
                (status, now, candidate_id),
            )
            if accepted:
                connection.execute(
                    """
                    INSERT INTO agent_context_items (
                        context_id, candidate_id, source, category, content, source_ref,
                        created_at, updated_at, revision, deleted_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, NULL)
                    """,
                    (
                        str(uuid4()), candidate_id, row["source"], row["category"],
                        row["content"], row["source_ref"], now, now,
                    ),
                )
            updated = connection.execute(
                "SELECT * FROM agent_memory_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
        assert updated is not None
        return dict(updated)

    def list_context(self, limit: int = 200) -> list[dict[str, object]]:
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                """
                SELECT * FROM agent_context_items
                WHERE deleted_at IS NULL
                ORDER BY updated_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_context(
        self, context_id: str, *, content: str, category: str
    ) -> dict[str, object]:
        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM agent_context_items WHERE context_id = ? AND deleted_at IS NULL",
                (context_id,),
            ).fetchone()
            if row is None:
                raise KeyError(context_id)
            connection.execute(
                """
                INSERT INTO agent_context_revisions
                VALUES (?, ?, 'update', ?, ?, ?)
                """,
                (str(uuid4()), context_id, row["category"], row["content"], now),
            )
            connection.execute(
                """
                UPDATE agent_context_items
                SET content = ?, category = ?, revision = revision + 1, updated_at = ?
                WHERE context_id = ?
                """,
                (content, category, now, context_id),
            )
            updated = connection.execute(
                "SELECT * FROM agent_context_items WHERE context_id = ?", (context_id,)
            ).fetchone()
        assert updated is not None
        return dict(updated)

    def delete_context(self, context_id: str) -> bool:
        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM agent_context_items WHERE context_id = ? AND deleted_at IS NULL",
                (context_id,),
            ).fetchone()
            if row is None:
                return False
            connection.execute(
                """
                INSERT INTO agent_context_revisions
                VALUES (?, ?, 'delete', ?, ?, ?)
                """,
                (str(uuid4()), context_id, row["category"], row["content"], now),
            )
            connection.execute(
                "UPDATE agent_context_items SET deleted_at = ?, updated_at = ? WHERE context_id = ?",
                (now, now, context_id),
            )
        return True


def _ensure_column(
    connection: sqlite3.Connection, table: str, column: str, definition: str
) -> None:
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
