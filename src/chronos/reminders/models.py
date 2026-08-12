"""Domain models for temporal beacons that do not reserve schedule time."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ReminderStatus(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    DONE = "done"
    DISMISSED = "dismissed"


@dataclass(frozen=True, slots=True)
class Reminder:
    reminder_id: str
    title: str
    trigger_type: str
    trigger_at: datetime | None
    window_start: datetime | None
    window_end: datetime | None
    delivery: str
    priority: int
    status: ReminderStatus
    created_at: datetime
    source: str = "user"

    def __post_init__(self) -> None:
        if not self.reminder_id or not self.title.strip():
            raise ValueError("reminder id and title are required")
        if self.trigger_type not in {"time", "window"}:
            raise ValueError("reminder trigger must be time or window")
        if self.delivery not in {"exact", "context-aware"}:
            raise ValueError("reminder delivery must be exact or context-aware")
        if not 1 <= self.priority <= 5:
            raise ValueError("reminder priority must be between 1 and 5")
        if self.trigger_type == "time" and self.trigger_at is None:
            raise ValueError("point reminder requires trigger_at")
        if self.trigger_type == "time":
            if self.window_start is not None or self.window_end is not None:
                raise ValueError("point reminder cannot contain a window")
            if self.delivery != "exact":
                raise ValueError("point reminder delivery must be exact")
        if self.trigger_type == "window":
            if self.trigger_at is not None:
                raise ValueError("window reminder cannot contain trigger_at")
            if self.window_start is None or self.window_end is None:
                raise ValueError("window reminder requires start and end")
            if self.window_end <= self.window_start:
                raise ValueError("reminder window must have positive duration")
