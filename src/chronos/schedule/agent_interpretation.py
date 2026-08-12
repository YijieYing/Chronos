"""Typed, source-grounded interpretation before Schedule commands are planned."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from chronos.schedule.commands import ScheduleCommand
from chronos.schedule.models import Task

InterpretationIntent = Literal["create_schedule", "single_command"]


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
    recurrence_source: str | None
    fixed: bool


@dataclass(frozen=True, slots=True)
class AgentInterpretation:
    intent: InterpretationIntent
    tasks: tuple[InterpretedTask, ...]
    unresolved: tuple[UnresolvedField, ...] = ()
    assumptions: tuple[str, ...] = ()
    context_used: tuple[dict[str, object], ...] = ()
    mode: str = "semantic"
    command: ScheduleCommand | None = None


class AgentInterpreter(Protocol):
    def interpret(self, text: str, now: datetime, tasks: list[Task]) -> AgentInterpretation: ...
