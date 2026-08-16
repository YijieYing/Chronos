"""Deterministic conversion from Plan to executable Operations."""

from __future__ import annotations

from chronos.agent.models import (
    CreateReminderOperation,
    CreateTaskOperation,
    ReminderSpec,
    TaskSpec,
    TimelineOperation,
    TimeRange,
)
from chronos.agent.plan import Plan


class Lowerer:
    def lower(self, plan: Plan) -> tuple[TimelineOperation, ...]:
        if plan.conflicts:
            raise ValueError("cannot lower a Plan with conflicts")
        operations: list[TimelineOperation] = []
        for change in plan.changes:
            if change.reminder is not None:
                reminder = change.reminder
                operations.append(CreateReminderOperation(
                    reminder.id,
                    ReminderSpec(
                        title=reminder.title,
                        trigger_type=reminder.trigger,
                        at=reminder.at,
                        window=(
                            TimeRange(reminder.window.start, reminder.window.end)
                            if reminder.window is not None
                            else None
                        ),
                        delivery=reminder.delivery,
                        priority=reminder.priority,
                    ),
                ))
                continue
            if change.task is None:
                raise ValueError("Lowerer add change requires a task or reminder draft")
            task = change.task
            operations.append(CreateTaskOperation(
                task.id,
                TaskSpec(
                    title=task.title,
                    start=task.start,
                    duration_minutes=task.duration,
                    fixed=task.fixed,
                    window=(
                        TimeRange(task.window.start, task.window.end)
                        if task.window is not None
                        else None
                    ),
                ),
            ))
        return tuple(operations)
