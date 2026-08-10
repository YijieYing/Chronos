from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from chronos.infrastructure.sqlite_timeline import SQLiteTimelineRepository


class TimelineRepositoryTest(TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.repository = SQLiteTimelineRepository(
            Path(self.temporary.name) / "chronos.sqlite3"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_recurring_series_is_stored_as_one_row(self) -> None:
        task = self.repository.create_task(
            _task(
                recurrence={"frequency": "weekly", "weekdays": [5, 1, 3, 1]}
            )
        )

        stored = self.repository.list_tasks()

        self.assertEqual(len(stored), 1)
        self.assertEqual(task["recurrence"], {"frequency": "weekly", "weekdays": [1, 3, 5]})
        self.assertEqual(stored[0]["recurrence"], task["recurrence"])

    def test_save_updates_series_without_changing_creation_time(self) -> None:
        original = self.repository.create_task(_task())
        changed = {
            **original,
            "title": "Updated daily task",
            "start": original["start"] + 3_600_000,
            "end": original["end"] + 3_600_000,
            "predicted_end": original["predicted_end"] + 3_600_000,
            "updated_at": original["updated_at"] + 10_000,
        }

        saved = self.repository.save_task(changed)

        self.assertEqual(saved["created_at"], original["created_at"])
        self.assertEqual(self.repository.get_task("timeline-1"), saved)

    def test_delete_removes_entire_series(self) -> None:
        self.repository.create_task(_task())

        self.assertTrue(self.repository.delete_task("timeline-1"))
        self.assertEqual(self.repository.list_tasks(), [])
        self.assertFalse(self.repository.delete_task("timeline-1"))

    def test_rejects_invalid_weekdays(self) -> None:
        with self.assertRaisesRegex(ValueError, "weekdays"):
            self.repository.create_task(
                _task(recurrence={"frequency": "weekly", "weekdays": [7]})
            )


def _task(**changes: object) -> dict[str, object]:
    task: dict[str, object] = {
        "id": "timeline-1",
        "title": "Daily research",
        "start": 1_753_920_000_000,
        "end": 1_753_923_600_000,
        "predicted_end": 1_753_924_200_000,
        "intensity": 0.72,
        "spectrum": 0.25,
        "fixed": False,
        "task_type": "research",
        "source": "user",
        "recurrence": {"frequency": "daily"},
        "created_at": 1_753_900_000_000,
        "updated_at": 1_753_900_000_000,
    }
    task.update(changes)
    return task
