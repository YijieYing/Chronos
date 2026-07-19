"""Bounded in-memory observation ingestion with explicit drop accounting."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import Lock

from chronos.models import Observation


@dataclass(frozen=True, slots=True)
class IngestResult:
    accepted: bool
    duplicate: bool = False
    evicted_observation: Observation | None = None


class ObservationManager:
    """Owns a strictly bounded queue; memory use cannot grow with event volume."""

    def __init__(self, capacity: int = 1_000) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._capacity = capacity
        self._items: deque[Observation] = deque()
        self._ids: set[object] = set()
        self._lock = Lock()
        self._evicted_count = 0
        self._duplicate_count = 0

    def ingest(self, observation: Observation) -> IngestResult:
        with self._lock:
            if observation.observation_id in self._ids:
                self._duplicate_count += 1
                return IngestResult(accepted=False, duplicate=True)

            evicted = None
            if len(self._items) == self._capacity:
                evicted = self._items.popleft()
                self._ids.remove(evicted.observation_id)
                self._evicted_count += 1

            self._items.append(observation)
            self._ids.add(observation.observation_id)
            return IngestResult(accepted=True, evicted_observation=evicted)

    def drain(self, limit: int | None = None) -> list[Observation]:
        with self._lock:
            count = len(self._items) if limit is None else min(max(limit, 0), len(self._items))
            drained = [self._items.popleft() for _ in range(count)]
            self._ids.difference_update(item.observation_id for item in drained)
            return drained

    @property
    def depth(self) -> int:
        with self._lock:
            return len(self._items)

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def evicted_count(self) -> int:
        with self._lock:
            return self._evicted_count

    @property
    def duplicate_count(self) -> int:
        with self._lock:
            return self._duplicate_count

