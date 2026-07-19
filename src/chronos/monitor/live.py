"""Small in-process loop that turns a live observation stream into current state."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta

from chronos.monitor.aggregation import FeatureAggregator
from chronos.monitor.estimator import RuleBasedStateEstimator
from chronos.monitor.models import AppContext, Observation, ObservationKind, WorkStateEstimate


class LiveRecognizer:
    def __init__(
        self,
        *,
        window: timedelta = timedelta(seconds=30),
        capacity: int = 2_000,
        history_ttl: timedelta = timedelta(minutes=10),
    ) -> None:
        if window.total_seconds() <= 0:
            raise ValueError("window must be positive")
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if history_ttl.total_seconds() <= 0:
            raise ValueError("history_ttl must be positive")
        self._window = window
        self._capacity = capacity
        self._history_ttl = history_ttl
        self._history: deque[Observation] = deque(maxlen=capacity)
        self._latest_seen_at: datetime | None = None
        self._aggregator = FeatureAggregator()
        self._estimator = RuleBasedStateEstimator()

    def ingest(self, observation: Observation) -> WorkStateEstimate | None:
        self._history.append(observation)
        if self._latest_seen_at is None or observation.observed_at > self._latest_seen_at:
            self._latest_seen_at = observation.observed_at
        self._expire_history()
        if observation.kind not in {
            ObservationKind.INPUT_ACTIVITY,
            ObservationKind.SCREEN_STATE,
            ObservationKind.DEVICE_PRESENCE,
        }:
            return None

        end_at = observation.observed_at + timedelta(microseconds=1)
        start_at = end_at - self._window
        device_history = [
            item
            for item in self._history
            if item.device_id == observation.device_id and item.observed_at <= observation.observed_at
        ]
        initial_context = _context_before(device_history, start_at)
        initial_screen = _state_before(
            device_history, start_at, ObservationKind.SCREEN_STATE
        )
        initial_device = _state_before(
            device_history, start_at, ObservationKind.DEVICE_PRESENCE
        )
        features = self._aggregator.aggregate(
            device_history,
            device_id=observation.device_id,
            start_at=start_at,
            end_at=end_at,
            initial_context=initial_context,
            initial_screen_state=initial_screen,
            initial_device_state=initial_device,
        )
        return self._estimator.estimate(features)

    @property
    def history_depth(self) -> int:
        return len(self._history)

    @property
    def history_capacity(self) -> int:
        return self._capacity

    @property
    def history_ttl(self) -> timedelta:
        return self._history_ttl

    def _expire_history(self) -> None:
        if self._latest_seen_at is None:
            return
        cutoff = self._latest_seen_at - self._history_ttl
        if all(item.observed_at >= cutoff for item in self._history):
            return
        self._history = deque(
            (item for item in self._history if item.observed_at >= cutoff),
            maxlen=self._capacity,
        )


def _context_before(observations: list[Observation], before) -> AppContext | None:
    for item in reversed(observations):
        if item.observed_at >= before or item.kind != ObservationKind.FOREGROUND_CHANGED:
            continue
        return AppContext(
            app_id=str(item.payload.get("app_id", "")),
            app_name=str(item.payload.get("app_name", "")),
            window_title=_optional_string(item.payload.get("window_title")),
        )
    return None


def _state_before(
    observations: list[Observation], before, kind: ObservationKind
) -> str | None:
    for item in reversed(observations):
        if item.observed_at < before and item.kind == kind:
            return _optional_string(item.payload.get("state"))
    return None


def _optional_string(value: object) -> str | None:
    return None if value is None else str(value)
