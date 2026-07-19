"""Stabilize noisy estimates into durable activity intervals."""

from __future__ import annotations

from dataclasses import replace

from chronos.models import ActivitySegment, SegmentStatus, WorkStateEstimate


class SegmentBuilder:
    """Require repeated evidence before switching the active segment."""

    def __init__(self, switch_confirmation_count: int = 2) -> None:
        if switch_confirmation_count <= 0:
            raise ValueError("switch_confirmation_count must be positive")
        self._required = switch_confirmation_count
        self._current: ActivitySegment | None = None
        self._candidate: list[WorkStateEstimate] = []

    def add(self, estimate: WorkStateEstimate) -> list[ActivitySegment]:
        if self._current is None:
            self._current = self._from_estimate(estimate)
            return []

        if estimate.activity == self._current.activity:
            self._candidate.clear()
            self._current = replace(
                self._current,
                end_at=estimate.window_end,
                confidence=_running_average(
                    self._current.confidence,
                    self._current.estimate_count,
                    estimate.confidence,
                ),
                estimate_count=self._current.estimate_count + 1,
            )
            return []

        if self._candidate and self._candidate[0].activity != estimate.activity:
            self._candidate.clear()
        self._candidate.append(estimate)
        if len(self._candidate) < self._required:
            return []

        transition_at = self._candidate[0].window_start
        finalized = replace(
            self._current,
            end_at=transition_at,
            status=SegmentStatus.FINALIZED,
        )
        candidate_estimates = self._candidate
        self._candidate = []
        self._current = self._from_estimate(candidate_estimates[0])
        for item in candidate_estimates[1:]:
            self._current = replace(
                self._current,
                end_at=item.window_end,
                confidence=_running_average(
                    self._current.confidence,
                    self._current.estimate_count,
                    item.confidence,
                ),
                estimate_count=self._current.estimate_count + 1,
            )
        return [finalized]

    def flush(self) -> ActivitySegment | None:
        if self._current is None:
            return None
        if self._candidate:
            self._current = replace(self._current, end_at=self._candidate[-1].window_end)
        finalized = replace(self._current, status=SegmentStatus.FINALIZED)
        self._current = None
        self._candidate.clear()
        return finalized

    @staticmethod
    def _from_estimate(estimate: WorkStateEstimate) -> ActivitySegment:
        return ActivitySegment(
            device_id=estimate.device_id,
            start_at=estimate.window_start,
            end_at=estimate.window_end,
            activity=estimate.activity,
            confidence=estimate.confidence,
            status=SegmentStatus.OPEN,
            task_id=estimate.possible_task_id,
        )


def _running_average(current: float, count: int, new: float) -> float:
    return ((current * count) + new) / (count + 1)

