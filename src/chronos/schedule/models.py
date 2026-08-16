"""Domain models owned exclusively by the Schedule bounded context."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class TaskStatus(StrEnum):
    BACKLOG = "backlog"
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class AgendaStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class BlockStatus(StrEnum):
    PLANNED = "planned"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class Task:
    task_id: str
    title: str
    estimated_minutes: int
    priority: int
    status: TaskStatus
    created_at: datetime
    deadline: datetime | None = None
    splittable: bool = True
    min_chunk_minutes: int = 25
    preferred_start: datetime | None = None
    cognitive_intensity: float = 0.5
    spectrum: float = 0.5
    task_type: str = "execution"
    fixed: bool = False
    source: str = "user"
    recurrence: dict[str, object] | None = None

    def __post_init__(self) -> None:
        if not self.task_id or not self.title.strip():
            raise ValueError("task_id and title are required")
        if self.estimated_minutes <= 0:
            raise ValueError("estimated_minutes must be positive")
        if not 1 <= self.priority <= 5:
            raise ValueError("priority must be between 1 and 5")
        if self.min_chunk_minutes <= 0:
            raise ValueError("min_chunk_minutes must be positive")
        if not 0 <= self.cognitive_intensity <= 1 or not 0 <= self.spectrum <= 1:
            raise ValueError("cognitive_intensity and spectrum must be between 0 and 1")
        if self.fixed and self.preferred_start is None:
            raise ValueError("fixed tasks require preferred_start")
        if self.recurrence is not None:
            frequency = self.recurrence.get("frequency")
            if frequency not in {"daily", "weekly"}:
                raise ValueError("recurrence frequency must be daily or weekly")
            if frequency == "weekly":
                weekdays = self.recurrence.get("weekdays")
                if not isinstance(weekdays, list) or not weekdays:
                    raise ValueError("weekly recurrence requires weekdays")
            until_value = self.recurrence.get("until")
            if until_value is not None:
                until = date.fromisoformat(str(until_value))
                if self.preferred_start is None:
                    raise ValueError("bounded recurrence requires preferred_start")
                if until < self.preferred_start.date():
                    raise ValueError("recurrence until cannot precede preferred_start")


@dataclass(frozen=True, slots=True)
class AvailabilityWindow:
    start_at: datetime
    end_at: datetime

    def __post_init__(self) -> None:
        if self.end_at <= self.start_at:
            raise ValueError("availability window must have positive duration")


@dataclass(frozen=True, slots=True)
class FixedBlock:
    block_id: str
    title: str
    start_at: datetime
    end_at: datetime
    source: str = "manual"

    def __post_init__(self) -> None:
        if self.end_at <= self.start_at:
            raise ValueError("fixed block must have positive duration")


@dataclass(frozen=True, slots=True)
class ScheduleBlock:
    block_id: str
    task_id: str
    title: str
    start_at: datetime
    end_at: datetime
    status: BlockStatus = BlockStatus.PLANNED
    flexibility: str = "flexible"

    def __post_init__(self) -> None:
        if self.end_at <= self.start_at:
            raise ValueError("schedule block must have positive duration")

    @property
    def duration_minutes(self) -> int:
        return int((self.end_at - self.start_at).total_seconds() // 60)


@dataclass(frozen=True, slots=True)
class UnscheduledTask:
    task_id: str
    title: str
    remaining_minutes: int
    reason: str


@dataclass(frozen=True, slots=True)
class Agenda:
    agenda_id: str
    version: int
    target_date: date
    timezone: str
    status: AgendaStatus
    created_at: datetime
    blocks: tuple[ScheduleBlock, ...]
    unscheduled: tuple[UnscheduledTask, ...] = ()
    based_on_version: int | None = None
