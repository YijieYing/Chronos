"""A deterministic, explainable daily planner for the first Schedule prototype."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from chronos.schedule.constraints import calculate_free_windows, validate_plan
from chronos.schedule.models import (
    AvailabilityWindow,
    FixedBlock,
    Plan,
    PlanStatus,
    ScheduleBlock,
    Task,
    TaskStatus,
    UnscheduledTask,
)


class DailyPlanner:
    def generate(
        self,
        *,
        tasks: list[Task],
        fixed_blocks: list[FixedBlock],
        availability: AvailabilityWindow,
        target_date: date,
        timezone: str,
        version: int,
        based_on_version: int | None = None,
    ) -> Plan:
        candidates = [
            occurrence
            for task in tasks
            if task.status not in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}
            if (occurrence := _occurrence_for_date(task, target_date)) is not None
        ]
        candidates.sort(key=_task_sort_key)
        fixed_tasks = [task for task in candidates if task.fixed]
        flexible_tasks = [task for task in candidates if not task.fixed]
        fixed_task_blocks = [_fixed_task_block(task) for task in fixed_tasks]
        task_constraints = [
            FixedBlock(
                block_id=f"task:{block.block_id}",
                title=block.title,
                start_at=block.start_at,
                end_at=block.end_at,
                source="schedule-task",
            )
            for block in fixed_task_blocks
        ]
        free_windows = calculate_free_windows(
            availability, [*fixed_blocks, *task_constraints]
        )
        blocks: list[ScheduleBlock] = list(fixed_task_blocks)
        unscheduled: list[UnscheduledTask] = []

        for task in flexible_tasks:
            remaining = task.estimated_minutes
            if task.splittable:
                remaining = self._place_splittable(task, remaining, free_windows, blocks)
            else:
                remaining = self._place_whole(task, remaining, free_windows, blocks)
            if remaining:
                unscheduled.append(
                    UnscheduledTask(
                        task_id=task.task_id,
                        title=task.title,
                        remaining_minutes=remaining,
                        reason="insufficient_available_time",
                    )
                )

        result = Plan(
            plan_id=str(uuid4()),
            version=version,
            target_date=target_date,
            timezone=timezone,
            status=PlanStatus.DRAFT,
            created_at=datetime.now(UTC),
            blocks=tuple(sorted(blocks, key=lambda item: item.start_at)),
            unscheduled=tuple(unscheduled),
            based_on_version=based_on_version,
        )
        validate_plan(result.blocks, availability, fixed_blocks)
        return result

    @staticmethod
    def _place_splittable(
        task: Task,
        remaining: int,
        free_windows: list[tuple[datetime, datetime]],
        blocks: list[ScheduleBlock],
    ) -> int:
        index = 0
        while index < len(free_windows):
            window_start, end_at = free_windows[index]
            start_at = max(window_start, task.preferred_start or window_start)
            available = int((end_at - start_at).total_seconds() // 60)
            if available <= 0:
                index += 1
                continue
            allocated = min(remaining, available)
            if allocated < task.min_chunk_minutes and allocated != remaining:
                index += 1
                continue
            block_end = start_at + timedelta(minutes=allocated)
            blocks.append(_block(task, start_at, block_end))
            _consume_window(free_windows, index, start_at, block_end)
            remaining -= allocated
            if remaining == 0:
                break
            index = 0
        return remaining

    @staticmethod
    def _place_whole(
        task: Task,
        remaining: int,
        free_windows: list[tuple[datetime, datetime]],
        blocks: list[ScheduleBlock],
    ) -> int:
        for index, (window_start, end_at) in enumerate(free_windows):
            start_at = max(window_start, task.preferred_start or window_start)
            available = int((end_at - start_at).total_seconds() // 60)
            if available < remaining:
                continue
            block_end = start_at + timedelta(minutes=remaining)
            blocks.append(_block(task, start_at, block_end))
            _consume_window(free_windows, index, start_at, block_end)
            return 0
        return remaining


def _task_sort_key(task: Task) -> tuple[int, datetime, datetime]:
    deadline = task.deadline or datetime.max.replace(tzinfo=UTC)
    return (-task.priority, deadline, task.created_at)


def _block(task: Task, start_at: datetime, end_at: datetime) -> ScheduleBlock:
    return ScheduleBlock(
        block_id=str(uuid4()),
        task_id=task.task_id,
        title=task.title,
        start_at=start_at,
        end_at=end_at,
    )


def _fixed_task_block(task: Task) -> ScheduleBlock:
    assert task.preferred_start is not None
    return ScheduleBlock(
        block_id=str(uuid4()),
        task_id=task.task_id,
        title=task.title,
        start_at=task.preferred_start,
        end_at=task.preferred_start + timedelta(minutes=task.estimated_minutes),
        flexibility="fixed",
    )


def _occurrence_for_date(task: Task, target_date: date) -> Task | None:
    if task.preferred_start is None:
        return task
    base = task.preferred_start
    if task.recurrence is None:
        return task if base.date() == target_date else None
    if target_date < base.date():
        return None
    frequency = task.recurrence["frequency"]
    if frequency == "weekly" and target_date.weekday() not in {
        _python_weekday(int(day)) for day in task.recurrence.get("weekdays", [])
    }:
        return None
    occurrence = base.replace(
        year=target_date.year,
        month=target_date.month,
        day=target_date.day,
    )
    return replace(task, preferred_start=occurrence)


def _python_weekday(javascript_weekday: int) -> int:
    return (javascript_weekday - 1) % 7


def _consume_window(
    windows: list[tuple[datetime, datetime]],
    index: int,
    used_start: datetime,
    used_end: datetime,
) -> None:
    start_at, end_at = windows[index]
    remaining: list[tuple[datetime, datetime]] = []
    if start_at < used_start:
        remaining.append((start_at, used_start))
    if used_end < end_at:
        remaining.append((used_end, end_at))
    windows[index:index + 1] = remaining
