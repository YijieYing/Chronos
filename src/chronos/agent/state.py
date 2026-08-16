"""Minimal changing state consumed by Planner."""

from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True)
class State:
    now: int
    timezone: str

    def __post_init__(self) -> None:
        if self.now <= 0:
            raise ValueError("State now must be positive")
        ZoneInfo(self.timezone)
