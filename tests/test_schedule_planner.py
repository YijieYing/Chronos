from datetime import UTC, date, datetime, timedelta
from unittest import TestCase

from chronos.schedule.constraints import validate_plan
from chronos.schedule.models import AvailabilityWindow, FixedBlock, Task, TaskStatus
from chronos.schedule.planner import DailyPlanner


class DailyPlannerTest(TestCase):
    def setUp(self) -> None:
        self.day = date(2026, 7, 20)
        self.start = datetime(2026, 7, 20, 9, 0, tzinfo=UTC)
        self.availability = AvailabilityWindow(
            start_at=self.start,
            end_at=self.start + timedelta(hours=8),
        )
    def test_priority_and_fixed_blocks_determine_placement(self) -> None:
        tasks = [
            self._task("low", "Low priority", 60, priority=2),
            self._task("high", "High priority", 120, priority=5),
        ]
        lunch = FixedBlock(
            block_id="lunch",
            title="Lunch",
            start_at=self.start + timedelta(hours=3),
            end_at=self.start + timedelta(hours=4),
        )

        plan = DailyPlanner().generate(
            tasks=tasks,
            fixed_blocks=[lunch],
            availability=self.availability,
            target_date=self.day,
            timezone="UTC",
            version=1,
        )

        self.assertEqual(plan.blocks[0].task_id, "high")
        self.assertEqual(plan.blocks[0].start_at, self.start)
        self.assertFalse(plan.unscheduled)
        validate_plan(plan.blocks, self.availability, [lunch])

    def test_splittable_task_uses_windows_around_fixed_block(self) -> None:
        task = self._task("deep", "Deep work", 180, priority=5, min_chunk=30)
        meeting = FixedBlock(
            block_id="meeting",
            title="Meeting",
            start_at=self.start + timedelta(hours=1),
            end_at=self.start + timedelta(hours=2),
        )

        plan = DailyPlanner().generate(
            tasks=[task],
            fixed_blocks=[meeting],
            availability=self.availability,
            target_date=self.day,
            timezone="UTC",
            version=1,
        )

        self.assertEqual(len(plan.blocks), 2)
        self.assertEqual(sum(block.duration_minutes for block in plan.blocks), 180)
        self.assertEqual(plan.blocks[0].end_at, meeting.start_at)
        self.assertEqual(plan.blocks[1].start_at, meeting.end_at)

    def test_unscheduled_remainder_is_explicit(self) -> None:
        task = self._task("large", "Large task", 600, priority=5)

        plan = DailyPlanner().generate(
            tasks=[task],
            fixed_blocks=[],
            availability=self.availability,
            target_date=self.day,
            timezone="UTC",
            version=1,
        )

        self.assertEqual(plan.unscheduled[0].remaining_minutes, 120)

    def _task(
        self,
        task_id: str,
        title: str,
        minutes: int,
        *,
        priority: int,
        min_chunk: int = 25,
    ) -> Task:
        return Task(
            task_id=task_id,
            title=title,
            estimated_minutes=minutes,
            priority=priority,
            status=TaskStatus.BACKLOG,
            created_at=self.start,
            splittable=True,
            min_chunk_minutes=min_chunk,
        )
