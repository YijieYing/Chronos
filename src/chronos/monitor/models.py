"""Monitor data contracts shared by collectors and the estimation pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping
from uuid import UUID, uuid4


class ObservationKind(StrEnum):
    COLLECTOR_STATUS = "collector.status"
    INPUT_ACTIVITY = "input.activity"
    FOREGROUND_CHANGED = "foreground.changed"
    DEVICE_PRESENCE = "device.presence"
    SCREEN_STATE = "screen.state"
    LOCATION_EVIDENCE = "location.evidence"


class Presence(StrEnum):
    ACTIVE = "active"
    PASSIVE = "passive"
    IDLE = "idle"
    AWAY = "away"
    UNKNOWN = "unknown"


class Activity(StrEnum):
    CODING = "coding"
    WRITING = "writing"
    RESEARCHING = "researching"
    COMMUNICATING = "communicating"
    MEETING = "meeting"
    PLANNING = "planning"
    ENTERTAINMENT = "entertainment"
    UNKNOWN = "unknown"


class SegmentStatus(StrEnum):
    OPEN = "open"
    FINALIZED = "finalized"


@dataclass(frozen=True, slots=True)
class Observation:
    """A timestamped piece of evidence, not an interpretation of user behavior."""

    device_id: str
    kind: ObservationKind
    observed_at: datetime
    payload: Mapping[str, object] = field(default_factory=dict)
    confidence: float = 1.0
    observation_id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if not self.device_id:
            raise ValueError("device_id must not be empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True, slots=True)
class AppContext:
    app_id: str
    app_name: str
    window_title: str | None = None


@dataclass(frozen=True, slots=True)
class FeatureWindow:
    """Deterministic aggregate of observations over a bounded time window."""

    device_id: str
    start_at: datetime
    end_at: datetime
    key_count: int = 0
    click_count: int = 0
    pointer_distance: float = 0.0
    scroll_distance: float = 0.0
    active_seconds: float = 0.0
    context_switches: int = 0
    app_seconds: Mapping[str, float] = field(default_factory=dict)
    latest_context: AppContext | None = None
    screen_state: str | None = None
    device_state: str | None = None
    observation_count: int = 0

    def __post_init__(self) -> None:
        if self.end_at <= self.start_at:
            raise ValueError("feature window must have positive duration")
        object.__setattr__(self, "app_seconds", MappingProxyType(dict(self.app_seconds)))


@dataclass(frozen=True, slots=True)
class WorkStateEstimate:
    """A mutable-in-time hypothesis about the user's current work state."""

    device_id: str
    window_start: datetime
    window_end: datetime
    evaluated_at: datetime
    presence: Presence
    activity: Activity
    confidence: float
    focus_level: float
    possible_task_id: str | None = None
    evidence_ids: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if not 0.0 <= self.focus_level <= 1.0:
            raise ValueError("focus_level must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class ActivitySegment:
    """A stabilized interval suitable for durable history and Daytrace export."""

    device_id: str
    start_at: datetime
    end_at: datetime
    activity: Activity
    confidence: float
    status: SegmentStatus
    task_id: str | None = None
    estimate_count: int = 1
