"""SQLite implementation of the Schedule repository port."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

from chronos.schedule.models import (
    BlockStatus,
    FixedBlock,
    Agenda,
    AgendaStatus,
    ScheduleBlock,
    Task,
    TaskStatus,
    UnscheduledTask,
)


class SQLiteScheduleRepository:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                -- Legacy storage names stay stable while the Python domain calls this Agenda.
                -- A later migration can rename plans/plan_id without blocking the Agent Plan.
                CREATE TABLE IF NOT EXISTS schedule_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    estimated_minutes INTEGER NOT NULL,
                    priority INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    deadline TEXT,
                    splittable INTEGER NOT NULL,
                    min_chunk_minutes INTEGER NOT NULL,
                    preferred_start TEXT,
                    cognitive_intensity REAL NOT NULL DEFAULT 0.5,
                    spectrum REAL NOT NULL DEFAULT 0.5,
                    task_type TEXT NOT NULL DEFAULT 'execution',
                    fixed INTEGER NOT NULL DEFAULT 0,
                    source TEXT NOT NULL DEFAULT 'user',
                    recurrence_json TEXT
                );
                CREATE TABLE IF NOT EXISTS fixed_blocks (
                    block_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    start_at TEXT NOT NULL,
                    end_at TEXT NOT NULL,
                    source TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS plans (
                    plan_id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    target_date TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    based_on_version INTEGER
                );
                CREATE UNIQUE INDEX IF NOT EXISTS plans_date_version
                    ON plans(target_date, version);
                CREATE TABLE IF NOT EXISTS schedule_blocks (
                    block_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL REFERENCES plans(plan_id) ON DELETE CASCADE,
                    task_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    start_at TEXT NOT NULL,
                    end_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    flexibility TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS unscheduled_tasks (
                    plan_id TEXT NOT NULL REFERENCES plans(plan_id) ON DELETE CASCADE,
                    task_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    remaining_minutes INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    PRIMARY KEY (plan_id, task_id)
                );
                """
            )
            _ensure_task_columns(connection)

    def list_tasks(self) -> list[Task]:
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                "SELECT * FROM tasks ORDER BY status, priority DESC, created_at"
            ).fetchall()
        return [_task_from_row(row) for row in rows]

    def save_task(self, task: Task) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO tasks (
                    task_id, title, estimated_minutes, priority, status, created_at,
                    deadline, splittable, min_chunk_minutes, preferred_start,
                    cognitive_intensity, spectrum, task_type, fixed, source,
                    recurrence_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    title=excluded.title,
                    estimated_minutes=excluded.estimated_minutes,
                    priority=excluded.priority,
                    status=excluded.status,
                    deadline=excluded.deadline,
                    splittable=excluded.splittable,
                    min_chunk_minutes=excluded.min_chunk_minutes,
                    preferred_start=excluded.preferred_start,
                    cognitive_intensity=excluded.cognitive_intensity,
                    spectrum=excluded.spectrum,
                    task_type=excluded.task_type,
                    fixed=excluded.fixed,
                    source=excluded.source,
                    recurrence_json=excluded.recurrence_json
                """,
                (
                    task.task_id,
                    task.title,
                    task.estimated_minutes,
                    task.priority,
                    task.status.value,
                    task.created_at.isoformat(),
                    task.deadline.isoformat() if task.deadline else None,
                    int(task.splittable),
                    task.min_chunk_minutes,
                    task.preferred_start.isoformat() if task.preferred_start else None,
                    task.cognitive_intensity,
                    task.spectrum,
                    task.task_type,
                    int(task.fixed),
                    task.source,
                    json.dumps(task.recurrence) if task.recurrence else None,
                ),
            )

    def delete_task(self, task_id: str) -> bool:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
            return cursor.rowcount > 0

    def get_task(self, task_id: str) -> Task | None:
        with closing(self._connect()) as connection, connection:
            row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return _task_from_row(row) if row else None

    def list_fixed_blocks(self, target_date: date) -> list[FixedBlock]:
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                "SELECT * FROM fixed_blocks WHERE substr(start_at, 1, 10) = ? ORDER BY start_at",
                (target_date.isoformat(),),
            ).fetchall()
        return [_fixed_from_row(row) for row in rows]

    def save_fixed_block(self, block: FixedBlock) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT OR REPLACE INTO fixed_blocks VALUES (?, ?, ?, ?, ?)",
                (
                    block.block_id,
                    block.title,
                    block.start_at.isoformat(),
                    block.end_at.isoformat(),
                    block.source,
                ),
            )

    def delete_fixed_block(self, block_id: str) -> bool:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute("DELETE FROM fixed_blocks WHERE block_id = ?", (block_id,))
            return cursor.rowcount > 0

    def next_plan_version(self, target_date: date) -> int:
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS version FROM plans WHERE target_date = ?",
                (target_date.isoformat(),),
            ).fetchone()
        return int(row["version"])

    def save_agenda(self, plan: Agenda) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO plans VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    plan.agenda_id,
                    plan.version,
                    plan.target_date.isoformat(),
                    plan.timezone,
                    plan.status.value,
                    plan.created_at.isoformat(),
                    plan.based_on_version,
                ),
            )
            connection.executemany(
                "INSERT INTO schedule_blocks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        block.block_id,
                        plan.agenda_id,
                        block.task_id,
                        block.title,
                        block.start_at.isoformat(),
                        block.end_at.isoformat(),
                        block.status.value,
                        block.flexibility,
                    )
                    for block in plan.blocks
                ],
            )
            connection.executemany(
                "INSERT INTO unscheduled_tasks VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        plan.agenda_id,
                        item.task_id,
                        item.title,
                        item.remaining_minutes,
                        item.reason,
                    )
                    for item in plan.unscheduled
                ],
            )

    def get_agenda(self, plan_id: str) -> Agenda | None:
        with closing(self._connect()) as connection, connection:
            row = connection.execute("SELECT * FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
            if row is None:
                return None
            return self._agenda_from_row(connection, row)

    def latest_agenda(self, target_date: date) -> Agenda | None:
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM plans WHERE target_date = ? ORDER BY version DESC LIMIT 1",
                (target_date.isoformat(),),
            ).fetchone()
            if row is None:
                return None
            return self._agenda_from_row(connection, row)

    def activate_agenda(self, plan_id: str) -> Agenda:
        with closing(self._connect()) as connection, connection:
            row = connection.execute("SELECT * FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
            if row is None:
                raise KeyError(plan_id)
            connection.execute(
                "UPDATE plans SET status = ? WHERE target_date = ? AND status = ?",
                (
                    AgendaStatus.SUPERSEDED.value,
                    row["target_date"],
                    AgendaStatus.ACTIVE.value,
                ),
            )
            connection.execute(
                "UPDATE plans SET status = ? WHERE plan_id = ?",
                (AgendaStatus.ACTIVE.value, plan_id),
            )
        plan = self.get_agenda(plan_id)
        assert plan is not None
        return plan

    def apply_task_agenda_batch(self, tasks: list[Task], plans: list[Agenda]) -> None:
        """Atomically persist task series and activate every previewed daily plan."""
        with closing(self._connect()) as connection, connection:
            for task in tasks:
                _save_task(connection, task)
            for plan in plans:
                _save_agenda(connection, plan)
                connection.execute(
                    "UPDATE plans SET status = ? WHERE target_date = ? "
                    "AND status = ? AND plan_id != ?",
                    (
                        AgendaStatus.SUPERSEDED.value,
                        plan.target_date.isoformat(),
                        AgendaStatus.ACTIVE.value,
                        plan.agenda_id,
                    ),
                )
                connection.execute(
                    "UPDATE plans SET status = ? WHERE plan_id = ?",
                    (AgendaStatus.ACTIVE.value, plan.agenda_id),
                )

    def replace_task_agenda_batch(self, deleted_task_ids: list[str], plans: list[Agenda]) -> None:
        with closing(self._connect()) as connection, connection:
            connection.executemany(
                "DELETE FROM tasks WHERE task_id = ?",
                [(value,) for value in deleted_task_ids],
            )
            for plan in plans:
                _save_agenda(connection, plan)
                connection.execute(
                    "UPDATE plans SET status = ? WHERE target_date = ? "
                    "AND status = ? AND plan_id != ?",
                    (
                        AgendaStatus.SUPERSEDED.value,
                        plan.target_date.isoformat(),
                        AgendaStatus.ACTIVE.value,
                        plan.agenda_id,
                    ),
                )
                connection.execute(
                    "UPDATE plans SET status = ? WHERE plan_id = ?",
                    (AgendaStatus.ACTIVE.value, plan.agenda_id),
                )

    def get_setting(self, key: str) -> str | None:
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT value FROM schedule_settings WHERE key = ?", (key,)
            ).fetchone()
        return str(row["value"]) if row else None

    def set_setting(self, key: str, value: str) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO schedule_settings VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    @staticmethod
    def _agenda_from_row(connection: sqlite3.Connection, row: sqlite3.Row) -> Agenda:
        blocks = connection.execute(
            "SELECT * FROM schedule_blocks WHERE plan_id = ? ORDER BY start_at",
            (row["plan_id"],),
        ).fetchall()
        unscheduled = connection.execute(
            "SELECT * FROM unscheduled_tasks WHERE plan_id = ? ORDER BY title",
            (row["plan_id"],),
        ).fetchall()
        return Agenda(
            agenda_id=row["plan_id"],
            version=int(row["version"]),
            target_date=date.fromisoformat(row["target_date"]),
            timezone=row["timezone"],
            status=AgendaStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            based_on_version=row["based_on_version"],
            blocks=tuple(
                ScheduleBlock(
                    block_id=item["block_id"],
                    task_id=item["task_id"],
                    title=item["title"],
                    start_at=datetime.fromisoformat(item["start_at"]),
                    end_at=datetime.fromisoformat(item["end_at"]),
                    status=BlockStatus(item["status"]),
                    flexibility=item["flexibility"],
                )
                for item in blocks
            ),
            unscheduled=tuple(
                UnscheduledTask(
                    task_id=item["task_id"],
                    title=item["title"],
                    remaining_minutes=int(item["remaining_minutes"]),
                    reason=item["reason"],
                )
                for item in unscheduled
            ),
        )


def _task_from_row(row: sqlite3.Row) -> Task:
    return Task(
        task_id=row["task_id"],
        title=row["title"],
        estimated_minutes=int(row["estimated_minutes"]),
        priority=int(row["priority"]),
        status=TaskStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        deadline=datetime.fromisoformat(row["deadline"]) if row["deadline"] else None,
        splittable=bool(row["splittable"]),
        min_chunk_minutes=int(row["min_chunk_minutes"]),
        preferred_start=(
            datetime.fromisoformat(row["preferred_start"]) if row["preferred_start"] else None
        ),
        cognitive_intensity=float(row["cognitive_intensity"]),
        spectrum=float(row["spectrum"]),
        task_type=str(row["task_type"]),
        fixed=bool(row["fixed"]),
        source=str(row["source"]),
        recurrence=json.loads(row["recurrence_json"]) if row["recurrence_json"] else None,
    )


def _save_task(connection: sqlite3.Connection, task: Task) -> None:
    connection.execute(
        """
        INSERT INTO tasks (
            task_id, title, estimated_minutes, priority, status, created_at,
            deadline, splittable, min_chunk_minutes, preferred_start,
            cognitive_intensity, spectrum, task_type, fixed, source, recurrence_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(task_id) DO UPDATE SET
            title=excluded.title, estimated_minutes=excluded.estimated_minutes,
            priority=excluded.priority, status=excluded.status, deadline=excluded.deadline,
            splittable=excluded.splittable, min_chunk_minutes=excluded.min_chunk_minutes,
            preferred_start=excluded.preferred_start,
            cognitive_intensity=excluded.cognitive_intensity, spectrum=excluded.spectrum,
            task_type=excluded.task_type, fixed=excluded.fixed, source=excluded.source,
            recurrence_json=excluded.recurrence_json
        """,
        (
            task.task_id,
            task.title,
            task.estimated_minutes,
            task.priority,
            task.status.value,
            task.created_at.isoformat(),
            task.deadline.isoformat() if task.deadline else None,
            int(task.splittable),
            task.min_chunk_minutes,
            task.preferred_start.isoformat() if task.preferred_start else None,
            task.cognitive_intensity,
            task.spectrum,
            task.task_type,
            int(task.fixed),
            task.source,
            json.dumps(task.recurrence) if task.recurrence else None,
        ),
    )


def _save_agenda(connection: sqlite3.Connection, plan: Agenda) -> None:
    connection.execute(
        "INSERT INTO plans VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            plan.agenda_id,
            plan.version,
            plan.target_date.isoformat(),
            plan.timezone,
            plan.status.value,
            plan.created_at.isoformat(),
            plan.based_on_version,
        ),
    )
    connection.executemany(
        "INSERT INTO schedule_blocks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                block.block_id,
                plan.agenda_id,
                block.task_id,
                block.title,
                block.start_at.isoformat(),
                block.end_at.isoformat(),
                block.status.value,
                block.flexibility,
            )
            for block in plan.blocks
        ],
    )
    connection.executemany(
        "INSERT INTO unscheduled_tasks VALUES (?, ?, ?, ?, ?)",
        [
            (
                plan.agenda_id,
                item.task_id,
                item.title,
                item.remaining_minutes,
                item.reason,
            )
            for item in plan.unscheduled
        ],
    )


def _fixed_from_row(row: sqlite3.Row) -> FixedBlock:
    return FixedBlock(
        block_id=row["block_id"],
        title=row["title"],
        start_at=datetime.fromisoformat(row["start_at"]),
        end_at=datetime.fromisoformat(row["end_at"]),
        source=row["source"],
    )


def _ensure_task_columns(connection: sqlite3.Connection) -> None:
    columns = {
        str(row["name"]) for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
    }
    additions = {
        "preferred_start": "TEXT",
        "cognitive_intensity": "REAL NOT NULL DEFAULT 0.5",
        "spectrum": "REAL NOT NULL DEFAULT 0.5",
        "task_type": "TEXT NOT NULL DEFAULT 'execution'",
        "fixed": "INTEGER NOT NULL DEFAULT 0",
        "source": "TEXT NOT NULL DEFAULT 'user'",
        "recurrence_json": "TEXT",
    }
    for name, declaration in additions.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE tasks ADD COLUMN {name} {declaration}")
