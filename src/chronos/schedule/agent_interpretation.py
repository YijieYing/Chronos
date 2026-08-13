"""Typed, source-grounded interpretation before Schedule commands are planned."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from chronos.schedule.commands import ScheduleCommand
from chronos.schedule.models import Task

InterpretationIntent = Literal[
    "create_schedule", "create_reminder", "replan_schedule", "single_command"
]


@dataclass(frozen=True, slots=True)
class UnresolvedField:
    field: str
    question: str


@dataclass(frozen=True, slots=True)
class InterpretedTask:
    title: str
    title_source: str
    duration_minutes: int | None
    duration_source: str | None
    preferred_start: datetime | None
    temporal_source: str | None
    task_type: str
    recurrence: dict[str, object] | None
    recurrence_sources: dict[str, tuple[str, ...]]
    fixed: bool


@dataclass(frozen=True, slots=True)
class InterpretedReminder:
    title: str
    title_source: str
    trigger_type: Literal["time", "window"]
    trigger_at: datetime | None
    window_start: datetime | None
    window_end: datetime | None
    temporal_sources: tuple[str, ...]
    delivery: Literal["exact", "context-aware"]
    delivery_sources: tuple[str, ...] = ()
    priority: int = 3


@dataclass(frozen=True, slots=True)
class AgentInterpretation:
    intent: InterpretationIntent
    tasks: tuple[InterpretedTask, ...]
    reminders: tuple[InterpretedReminder, ...] = ()
    unresolved: tuple[UnresolvedField, ...] = ()
    assumptions: tuple[str, ...] = ()
    context_used: tuple[dict[str, object], ...] = ()
    mode: str = "semantic"
    command: ScheduleCommand | None = None


class AgentInterpreter(Protocol):
    def interpret(self, text: str, now: datetime, tasks: list[Task]) -> AgentInterpretation: ...
