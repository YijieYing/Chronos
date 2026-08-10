"""Application service joining Observation recognition and cognitive-state persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from threading import Lock

from chronos.infrastructure.sqlite_cognitive_state import SQLiteCognitiveStateRepository
from chronos.monitor.cognitive import (
    CognitiveStateEstimator,
    CognitiveStatePoint,
    cognitive_point_dict,
)
from chronos.monitor.live import LiveRecognizer
from chronos.monitor.models import Observation


class MonitorService:
    def __init__(self, repository: SQLiteCognitiveStateRepository) -> None:
        self._repository = repository
        previous = repository.latest()
        self._estimator = CognitiveStateEstimator(previous)
        self._recognizer = LiveRecognizer()
        self._lock = Lock()
        self._last_ingested_at: datetime | None = None

    def ingest(self, observation: Observation) -> CognitiveStatePoint | None:
        with self._lock:
            estimate = self._recognizer.ingest(observation)
            if estimate is None:
                return None
            point = self._estimator.ingest(estimate)
            if self._estimator.finalized_point is not None:
                self._repository.upsert(self._estimator.finalized_point)
            self._repository.upsert(point)
            self._repository.prune(datetime.now(UTC))
            self._last_ingested_at = datetime.now(UTC)
            return point

    def current(self, now: datetime | None = None) -> dict[str, object]:
        now = now or datetime.now(UTC)
        point = self._repository.latest()
        if point is None:
            return {"status": "offline", "generated_at": int(now.timestamp() * 1000), "point": None}
        stale_seconds = (
            max(0.0, (now - self._last_ingested_at).total_seconds())
            if self._last_ingested_at
            else None
        )
        return {
            "status": "live" if stale_seconds is not None and stale_seconds <= 30 else "offline",
            "generated_at": int(now.timestamp() * 1000),
            "stale_seconds": round(stale_seconds, 1) if stale_seconds is not None else None,
            "point": cognitive_point_dict(point),
        }

    def history(self, start: datetime, end: datetime) -> dict[str, object]:
        points = self._repository.between(start, end)
        return {
            "from": int(start.timestamp() * 1000),
            "to": int(end.timestamp() * 1000),
            "points": [cognitive_point_dict(point) for point in points],
        }
