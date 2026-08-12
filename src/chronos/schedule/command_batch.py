"""Typed command batch passed from Agent interpretation to Schedule planning."""

from __future__ import annotations

from dataclasses import dataclass

from chronos.schedule.models import Task


@dataclass(frozen=True, slots=True)
class ScheduleCreateCommand:
    task: Task
    title_source: str
    duration_source: str | None
    temporal_source: str | None
    recurrence_sources: dict[str, tuple[str, ...]]

    def to_dict(self) -> dict[str, object]:
        from chronos.schedule.service import _task_dict

        return {
            "type": "create_task",
            "task_id": self.task.task_id,
            "after": _task_dict(self.task),
            "provenance": {
                "title": self.title_source,
                "duration": self.duration_source,
                "time": self.temporal_source,
                "recurrence": {
                    field: list(sources)
                    for field, sources in self.recurrence_sources.items()
                },
            },
        }


@dataclass(frozen=True, slots=True)
class ScheduleCommandBatch:
    commands: tuple[ScheduleCreateCommand, ...]
    horizon_days: int = 14

    def __post_init__(self) -> None:
        if not self.commands:
            raise ValueError("schedule command batch cannot be empty")
        if not 1 <= self.horizon_days <= 90:
            raise ValueError("schedule command batch horizon must be between 1 and 90 days")

    @property
    def tasks(self) -> list[Task]:
        return [command.task for command in self.commands]

    def to_dicts(self) -> list[dict[str, object]]:
        return [command.to_dict() for command in self.commands]
