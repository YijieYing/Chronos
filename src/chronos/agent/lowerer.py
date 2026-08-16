"""Deterministic conversion from Plan to executable Operations."""

from __future__ import annotations

from chronos.agent.models import CreateTaskOperation, TaskSpec, TimelineOperation, TimeRange
from chronos.agent.plan import Plan


class Lowerer:
    def lower(self, plan: Plan) -> tuple[TimelineOperation, ...]:
        if plan.conflicts:
            raise ValueError("cannot lower a Plan with conflicts")
        operations: list[TimelineOperation] = []
        for change in plan.changes:
            if change.task is None:
                raise ValueError("first Lowerer slice supports only task creation")
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
