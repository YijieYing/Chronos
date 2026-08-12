from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from chronos.infrastructure.sqlite_reminders import SQLiteReminderRepository
from chronos.reminders.models import ReminderStatus
from chronos.reminders.service import ReminderService


class ReminderServiceTest(TestCase):
    def test_point_and_window_reminders_are_independent_objects(self) -> None:
        with TemporaryDirectory() as temporary:
            service = ReminderService(
                SQLiteReminderRepository(Path(temporary) / "chronos.sqlite3")
            )
            point = service.create(
                title="取快递",
                trigger_type="time",
                trigger_at=datetime(2026, 8, 13, 15, 20, tzinfo=UTC),
            )
            window = service.create(
                title="回复邮件",
                trigger_type="window",
                window_start=datetime(2026, 8, 13, 14, 0, tzinfo=UTC),
                window_end=datetime(2026, 8, 13, 18, 0, tzinfo=UTC),
                delivery="context-aware",
            )

            items = service.list()

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["trigger"]["type"], "window")
        self.assertEqual(items[1]["trigger"]["type"], "time")
        self.assertEqual(window.status, ReminderStatus.PENDING)
        self.assertNotEqual(point.reminder_id, window.reminder_id)

    def test_invalid_trigger_shapes_are_rejected(self) -> None:
        with TemporaryDirectory() as temporary:
            service = ReminderService(
                SQLiteReminderRepository(Path(temporary) / "nested" / "chronos.sqlite3")
            )
            with self.assertRaisesRegex(ValueError, "cannot contain a window"):
                service.create(
                    title="invalid point",
                    trigger_type="time",
                    trigger_at=datetime(2026, 8, 13, 15, 20, tzinfo=UTC),
                    window_start=datetime(2026, 8, 13, 14, 0, tzinfo=UTC),
                )
            with self.assertRaisesRegex(ValueError, "must be exact"):
                service.create(
                    title="invalid point delivery",
                    trigger_type="time",
                    trigger_at=datetime(2026, 8, 13, 15, 20, tzinfo=UTC),
                    delivery="context-aware",
                )
