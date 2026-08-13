"""Strict, immutable contracts for the Chronos Agent interaction protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType


class OperationState(StrEnum):
    INTERPRETING = "interpreting"
    AWAITING_CLARIFICATION = "awaiting_clarification"
    READY = "ready"
    PROPOSED = "proposed"
    APPROVED = "approved"
    EXECUTING = "executing"
    COMPLETED = "completed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    FAILED = "failed"
    STALE = "stale"


class ProjectionKind(StrEnum):
    CLARIFICATION = "clarification"
    PROPOSAL = "proposal"


class ProjectionVisualState(StrEnum):
    INCOMPLETE = "incomplete"
    PROPOSED = "proposed"


class TransactionStatus(StrEnum):
    APPLIED = "applied"
    REVERTED = "reverted"


class LogEventType(StrEnum):
    USER_PROMPT = "user_prompt"
    AGENT_MESSAGE = "agent_message"
    OPERATION_CREATED = "operation_created"
    CLARIFICATION_REQUESTED = "clarification_requested"
    CLARIFICATION_ANSWERED = "clarification_answered"
    PROPOSAL_CREATED = "proposal_created"
    PROPOSAL_UPDATED = "proposal_updated"
    OPERATION_APPROVED = "operation_approved"
    OPERATION_EXECUTED = "operation_executed"
    OPERATION_COMPLETED = "operation_completed"
    OPERATION_REJECTED = "operation_rejected"
    OPERATION_FAILED = "operation_failed"
    MANUAL_TASK_MOVE = "manual_task_move"
    MANUAL_TASK_RESIZE = "manual_task_resize"
    MANUAL_REMINDER_MOVE = "manual_reminder_move"
    UNDO = "undo"
    RESTORE = "restore"


class ReplanSignalType(StrEnum):
    SCHEDULE_DRIFT = "schedule_drift"
    TASK_OVERRUN = "task_overrun"
    MISSED_TASK = "missed_task"
    FIXED_CONFLICT = "fixed_conflict"
    COGNITIVE_OVERLOAD = "cognitive_overload"


@dataclass(frozen=True, slots=True)
class TimeRange:
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError("time range must have positive duration")


@dataclass(frozen=True, slots=True)
class TimelineReference:
    type: str
    id: str | None = None
    start: int | None = None
    end: int | None = None

    def __post_init__(self) -> None:
        if self.type in {"task", "reminder"}:
            if not self.id or self.start is not None or self.end is not None:
                raise ValueError(f"{self.type} reference requires only id")
        elif self.type == "time_range":
            if self.id is not None or self.start is None or self.end is None:
                raise ValueError("time_range reference requires start and end")
            if self.end <= self.start:
                raise ValueError("time_range reference must have positive duration")
        else:
            raise ValueError(f"unsupported timeline reference: {self.type}")


@dataclass(frozen=True, slots=True)
class OperationScope:
    task_ids: tuple[str, ...] = ()
    reminder_ids: tuple[str, ...] = ()
    time_ranges: tuple[TimeRange, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_ids", _unique_ids(self.task_ids, "task"))
        object.__setattr__(self, "reminder_ids", _unique_ids(self.reminder_ids, "reminder"))

    def overlaps(self, other: OperationScope) -> bool:
        if set(self.task_ids) & set(other.task_ids):
            return True
        if set(self.reminder_ids) & set(other.reminder_ids):
            return True
        return any(
            left.start < right.end and right.start < left.end
            for left in self.time_ranges
            for right in other.time_ranges
        )


@dataclass(frozen=True, slots=True)
class RecurrenceSpec:
    frequency: str
    weekdays: tuple[int, ...] = ()
    until: str | None = None

    def __post_init__(self) -> None:
        if self.frequency not in {"daily", "weekly"}:
            raise ValueError("recurrence frequency must be daily or weekly")
        days = tuple(sorted(set(self.weekdays)))
        if self.frequency == "weekly" and (not days or any(day < 0 or day > 6 for day in days)):
            raise ValueError("weekly recurrence requires weekdays from 0 to 6")
        if self.frequency == "daily" and days:
            raise ValueError("daily recurrence cannot contain weekdays")
        object.__setattr__(self, "weekdays", days)
        if self.until is not None:
            datetime.fromisoformat(self.until)


@dataclass(frozen=True, slots=True)
class AdjustmentPolicy:
    rigidity: float = 0.5
    priority: int = 3
    deferrability: float = 0.5
    shrinkability: float = 0.0
    min_duration: int | None = None
    continuity_value: float | None = None

    def __post_init__(self) -> None:
        for name in ("rigidity", "deferrability", "shrinkability"):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.continuity_value is not None and not 0 <= self.continuity_value <= 1:
            raise ValueError("continuity_value must be between 0 and 1")
        if not 1 <= self.priority <= 5:
            raise ValueError("priority must be between 1 and 5")
        if self.min_duration is not None and self.min_duration <= 0:
            raise ValueError("min_duration must be positive")


@dataclass(frozen=True, slots=True)
class TaskSpec:
    title: str
    start: int
    duration_minutes: int
    task_type: str = "execution"
    fixed: bool = False
    recurrence: RecurrenceSpec | None = None
    window: TimeRange | None = None
    adjustment_policy: AdjustmentPolicy = field(default_factory=AdjustmentPolicy)

    def __post_init__(self) -> None:
        if not self.title.strip() or self.duration_minutes <= 0:
            raise ValueError("task title and positive duration are required")
        if self.window and not self.window.start <= self.start < self.window.end:
            raise ValueError("task start must fall inside its window")


@dataclass(frozen=True, slots=True)
class ReminderSpec:
    title: str
    trigger_type: str
    at: int | None = None
    window: TimeRange | None = None
    delivery: str = "exact"
    priority: int = 3
    prefer_interruptible_moment: bool = False
    avoid_high_focus: bool = False

    def __post_init__(self) -> None:
        if not self.title.strip() or not 1 <= self.priority <= 5:
            raise ValueError("reminder title and priority from 1 to 5 are required")
        if self.trigger_type == "time":
            if self.at is None or self.window is not None or self.delivery != "exact":
                raise ValueError("point reminder requires at and exact delivery")
        elif self.trigger_type == "window":
            if self.at is not None or self.window is None:
                raise ValueError("window reminder requires only a window")
            if self.delivery not in {"exact", "context-aware"}:
                raise ValueError("unsupported reminder delivery")
        else:
            raise ValueError("reminder trigger must be time or window")


@dataclass(frozen=True, slots=True)
class CreateTaskOperation:
    task_id: str
    task: TaskSpec
    type: str = field(default="create_task", init=False)


@dataclass(frozen=True, slots=True)
class UpdateTaskOperation:
    task_id: str
    task: TaskSpec
    type: str = field(default="update_task", init=False)


@dataclass(frozen=True, slots=True)
class MoveTaskOperation:
    task_id: str
    start: int
    type: str = field(default="move_task", init=False)


@dataclass(frozen=True, slots=True)
class ResizeTaskOperation:
    task_id: str
    duration_minutes: int
    type: str = field(default="resize_task", init=False)

    def __post_init__(self) -> None:
        if self.duration_minutes <= 0:
            raise ValueError("task duration must be positive")


@dataclass(frozen=True, slots=True)
class DeleteTaskOperation:
    task_id: str
    type: str = field(default="delete_task", init=False)


@dataclass(frozen=True, slots=True)
class CreateReminderOperation:
    reminder_id: str
    reminder: ReminderSpec
    type: str = field(default="create_reminder", init=False)


@dataclass(frozen=True, slots=True)
class UpdateReminderOperation:
    reminder_id: str
    reminder: ReminderSpec
    type: str = field(default="update_reminder", init=False)


@dataclass(frozen=True, slots=True)
class MoveReminderOperation:
    reminder_id: str
    at: int | None = None
    window: TimeRange | None = None
    type: str = field(default="move_reminder", init=False)

    def __post_init__(self) -> None:
        if (self.at is None) == (self.window is None):
            raise ValueError("move reminder requires exactly one trigger")


@dataclass(frozen=True, slots=True)
class DeleteReminderOperation:
    reminder_id: str
    type: str = field(default="delete_reminder", init=False)


@dataclass(frozen=True, slots=True)
class CreateRecurrenceOperation:
    task_id: str
    recurrence: RecurrenceSpec
    type: str = field(default="create_recurrence", init=False)


@dataclass(frozen=True, slots=True)
class UpdateRecurrenceOperation:
    task_id: str
    recurrence: RecurrenceSpec
    type: str = field(default="update_recurrence", init=False)


@dataclass(frozen=True, slots=True)
class DeferTaskOperation:
    task_id: str
    target_start: int
    type: str = field(default="defer_task", init=False)


@dataclass(frozen=True, slots=True)
class ShrinkTaskOperation:
    task_id: str
    duration_minutes: int
    type: str = field(default="shrink_task", init=False)

    def __post_init__(self) -> None:
        if self.duration_minutes <= 0:
            raise ValueError("task duration must be positive")


@dataclass(frozen=True, slots=True)
class SplitTaskOperation:
    task_id: str
    segments: tuple[TimeRange, ...]
    type: str = field(default="split_task", init=False)

    def __post_init__(self) -> None:
        if len(self.segments) < 2:
            raise ValueError("split task requires at least two segments")


type TimelineOperation = (
    CreateTaskOperation
    | UpdateTaskOperation
    | MoveTaskOperation
    | ResizeTaskOperation
    | DeleteTaskOperation
    | CreateReminderOperation
    | UpdateReminderOperation
    | MoveReminderOperation
    | DeleteReminderOperation
    | CreateRecurrenceOperation
    | UpdateRecurrenceOperation
    | DeferTaskOperation
    | ShrinkTaskOperation
    | SplitTaskOperation
)


@dataclass(frozen=True, slots=True)
class IntentSnapshot:
    kind: str
    summary: str
    source_text: str | None = None
    attributes: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.kind or not self.summary.strip():
            raise ValueError("intent kind and summary are required")
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))


@dataclass(frozen=True, slots=True)
class ClarificationState:
    field: str
    question: str
    options: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.field or not self.question.strip():
            raise ValueError("clarification field and question are required")


@dataclass(frozen=True, slots=True)
class TimelineProjection:
    id: str
    operation_id: str
    type: ProjectionKind
    target: TimelineReference
    visual_state: ProjectionVisualState
    start: int | None = None
    end: int | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id or not self.operation_id:
            raise ValueError("projection id and operation id are required")
        if (self.start is None) != (self.end is None):
            raise ValueError("projection start and end must appear together")
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ValueError("projection range must have positive duration")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class ProposalSnapshot:
    operation_id: str
    version: int
    operations: tuple[TimelineOperation, ...]
    created_at: datetime
    explanation: str | None = None

    def __post_init__(self) -> None:
        if not self.operation_id or self.version <= 0 or not self.operations:
            raise ValueError("proposal requires operation, positive version, and operations")
        _aware(self.created_at, "proposal created_at")


@dataclass(frozen=True, slots=True)
class AgentOperation:
    id: str
    state: OperationState
    intent: IntentSnapshot
    unresolved_questions: tuple[ClarificationState, ...]
    compiled_operations: tuple[TimelineOperation, ...]
    projections: tuple[TimelineProjection, ...]
    references: tuple[TimelineReference, ...]
    scope: OperationScope
    ambiguity: float
    risk: float
    impact: float
    reversible: bool
    required_autonomy_level: int
    created_at: datetime
    updated_at: datetime
    version: int
    proposal: ProposalSnapshot | None = None
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.id or self.version <= 0:
            raise ValueError("operation id and positive version are required")
        for name in ("ambiguity", "risk", "impact"):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if not 0 <= self.required_autonomy_level <= 3:
            raise ValueError("required autonomy level must be from 0 to 3")
        _aware(self.created_at, "operation created_at")
        _aware(self.updated_at, "operation updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("operation updated_at cannot precede created_at")
        if self.state == OperationState.AWAITING_CLARIFICATION and not self.unresolved_questions:
            raise ValueError("awaiting clarification requires unresolved questions")
        if self.state == OperationState.PROPOSED and self.proposal is None:
            raise ValueError("proposed operation requires proposal snapshot")
        if self.proposal and (
            self.proposal.operation_id != self.id or self.proposal.version != self.version
        ):
            raise ValueError("proposal must match operation id and version")
        if any(item.operation_id != self.id for item in self.projections):
            raise ValueError("projection must belong to its operation")


@dataclass(frozen=True, slots=True)
class ChronosLogEntry:
    id: str
    event_type: LogEventType
    occurred_at: datetime
    message: str
    operation_id: str | None = None
    references: tuple[TimelineReference, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id or not self.message.strip():
            raise ValueError("log id and message are required")
        _aware(self.occurred_at, "log occurred_at")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class ScheduleSnapshot:
    captured_at: datetime
    tasks: tuple[dict[str, object], ...] = ()
    reminders: tuple[dict[str, object], ...] = ()
    plan_versions: dict[str, int | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _aware(self.captured_at, "snapshot captured_at")
        object.__setattr__(
            self, "tasks", tuple(MappingProxyType(dict(item)) for item in self.tasks)
        )
        object.__setattr__(
            self, "reminders", tuple(MappingProxyType(dict(item)) for item in self.reminders)
        )
        object.__setattr__(self, "plan_versions", MappingProxyType(dict(self.plan_versions)))


@dataclass(frozen=True, slots=True)
class AdjustmentTransaction:
    id: str
    operation_id: str
    before_state: ScheduleSnapshot
    operations: tuple[TimelineOperation, ...]
    after_state: ScheduleSnapshot
    status: TransactionStatus
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.id or not self.operation_id or not self.operations:
            raise ValueError("transaction id, operation id, and operations are required")
        _aware(self.created_at, "transaction created_at")


@dataclass(frozen=True, slots=True)
class AutonomyPolicy:
    level: int = 0
    max_risk: float = 0.2
    max_ambiguity: float = 0.15
    max_impact: float = 0.25
    require_reversible: bool = True

    def __post_init__(self) -> None:
        if not 0 <= self.level <= 3:
            raise ValueError("autonomy level must be from 0 to 3")
        for name in ("max_risk", "max_ambiguity", "max_impact"):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(f"{name} must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class InteractionContext:
    current_time: int
    user_input: str | None = None
    selection: TimelineReference | None = None
    current_state: dict[str, object] | None = None
    timeline_context: dict[str, object] = field(default_factory=dict)
    active_operation_ids: tuple[str, ...] = ()
    relevant_log_entry_ids: tuple[str, ...] = ()
    user_profile: dict[str, object] | None = None

    def __post_init__(self) -> None:
        if self.current_time <= 0:
            raise ValueError("current_time must be positive")
        object.__setattr__(self, "timeline_context", MappingProxyType(dict(self.timeline_context)))
        if self.current_state is not None:
            object.__setattr__(self, "current_state", MappingProxyType(dict(self.current_state)))
        if self.user_profile is not None:
            object.__setattr__(self, "user_profile", MappingProxyType(dict(self.user_profile)))


@dataclass(frozen=True, slots=True)
class ReplanSignal:
    type: ReplanSignalType
    severity: float
    confidence: float
    threshold_reached: bool
    references: tuple[TimelineReference, ...] = ()

    def __post_init__(self) -> None:
        if not 0 <= self.severity <= 1 or not 0 <= self.confidence <= 1:
            raise ValueError("signal severity and confidence must be between 0 and 1")


def _unique_ids(values: tuple[str, ...], name: str) -> tuple[str, ...]:
    if any(not value for value in values):
        raise ValueError(f"{name} ids cannot be empty")
    if len(set(values)) != len(values):
        raise ValueError(f"{name} ids must be unique")
    return tuple(values)


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
