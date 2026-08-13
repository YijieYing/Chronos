"""Adjustment signals bridged into the Agent Operation protocol.

The first integration is deliberately passive: reliable backend evidence is
captured as a completed Operation, but it never mutates Schedule or opens UI.
Replacing ``PassiveReplanCompiler`` later enables proposal generation without
changing signal producers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from chronos.agent.models import (
    AgentOperation,
    IntentSnapshot,
    OperationScope,
    OperationState,
    ReplanSignal,
    ReplanSignalType,
    TimeRange,
    TimelineReference,
)
from chronos.agent.service import OperationStore
from chronos.monitor.cognitive import CognitiveStatePoint
from chronos.schedule.models import Task, TaskStatus
from chronos.schedule.planner import task_occurrence
from chronos.schedule.service import ScheduleService

MISSED_GRACE = timedelta(minutes=15)
COGNITIVE_FRESHNESS = timedelta(minutes=15)
COGNITIVE_LOAD_THRESHOLD = 0.78
COGNITIVE_CONFIDENCE_THRESHOLD = 0.60
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DetectedReplanSignal:
    """A signal plus the stable identity required for idempotent ingestion."""

    signal: ReplanSignal
    key: str
    source: str
    detected_at: datetime


class CognitiveStateReader(Protocol):
    def latest(self) -> CognitiveStatePoint | None: ...


class AdjustmentEngine:
    """Detect only adjustment conditions supported by current backend evidence."""

    def __init__(
        self,
        schedule: ScheduleService,
        cognitive_states: CognitiveStateReader,
    ) -> None:
        self._schedule = schedule
        self._cognitive_states = cognitive_states

    def detect(self, now: datetime | None = None) -> tuple[DetectedReplanSignal, ...]:
        timestamp = (now or datetime.now(UTC)).astimezone(UTC)
        zone = ZoneInfo(self._schedule.settings()["timezone"])
        local_now = timestamp.astimezone(zone)
        tasks = self._schedule.list_tasks()
        occurrences = [
            occurrence
            for task in tasks
            if (occurrence := task_occurrence(task, local_now.date())) is not None
        ]
        signals = [
            *self._fixed_conflicts(occurrences, timestamp, local_now.date().isoformat()),
            *self._missed_tasks(occurrences, timestamp, local_now.date().isoformat()),
        ]
        cognitive = self._cognitive_overload(
            self._cognitive_states.latest(), occurrences, timestamp
        )
        if cognitive is not None:
            signals.append(cognitive)
        return tuple(signals)

    @staticmethod
    def _fixed_conflicts(
        tasks: list[Task], now: datetime, day_key: str
    ) -> list[DetectedReplanSignal]:
        fixed = sorted(
            (task for task in tasks if task.fixed and task.preferred_start is not None),
            key=lambda task: task.preferred_start,
        )
        detected: list[DetectedReplanSignal] = []
        for index, left in enumerate(fixed):
            assert left.preferred_start is not None
            left_end = left.preferred_start + timedelta(minutes=left.estimated_minutes)
            for right in fixed[index + 1 :]:
                assert right.preferred_start is not None
                if right.preferred_start >= left_end:
                    break
                right_end = right.preferred_start + timedelta(minutes=right.estimated_minutes)
                overlap = (min(left_end, right_end) - right.preferred_start).total_seconds() / 60
                task_ids = tuple(sorted((left.task_id, right.task_id)))
                detected.append(
                    DetectedReplanSignal(
                        signal=ReplanSignal(
                            type=ReplanSignalType.FIXED_CONFLICT,
                            severity=min(1.0, max(0.25, overlap / 30)),
                            confidence=1.0,
                            threshold_reached=True,
                            references=tuple(
                                TimelineReference(type="task", id=task_id)
                                for task_id in task_ids
                            ),
                        ),
                        key=f"fixed_conflict:{day_key}:{':'.join(task_ids)}",
                        source="schedule",
                        detected_at=now,
                    )
                )
        return detected

    @staticmethod
    def _missed_tasks(
        tasks: list[Task], now: datetime, day_key: str
    ) -> list[DetectedReplanSignal]:
        detected: list[DetectedReplanSignal] = []
        for task in tasks:
            if task.preferred_start is None or task.status in {
                TaskStatus.IN_PROGRESS,
                TaskStatus.COMPLETED,
                TaskStatus.CANCELLED,
            }:
                continue
            end = task.preferred_start + timedelta(minutes=task.estimated_minutes)
            overdue = now - end.astimezone(UTC)
            if overdue < MISSED_GRACE:
                continue
            detected.append(
                DetectedReplanSignal(
                    signal=ReplanSignal(
                        type=ReplanSignalType.MISSED_TASK,
                        severity=min(1.0, max(0.2, overdue.total_seconds() / 7200)),
                        confidence=0.95,
                        threshold_reached=True,
                        references=(TimelineReference(type="task", id=task.task_id),),
                    ),
                    key=f"missed_task:{day_key}:{task.task_id}",
                    source="schedule",
                    detected_at=now,
                )
            )
        return detected

    @staticmethod
    def _cognitive_overload(
        point: CognitiveStatePoint | None,
        tasks: list[Task],
        now: datetime,
    ) -> DetectedReplanSignal | None:
        if point is None or now - point.time.astimezone(UTC) > COGNITIVE_FRESHNESS:
            return None
        if (
            point.cognitive_load < COGNITIVE_LOAD_THRESHOLD
            or point.task_confidence < COGNITIVE_CONFIDENCE_THRESHOLD
        ):
            return None
        active = [
            task
            for task in tasks
            if task.preferred_start is not None
            and task.preferred_start.astimezone(UTC) <= now
            < (task.preferred_start + timedelta(minutes=task.estimated_minutes)).astimezone(UTC)
        ]
        references = (
            tuple(TimelineReference(type="task", id=task.task_id) for task in active)
            if active
            else (
                TimelineReference(
                    type="time_range",
                    start=int(point.time.timestamp() * 1000),
                    end=int((point.time + timedelta(minutes=5)).timestamp() * 1000),
                ),
            )
        )
        return DetectedReplanSignal(
            signal=ReplanSignal(
                type=ReplanSignalType.COGNITIVE_OVERLOAD,
                severity=point.cognitive_load,
                confidence=point.task_confidence,
                threshold_reached=True,
                references=references,
            ),
            key=f"cognitive_overload:{point.device_id}:{point.time.isoformat()}",
            source="monitor.cognitive_state",
            detected_at=now,
        )


class PassiveReplanCompiler:
    """Compile a signal into a strict, non-user-facing Operation snapshot."""

    def compile(
        self,
        operation: AgentOperation,
        detected: DetectedReplanSignal,
    ) -> AgentOperation:
        signal = detected.signal
        return replace(
            operation,
            state=OperationState.COMPLETED,
            intent=IntentSnapshot(
                kind="replan_signal",
                summary=_signal_summary(signal.type),
                attributes={
                    "signal_type": signal.type.value,
                    "severity": signal.severity,
                    "confidence": signal.confidence,
                    "threshold_reached": signal.threshold_reached,
                    "source": detected.source,
                    "proactive_enabled": False,
                    "execution": "captured_without_timeline_change",
                },
            ),
            references=signal.references,
            scope=_scope_for(signal.references),
            ambiguity=max(0.0, 1.0 - signal.confidence),
            risk=signal.severity,
            impact=signal.severity,
            required_autonomy_level=2,
            updated_at=detected.detected_at,
            version=operation.version + 1,
        )


class AdjustmentCoordinator:
    """Idempotently route detector output through the Operation Store."""

    def __init__(
        self,
        engine: AdjustmentEngine,
        operations: OperationStore,
        compiler: PassiveReplanCompiler | None = None,
    ) -> None:
        self._engine = engine
        self._operations = operations
        self._compiler = compiler or PassiveReplanCompiler()

    def scan(self, now: datetime | None = None) -> tuple[AgentOperation, ...]:
        captured: list[AgentOperation] = []
        for detected in self._engine.detect(now):
            if not detected.signal.threshold_reached:
                continue
            operation_id = _operation_id(detected.key)
            try:
                existing = self._operations.get(operation_id)
                if existing.state == OperationState.INTERPRETING:
                    try:
                        existing = self._operations.save_snapshot(
                            self._compiler.compile(existing, detected),
                            expected_version=existing.version,
                        )
                    except Exception:
                        existing = self._operations.get(operation_id)
                captured.append(existing)
                continue
            except KeyError:
                pass
            try:
                initial = self._operations.create(
                    IntentSnapshot(kind="replan_signal", summary="正在解析调整信号"),
                    operation_id=operation_id,
                    now=detected.detected_at,
                    scope=_scope_for(detected.signal.references),
                )
            except ValueError:
                # Another request may have captured the same deterministic signal.
                captured.append(self._operations.get(operation_id))
                continue
            compiled = self._compiler.compile(initial, detected)
            captured.append(
                self._operations.save_snapshot(compiled, expected_version=initial.version)
            )
        return tuple(captured)

    def scan_safely(self, now: datetime | None = None) -> tuple[AgentOperation, ...]:
        """Keep passive analysis failures outside Monitor and Schedule write paths."""

        try:
            return self.scan(now)
        except Exception:
            LOGGER.exception("Passive adjustment scan failed")
            return ()

    def list_captured(self) -> tuple[AgentOperation, ...]:
        return tuple(
            operation
            for operation in self._operations.list()
            if operation.intent.kind == "replan_signal"
        )


def _operation_id(key: str) -> str:
    digest = sha256(key.encode("utf-8")).hexdigest()
    return str(uuid5(NAMESPACE_URL, f"chronos:replan-signal:{digest}"))


def _scope_for(references: tuple[TimelineReference, ...]) -> OperationScope:
    return OperationScope(
        task_ids=tuple(ref.id for ref in references if ref.type == "task" and ref.id),
        reminder_ids=tuple(
            ref.id for ref in references if ref.type == "reminder" and ref.id
        ),
        time_ranges=tuple(
            TimeRange(ref.start, ref.end)
            for ref in references
            if ref.type == "time_range" and ref.start is not None and ref.end is not None
        ),
    )


def _signal_summary(signal_type: ReplanSignalType) -> str:
    return {
        ReplanSignalType.MISSED_TASK: "检测到未按计划完成的任务",
        ReplanSignalType.FIXED_CONFLICT: "检测到固定任务时间冲突",
        ReplanSignalType.COGNITIVE_OVERLOAD: "检测到认知负荷过高",
        ReplanSignalType.SCHEDULE_DRIFT: "检测到日程偏移",
        ReplanSignalType.TASK_OVERRUN: "检测到任务超时",
    }[signal_type]
