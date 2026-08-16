from datetime import date, time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from chronos.infrastructure.sqlite_schedule import SQLiteScheduleRepository
from chronos.schedule.models import AgendaStatus, TaskStatus
from chronos.schedule.service import ScheduleService


class ScheduleServiceTest(TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        repository = SQLiteScheduleRepository(Path(self.temporary.name) / "chronos.sqlite3")
        self.service = ScheduleService(repository)
        self.day = date(2026, 7, 20)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_generate_activate_and_version_plan(self) -> None:
        task = self.service.create_task(
            title="Build Schedule prototype",
            estimated_minutes=120,
            priority=5,
        )
        self.service.create_fixed_block(
            title="Lunch",
            target_date=self.day,
            start_time=time(12),
            end_time=time(13),
        )

        first = self.service.generate_agenda(self.day)
        active = self.service.activate_agenda(first.agenda_id)
        second = self.service.generate_agenda(self.day)
        snapshot = self.service.snapshot(self.day)

        self.assertEqual(active.status, AgendaStatus.ACTIVE)
        self.assertEqual(second.version, 2)
        self.assertEqual(second.based_on_version, 1)
        self.assertEqual(snapshot["type"], "chronos.schedule_snapshot")
        task_data = next(item for item in snapshot["tasks"] if item["task_id"] == task.task_id)
        self.assertEqual(task_data["status"], TaskStatus.PLANNED.value)

    def test_invalid_timezone_does_not_persist(self) -> None:
        before = self.service.settings()

        with self.assertRaises(ValueError):
            self.service.update_settings({"timezone": "Not/A-Timezone"})

        self.assertEqual(self.service.settings(), before)

    def test_planned_backlog_task_does_not_repeat_on_another_day(self) -> None:
        task = self.service.create_task(
            title="One-time backlog task",
            estimated_minutes=60,
            priority=3,
        )
        first = self.service.generate_agenda(self.day)
        self.service.activate_agenda(first.agenda_id)

        following = self.service.generate_agenda(date(2026, 7, 21))

        self.assertFalse(any(block.task_id == task.task_id for block in following.blocks))
