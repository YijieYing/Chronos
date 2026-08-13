from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from chronos.agent.log_service import ChronosLogService
from chronos.agent.legacy_log import migrate_proposal_history
from chronos.agent.models import LogEventType, TimelineReference
from chronos.agent.serialization import log_entry_from_dict, log_entry_to_dict
from chronos.infrastructure.sqlite_chronos_log import SQLiteChronosLogRepository


class ChronosLogTest(TestCase):
    def test_entries_are_append_only_ordered_and_round_trip_references(self) -> None:
        with TemporaryDirectory() as temporary:
            repository = SQLiteChronosLogRepository(
                Path(temporary) / "nested" / "chronos.sqlite3"
            )
            service = ChronosLogService(repository)
            first = service.append(
                LogEventType.USER_PROMPT,
                "把 Research 挪到晚上",
                operation_id="operation-1",
                references=(TimelineReference("task", id="research"),),
                entry_id="first",
                occurred_at=datetime(2026, 8, 13, 10, 0, tzinfo=UTC),
            )
            service.append(
                LogEventType.PROPOSAL_CREATED,
                "准备移动 Research。",
                operation_id="operation-1",
                references=(TimelineReference("time_range", start=100, end=200),),
                entry_id="second",
                occurred_at=datetime(2026, 8, 13, 10, 1, tzinfo=UTC),
            )

            stored = service.list()

            self.assertEqual([item.id for item in stored], ["second", "first"])
            self.assertEqual(stored[1].references[0].id, "research")
            self.assertEqual(log_entry_from_dict(log_entry_to_dict(first)), first)
            with self.assertRaisesRegex(ValueError, "already exists"):
                repository.append(first)

    def test_log_limit_is_bounded(self) -> None:
        with TemporaryDirectory() as temporary:
            service = ChronosLogService(
                SQLiteChronosLogRepository(Path(temporary) / "chronos.sqlite3")
            )
            with self.assertRaisesRegex(ValueError, "between 1 and 1000"):
                service.list(0)

    def test_legacy_proposal_history_is_imported_once(self) -> None:
        with TemporaryDirectory() as temporary:
            service = ChronosLogService(
                SQLiteChronosLogRepository(Path(temporary) / "chronos.sqlite3")
            )
            proposal = {
                "proposal_id": "old-proposal",
                "status": "pending",
                "request_text": "安排阅读",
                "updated_at": "2026-08-13T10:00:00+00:00",
                "explanation": ["准备创建阅读任务。"],
                "proposed_task": {
                    "id": "reading",
                    "start": 1_800_000,
                    "end": 3_600_000,
                },
                "proposed_tasks": [],
                "reminder_drafts": [],
            }

            self.assertEqual(migrate_proposal_history([proposal], service), 1)
            self.assertEqual(migrate_proposal_history([proposal], service), 0)
            entries = service.list()
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].operation_id, "old-proposal")
            self.assertEqual(entries[0].event_type, LogEventType.PROPOSAL_CREATED)
            self.assertTrue(entries[0].metadata["legacy_import"])
