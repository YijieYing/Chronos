import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from chronos.infrastructure.sqlite_cognitive_state import (
    SQLiteCognitiveStateRepository,
)
from chronos.infrastructure.sqlite_schedule import SQLiteScheduleRepository
from chronos.infrastructure.sqlite_proposals import SQLiteProposalRepository
from chronos.infrastructure.sqlite_timeline import SQLiteTimelineRepository


_connect = sqlite3.connect


class TrackingConnection(sqlite3.Connection):
    was_closed: bool

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.was_closed = False

    def close(self) -> None:
        self.was_closed = True
        super().close()


class SQLiteConnectionLifecycleTest(TestCase):
    def test_repositories_close_every_connection(self) -> None:
        connections: list[TrackingConnection] = []

        def tracked_connect(*args, **kwargs) -> TrackingConnection:
            kwargs["factory"] = TrackingConnection
            connection = _connect(*args, **kwargs)
            connections.append(connection)
            return connection

        with TemporaryDirectory() as temporary, patch.object(
            sqlite3, "connect", side_effect=tracked_connect
        ):
            database = Path(temporary) / "chronos.sqlite3"
            schedule = SQLiteScheduleRepository(database)
            cognitive = SQLiteCognitiveStateRepository(database)
            timeline = SQLiteTimelineRepository(database)
            proposals = SQLiteProposalRepository(database)

            schedule.list_tasks()
            cognitive.latest()
            timeline.list_tasks()
            proposals.list()

        self.assertGreaterEqual(len(connections), 8)
        self.assertTrue(all(connection.was_closed for connection in connections))
