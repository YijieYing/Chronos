"""Deterministic conversion from Plan to executable Operations."""

from __future__ import annotations

from chronos.agent.models import (
    AdjustmentPolicy,
    CreateReminderOperation,
    CreateTaskOperation,
    DeleteReminderOperation,
    DeleteTaskOperation,
    MoveReminderOperation,
    MoveTaskOperation,
    RecurrenceSpec,
    ReminderSpec,
    ResizeTaskOperation,
    TaskSpec,
    TimelineOperation,
    TimeRange,
    UpdateTaskOperation,
    UpdateReminderOperation,
)
from chronos.agent.meaning import Recurrence, RequestKind
from chronos.agent.plan import Plan


class Lowerer:
    def lower(self, plan: Plan) -> tuple[TimelineOperation, ...]:
        if plan.conflicts:
            raise ValueError("cannot lower a Plan with conflicts")
        operations: list[TimelineOperation] = []
        for change in plan.changes:
            if change.request == RequestKind.DELETE:
                if change.target_type == "task":
                    operations.append(DeleteTaskOperation(change.target_id or ""))
                else:
                    operations.append(DeleteReminderOperation(change.target_id or ""))
                continue
            if change.request == RequestKind.EDIT:
                if change.target_type == "task":
                    if change.task is not None:
                        task = change.task
                        operations.append(UpdateTaskOperation(
                            change.target_id or "",
                            TaskSpec(
                                title=task.title,
                                start=task.start,
                                duration_minutes=task.duration,
                                fixed=task.fixed,
                                recurrence=_recurrence(task.recurrence),
                                adjustment_policy=AdjustmentPolicy(priority=task.priority),
                            ),
                        ))
                        continue
                    if change.at is not None:
                        operations.append(MoveTaskOperation(change.target_id or "", change.at))
                    if change.duration is not None:
                        operations.append(
                            ResizeTaskOperation(change.target_id or "", change.duration)
                        )
                elif change.reminder is not None:
                    reminder = change.reminder
                    operations.append(UpdateReminderOperation(
                        change.target_id or "",
                        ReminderSpec(
                            title=reminder.title,
                            trigger_type=reminder.trigger,
                            at=reminder.at,
                            window=(
                                TimeRange(reminder.window.start, reminder.window.end)
                                if reminder.window is not None else None
                            ),
                            delivery=reminder.delivery,
                            priority=reminder.priority,
                        ),
                    ))
                elif change.at is not None:
                    operations.append(
                        MoveReminderOperation(change.target_id or "", at=change.at)
                    )
                else:
                    assert change.window is not None
                    operations.append(MoveReminderOperation(
                        change.target_id or "",
                        window=TimeRange(change.window.start, change.window.end),
                    ))
                continue
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
                    recurrence=_recurrence(task.recurrence),
                    window=(
                        TimeRange(task.window.start, task.window.end)
                        if task.window is not None
                        else None
                    ),
                ),
            ))
        return tuple(operations)


def _recurrence(value: Recurrence | None) -> RecurrenceSpec | None:
    if value is None:
        return None
    return RecurrenceSpec(value.frequency, value.weekdays, value.until)
