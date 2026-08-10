"""Stateful five-minute cognitive-state estimation from WorkState estimates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from chronos.monitor.models import Activity, Presence, WorkStateEstimate

BUCKET = timedelta(minutes=5)
MODEL_VERSION = "cognitive-rules-v1"


class RecoveryState(StrEnum):
    WORKING = "working"
    RECOVERING = "recovering"
    RESTED = "rested"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class CognitiveStatePoint:
    device_id: str
    time: datetime
    cognitive_load: float
    mental_fatigue: float
    focus: float
    task_type: str | None
    task_confidence: float
    recovery_state: RecoveryState
    source: str = "observed"
    model_version: str = MODEL_VERSION
    revision: int = 1


class CognitiveStateEstimator:
    """Incrementally updates one provisional point per wall-clock five-minute bucket."""

    _INTENSITY = {
        Activity.CODING: 0.86,
        Activity.WRITING: 0.82,
        Activity.RESEARCHING: 0.7,
        Activity.PLANNING: 0.76,
        Activity.MEETING: 0.55,
        Activity.COMMUNICATING: 0.42,
        Activity.ENTERTAINMENT: 0.24,
        Activity.UNKNOWN: 0.25,
    }

    def __init__(self, previous: CognitiveStatePoint | None = None) -> None:
        self._fatigue = previous.mental_fatigue if previous else 0.12
        self._continuous_work_minutes = 0.0
        self._recovery_minutes = 0.0
        self._switching_pressure = 0.0
        self._bucket_start: datetime | None = None
        self._samples: list[WorkStateEstimate] = []
        self._activity_switches = 0
        self._last_activity: Activity | None = None
        self._revision = previous.revision if previous else 0
        self.finalized_point: CognitiveStatePoint | None = None

    def ingest(self, estimate: WorkStateEstimate) -> CognitiveStatePoint:
        self.finalized_point = None
        observed_at = estimate.window_end.astimezone(UTC)
        bucket_start = _bucket_start(observed_at)
        if self._bucket_start is None or bucket_start > self._bucket_start:
            if self._samples:
                finalized = self._calculate(1.0)
                self._commit(finalized)
                self.finalized_point = finalized
            self._bucket_start = bucket_start
            self._samples = []
            self._activity_switches = 0
            self._revision = 0
        elif bucket_start < self._bucket_start:
            return self._calculate(1.0)

        if (
            estimate.activity != Activity.UNKNOWN
            and self._last_activity not in {None, Activity.UNKNOWN}
            and estimate.activity != self._last_activity
        ):
            self._activity_switches += 1
        if estimate.activity != Activity.UNKNOWN:
            self._last_activity = estimate.activity
        self._samples.append(estimate)
        self._samples = self._samples[-120:]
        self._revision += 1
        elapsed = max(
            0.05,
            min(1.0, (observed_at - bucket_start).total_seconds() / BUCKET.total_seconds()),
        )
        return self._calculate(elapsed)

    def _calculate(self, fraction: float) -> CognitiveStatePoint:
        assert self._bucket_start is not None and self._samples
        working_samples = [
            sample for sample in self._samples if sample.presence == Presence.ACTIVE
        ]
        is_working = len(working_samples) > len(self._samples) / 2
        minutes = 5.0 * fraction
        continuous = (
            self._continuous_work_minutes + minutes
            if is_working
            else max(0.0, self._continuous_work_minutes - 10.0 * fraction)
        )
        recovery = 0.0 if is_working else self._recovery_minutes + minutes
        continuous_pressure = _smoothstep(20.0, 120.0, continuous)
        switching = self._switching_pressure * 0.72 + min(
            self._activity_switches / 3.0, 1.0
        ) * 0.28
        activity = _dominant_activity(self._samples)
        intensity = self._INTENSITY[activity]
        confidence = _mean(sample.confidence for sample in self._samples)
        focus = _mean(sample.focus_level for sample in self._samples)

        if is_working:
            fatigue = _clamp(
                self._fatigue
                + fraction * (0.004 + intensity * 0.009 + continuous_pressure * 0.006)
            )
        else:
            recovery_rate = 0.014 + _smoothstep(5.0, 45.0, recovery) * 0.016
            fatigue = _clamp(self._fatigue - fraction * recovery_rate * confidence)
        recovery_effect = (
            0.0 if is_working else 0.3 + _smoothstep(5.0, 30.0, recovery) * 0.25
        )
        raw_load = (
            intensity * 0.52
            + continuous_pressure * 0.18
            + switching * 0.15
            + fatigue * 0.15
            - recovery_effect
        )
        load = _clamp(raw_load) if is_working else _clamp(raw_load, 0.04, 0.26)
        state = (
            RecoveryState.WORKING
            if is_working
            else RecoveryState.RESTED
            if recovery >= 25 and fatigue < 0.25
            else RecoveryState.RECOVERING
        )
        return CognitiveStatePoint(
            device_id=self._samples[-1].device_id,
            time=self._bucket_start,
            cognitive_load=load,
            mental_fatigue=fatigue,
            focus=focus,
            task_type=activity.value if is_working and activity != Activity.UNKNOWN else None,
            task_confidence=_clamp(confidence - switching * 0.08, 0.2, 0.96),
            recovery_state=state,
            revision=max(1, self._revision),
        )

    def _commit(self, point: CognitiveStatePoint) -> None:
        working = point.recovery_state == RecoveryState.WORKING
        self._fatigue = point.mental_fatigue
        self._continuous_work_minutes = (
            self._continuous_work_minutes + 5.0
            if working
            else max(0.0, self._continuous_work_minutes - 10.0)
        )
        self._recovery_minutes = 0.0 if working else self._recovery_minutes + 5.0
        self._switching_pressure = self._switching_pressure * 0.72 + min(
            self._activity_switches / 3.0, 1.0
        ) * 0.28


def cognitive_point_dict(point: CognitiveStatePoint) -> dict[str, object]:
    return {
        "device_id": point.device_id,
        "time": int(point.time.timestamp() * 1000),
        "cognitive_load": round(point.cognitive_load, 4),
        "mental_fatigue": round(point.mental_fatigue, 4),
        "focus": round(point.focus, 4),
        "task_type": point.task_type,
        "task_confidence": round(point.task_confidence, 4),
        "recovery_state": point.recovery_state.value,
        "source": point.source,
        "model_version": point.model_version,
        "revision": point.revision,
    }


def _bucket_start(value: datetime) -> datetime:
    value = value.astimezone(UTC)
    return value.replace(minute=value.minute - value.minute % 5, second=0, microsecond=0)


def _dominant_activity(samples: list[WorkStateEstimate]) -> Activity:
    scores: dict[Activity, float] = {}
    for sample in samples:
        scores[sample.activity] = scores.get(sample.activity, 0.0) + sample.confidence
    return max(scores, key=scores.get) if scores else Activity.UNKNOWN


def _mean(values) -> float:
    items = list(values)
    return sum(items) / max(1, len(items))


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return min(maximum, max(minimum, value))


def _smoothstep(edge0: float, edge1: float, value: float) -> float:
    progress = _clamp((value - edge0) / (edge1 - edge0))
    return progress * progress * (3 - 2 * progress)
