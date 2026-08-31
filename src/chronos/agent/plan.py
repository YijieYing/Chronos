"""The single resolved planning truth between Events and Operations."""

from __future__ import annotations

from dataclasses import dataclass

from chronos.agent.meaning import Recurrence, RequestKind


@dataclass(frozen=True, slots=True)
class Window:
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start <= 0 or self.end <= self.start:
            raise ValueError("window requires an ordered positive range")


@dataclass(frozen=True, slots=True)
class Horizon:
    start: int
    end: int
    mode: str = "prospective"

    def __post_init__(self) -> None:
        if self.start <= 0 or self.end <= self.start:
            raise ValueError("horizon requires an ordered positive range")
        if self.mode not in {"prospective", "historical"}:
            raise ValueError("horizon mode must be prospective or historical")


@dataclass(frozen=True, slots=True)
class TaskDraft:
    id: str
    title: str
    start: int
    duration: int
    window: Window | None = None
    fixed: bool = False
    priority: int = 3
    recurrence: Recurrence | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.title.strip() or self.start <= 0 or self.duration <= 0:
            raise ValueError("task draft requires id, title, start, and duration")
        if not 1 <= self.priority <= 5:
            raise ValueError("task priority must be between 1 and 5")
        if self.window is not None and not self.window.start <= self.start < self.window.end:
            raise ValueError("task draft start must fall inside its window")
        end = self.start + self.duration * 60_000
        if self.window is not None and end > self.window.end:
            raise ValueError("task draft must finish inside its window")


@dataclass(frozen=True, slots=True)
class ReminderDraft:
    id: str
    title: str
    trigger: str
    at: int | None = None
    window: Window | None = None
    delivery: str = "exact"
    priority: int = 3

    def __post_init__(self) -> None:
        if not self.id or not self.title.strip() or not 1 <= self.priority <= 5:
            raise ValueError("reminder draft requires id, title, and priority")
        if self.trigger == "time":
            if self.at is None or self.window is not None or self.delivery != "exact":
                raise ValueError("point reminder requires exact time delivery")
        elif self.trigger == "window":
            if self.at is not None or self.window is None:
                raise ValueError("window reminder requires a window")
            if self.delivery not in {"exact", "context-aware"}:
                raise ValueError("unsupported reminder delivery")
        else:
            raise ValueError("reminder trigger must be time or window")


@dataclass(frozen=True, slots=True)
class Change:
    event_id: str
    request: RequestKind
    task: TaskDraft | None = None
    reminder: ReminderDraft | None = None
    target_id: str | None = None
    target_type: str | None = None
    at: int | None = None
    duration: int | None = None
    window: Window | None = None

    def __post_init__(self) -> None:
        if not self.event_id:
            raise ValueError("change requires a source Event")
        if self.request == RequestKind.ADD:
            if (
                (self.task is None) == (self.reminder is None)
                or self.target_id is not None
                or self.target_type is not None
                or self.at is not None
                or self.duration is not None
                or self.window is not None
            ):
                raise ValueError("add change requires exactly one draft")
            return
        if not self.target_id or self.target_type not in {"task", "reminder"}:
            raise ValueError("edit/delete change requires a typed target")
        if self.reminder is not None:
            if self.request != RequestKind.EDIT or self.target_type != "reminder":
                raise ValueError("only reminder edits can carry a reminder draft")
            if self.at is not None or self.duration is not None or self.window is not None:
                raise ValueError("reminder draft edits cannot carry primitive values")
            return
        if self.task is not None:
            if self.request != RequestKind.EDIT or self.target_type != "task":
                raise ValueError("only task edits can carry a task draft")
            if self.at is not None or self.duration is not None or self.window is not None:
                raise ValueError("task draft edits cannot carry primitive values")
            return
        if self.request == RequestKind.DELETE:
            if self.at is not None or self.duration is not None or self.window is not None:
                raise ValueError("delete change cannot carry edited values")
            return
        if self.target_type == "task":
            if self.window is not None or (self.at is None and self.duration is None):
                raise ValueError("task edit requires time or duration")
        elif self.duration is not None or (self.at is None) == (self.window is None):
            raise ValueError("reminder edit requires exactly one trigger")
        if self.duration is not None and self.duration <= 0:
            raise ValueError("edited duration must be positive")


@dataclass(frozen=True, slots=True)
class Assumption:
    event_id: str
    text: str

    def __post_init__(self) -> None:
        if not self.event_id or not self.text.strip():
            raise ValueError("assumption requires an Event and explanation")


@dataclass(frozen=True, slots=True)
class Conflict:
    event_id: str
    code: str
    message: str

    def __post_init__(self) -> None:
        if not self.event_id or not self.code or not self.message.strip():
            raise ValueError("conflict requires an Event, code, and message")


@dataclass(frozen=True, slots=True)
class Plan:
    id: str
    snapshot_id: str
    snapshot_version: int
    horizon: Horizon
    changes: tuple[Change, ...]
    conflicts: tuple[Conflict, ...] = ()
    assumptions: tuple[Assumption, ...] = ()
    explanation: str = ""

    def __post_init__(self) -> None:
        if not self.id or not self.snapshot_id or self.snapshot_version <= 0:
            raise ValueError("Plan requires ids and a positive Snapshot version")
        if not self.changes and not self.conflicts:
            raise ValueError("Plan requires a change or an explicit conflict")
        event_ids = [item.event_id for item in self.changes]
        if len(set(event_ids)) != len(event_ids):
            raise ValueError("Plan can contain only one resolved change per Event")
