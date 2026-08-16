"""Versioned wire serialization for Agent Operation contracts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime

from chronos.agent.meaning import (
    Answer,
    Content,
    Directive,
    DirectiveKind,
    Duration,
    DurationKind,
    Event,
    Field,
    Gap,
    GapReason,
    Item,
    Kind,
    Origin,
    Period,
    Precision,
    Provenance,
    Reference,
    Relation,
    RelationKind,
    Request as SemanticRequest,
    RequestKind,
    Residue,
    ResidueReason,
    ResidueStatus,
    Snapshot,
    Span,
    Time as SemanticTime,
    TimeKind,
)
from chronos.agent.plan import (
    Assumption,
    Change,
    Conflict,
    Horizon,
    Plan,
    ReminderDraft,
    TaskDraft,
    Window,
)

from chronos.agent.models import (
    AdjustmentPolicy,
    AgentOperation,
    ChronosLogEntry,
    ClarificationState,
    CreateRecurrenceOperation,
    CreateReminderOperation,
    CreateTaskOperation,
    DeferTaskOperation,
    DeleteReminderOperation,
    DeleteTaskOperation,
    IntentSnapshot,
    LogEventType,
    MoveReminderOperation,
    MoveTaskOperation,
    OperationScope,
    OperationState,
    ProjectionKind,
    ProjectionVisualState,
    ProposalSnapshot,
    RecurrenceSpec,
    ReminderSpec,
    ResizeTaskOperation,
    ShrinkTaskOperation,
    SplitTaskOperation,
    TaskSpec,
    TimelineOperation,
    TimelineProjection,
    TimelineReference,
    TimeRange,
    UpdateRecurrenceOperation,
    UpdateReminderOperation,
    UpdateTaskOperation,
)

AGENT_OPERATION_SCHEMA_VERSION = 2
CHRONOS_LOG_SCHEMA_VERSION = 1


def log_entry_to_dict(entry: ChronosLogEntry) -> dict[str, object]:
    return {
        "schema_version": CHRONOS_LOG_SCHEMA_VERSION,
        "id": entry.id,
        "event_type": entry.event_type.value,
        "occurred_at": entry.occurred_at.isoformat(),
        "message": entry.message,
        "operation_id": entry.operation_id,
        "references": [_reference_to_dict(item) for item in entry.references],
        "metadata": dict(entry.metadata),
    }


def log_entry_from_dict(payload: dict[str, object]) -> ChronosLogEntry:
    _keys(
        payload,
        {
            "schema_version",
            "id",
            "event_type",
            "occurred_at",
            "message",
            "operation_id",
            "references",
            "metadata",
        },
        "log entry",
    )
    if payload["schema_version"] != CHRONOS_LOG_SCHEMA_VERSION:
        raise ValueError("unsupported Chronos Log schema version")
    return ChronosLogEntry(
        id=str(payload["id"]),
        event_type=LogEventType(str(payload["event_type"])),
        occurred_at=datetime.fromisoformat(str(payload["occurred_at"])),
        message=str(payload["message"]),
        operation_id=(
            str(payload["operation_id"]) if payload["operation_id"] is not None else None
        ),
        references=tuple(
            _reference_from_dict(_dict(item, "reference"))
            for item in _list(payload["references"], "references")
        ),
        metadata=_dict(payload["metadata"], "metadata"),
    )


def operation_to_dict(operation: AgentOperation) -> dict[str, object]:
    return {
        "schema_version": AGENT_OPERATION_SCHEMA_VERSION,
        "id": operation.id,
        "state": operation.state.value,
        "intent": {
            "kind": operation.intent.kind,
            "summary": operation.intent.summary,
            "source_text": operation.intent.source_text,
            "attributes": dict(operation.intent.attributes),
        },
        "unresolved_questions": [asdict(item) for item in operation.unresolved_questions],
        "compiled_operations": [
            timeline_operation_to_dict(item) for item in operation.compiled_operations
        ],
        "projections": [projection_to_dict(item) for item in operation.projections],
        "references": [_reference_to_dict(item) for item in operation.references],
        "scope": _scope_to_dict(operation.scope),
        "ambiguity": operation.ambiguity,
        "risk": operation.risk,
        "impact": operation.impact,
        "reversible": operation.reversible,
        "required_autonomy_level": operation.required_autonomy_level,
        "created_at": operation.created_at.isoformat(),
        "updated_at": operation.updated_at.isoformat(),
        "version": operation.version,
        "proposal": _proposal_to_dict(operation.proposal) if operation.proposal else None,
        "failure_reason": operation.failure_reason,
        "snapshot": asdict(operation.snapshot) if operation.snapshot else None,
        "plan": asdict(operation.plan) if operation.plan else None,
    }


def operation_from_dict(payload: dict[str, object]) -> AgentOperation:
    version = int(payload.get("schema_version", 0))
    if version not in {1, AGENT_OPERATION_SCHEMA_VERSION}:
        raise ValueError("unsupported Agent Operation schema version")
    expected = {
            "schema_version",
            "id",
            "state",
            "intent",
            "unresolved_questions",
            "compiled_operations",
            "projections",
            "references",
            "scope",
            "ambiguity",
            "risk",
            "impact",
            "reversible",
            "required_autonomy_level",
            "created_at",
            "updated_at",
            "version",
            "proposal",
            "failure_reason",
        }
    if version == 2:
        expected.update({"snapshot", "plan"})
    _keys(payload, expected, "operation")
    intent = _dict(payload["intent"], "intent")
    _keys(intent, {"kind", "summary", "source_text", "attributes"}, "intent")
    questions = _list(payload["unresolved_questions"], "unresolved_questions")
    projections = _list(payload["projections"], "projections")
    references = _list(payload["references"], "references")
    compiled = _list(payload["compiled_operations"], "compiled_operations")
    proposal = payload["proposal"]
    return AgentOperation(
        id=str(payload["id"]),
        state=OperationState(str(payload["state"])),
        intent=IntentSnapshot(
            kind=str(intent["kind"]),
            summary=str(intent["summary"]),
            source_text=str(intent["source_text"]) if intent["source_text"] is not None else None,
            attributes=_dict(intent["attributes"], "intent.attributes"),
        ),
        unresolved_questions=tuple(
            _question_from_dict(_dict(item, "question")) for item in questions
        ),
        compiled_operations=tuple(
            timeline_operation_from_dict(_dict(item, "operation")) for item in compiled
        ),
        projections=tuple(projection_from_dict(_dict(item, "projection")) for item in projections),
        references=tuple(_reference_from_dict(_dict(item, "reference")) for item in references),
        scope=_scope_from_dict(_dict(payload["scope"], "scope")),
        ambiguity=float(payload["ambiguity"]),
        risk=float(payload["risk"]),
        impact=float(payload["impact"]),
        reversible=_bool(payload["reversible"], "reversible"),
        required_autonomy_level=int(payload["required_autonomy_level"]),
        created_at=datetime.fromisoformat(str(payload["created_at"])),
        updated_at=datetime.fromisoformat(str(payload["updated_at"])),
        version=int(payload["version"]),
        proposal=_proposal_from_dict(_dict(proposal, "proposal")) if proposal is not None else None,
        failure_reason=str(payload["failure_reason"])
        if payload["failure_reason"] is not None
        else None,
        snapshot=(
            _snapshot_from_dict(_dict(payload["snapshot"], "snapshot"))
            if payload.get("snapshot") is not None
            else None
        ),
        plan=(
            _plan_from_dict(_dict(payload["plan"], "plan"))
            if payload.get("plan") is not None
            else None
        ),
    )


def timeline_operation_to_dict(operation: TimelineOperation) -> dict[str, object]:
    result = asdict(operation)
    if isinstance(operation, (CreateTaskOperation, UpdateTaskOperation)):
        result["task"] = _task_to_dict(operation.task)
    elif isinstance(operation, (CreateReminderOperation, UpdateReminderOperation)):
        result["reminder"] = _reminder_to_dict(operation.reminder)
    elif isinstance(operation, MoveReminderOperation) and operation.window:
        result["window"] = asdict(operation.window)
    elif isinstance(operation, (CreateRecurrenceOperation, UpdateRecurrenceOperation)):
        result["recurrence"] = _recurrence_to_dict(operation.recurrence)
    elif isinstance(operation, SplitTaskOperation):
        result["segments"] = [asdict(item) for item in operation.segments]
    return result


def timeline_operation_from_dict(payload: dict[str, object]) -> TimelineOperation:
    operation_type = str(payload.get("type", ""))
    loaders: dict[str, Callable[[dict[str, object]], TimelineOperation]] = {
        "create_task": lambda value: CreateTaskOperation(
            str(value["task_id"]), _task_from_dict(_dict(value["task"], "task"))
        ),
        "update_task": lambda value: UpdateTaskOperation(
            str(value["task_id"]), _task_from_dict(_dict(value["task"], "task"))
        ),
        "move_task": lambda value: MoveTaskOperation(str(value["task_id"]), int(value["start"])),
        "resize_task": lambda value: ResizeTaskOperation(
            str(value["task_id"]), int(value["duration_minutes"])
        ),
        "delete_task": lambda value: DeleteTaskOperation(str(value["task_id"])),
        "create_reminder": lambda value: CreateReminderOperation(
            str(value["reminder_id"]), _reminder_from_dict(_dict(value["reminder"], "reminder"))
        ),
        "update_reminder": lambda value: UpdateReminderOperation(
            str(value["reminder_id"]), _reminder_from_dict(_dict(value["reminder"], "reminder"))
        ),
        "move_reminder": _move_reminder_from_dict,
        "delete_reminder": lambda value: DeleteReminderOperation(str(value["reminder_id"])),
        "create_recurrence": lambda value: CreateRecurrenceOperation(
            str(value["task_id"]), _recurrence_from_dict(_dict(value["recurrence"], "recurrence"))
        ),
        "update_recurrence": lambda value: UpdateRecurrenceOperation(
            str(value["task_id"]), _recurrence_from_dict(_dict(value["recurrence"], "recurrence"))
        ),
        "defer_task": lambda value: DeferTaskOperation(
            str(value["task_id"]), int(value["target_start"])
        ),
        "shrink_task": lambda value: ShrinkTaskOperation(
            str(value["task_id"]), int(value["duration_minutes"])
        ),
        "split_task": lambda value: SplitTaskOperation(
            str(value["task_id"]),
            tuple(
                _range_from_dict(_dict(item, "segment"))
                for item in _list(value["segments"], "segments")
            ),
        ),
    }
    loader = loaders.get(operation_type)
    if loader is None:
        raise ValueError(f"unsupported TimelineOperation type: {operation_type}")
    return loader(payload)


def _task_to_dict(task: TaskSpec) -> dict[str, object]:
    return {
        "title": task.title,
        "start": task.start,
        "duration_minutes": task.duration_minutes,
        "task_type": task.task_type,
        "fixed": task.fixed,
        "recurrence": _recurrence_to_dict(task.recurrence) if task.recurrence else None,
        "window": asdict(task.window) if task.window else None,
        "adjustment_policy": asdict(task.adjustment_policy),
    }


def _task_from_dict(value: dict[str, object]) -> TaskSpec:
    recurrence = value.get("recurrence")
    window = value.get("window")
    policy = _dict(value.get("adjustment_policy", {}), "adjustment_policy")
    return TaskSpec(
        title=str(value["title"]),
        start=int(value["start"]),
        duration_minutes=int(value["duration_minutes"]),
        task_type=str(value.get("task_type", "execution")),
        fixed=_bool(value.get("fixed", False), "fixed"),
        recurrence=_recurrence_from_dict(_dict(recurrence, "recurrence")) if recurrence else None,
        window=_range_from_dict(_dict(window, "window")) if window else None,
        adjustment_policy=AdjustmentPolicy(**policy),
    )


def _reminder_to_dict(reminder: ReminderSpec) -> dict[str, object]:
    result = asdict(reminder)
    result["window"] = asdict(reminder.window) if reminder.window else None
    return result


def _reminder_from_dict(value: dict[str, object]) -> ReminderSpec:
    window = value.get("window")
    return ReminderSpec(
        title=str(value["title"]),
        trigger_type=str(value["trigger_type"]),
        at=int(value["at"]) if value.get("at") is not None else None,
        window=_range_from_dict(_dict(window, "window")) if window else None,
        delivery=str(value.get("delivery", "exact")),
        priority=int(value.get("priority", 3)),
        prefer_interruptible_moment=_bool(
            value.get("prefer_interruptible_moment", False), "prefer_interruptible_moment"
        ),
        avoid_high_focus=_bool(value.get("avoid_high_focus", False), "avoid_high_focus"),
    )


def _recurrence_from_dict(value: dict[str, object]) -> RecurrenceSpec:
    return RecurrenceSpec(
        frequency=str(value["frequency"]),
        weekdays=tuple(int(item) for item in _list(value.get("weekdays", []), "weekdays")),
        until=str(value["until"]) if value.get("until") is not None else None,
    )


def _recurrence_to_dict(value: RecurrenceSpec) -> dict[str, object]:
    return {
        "frequency": value.frequency,
        "weekdays": list(value.weekdays),
        "until": value.until,
    }


def _move_reminder_from_dict(value: dict[str, object]) -> MoveReminderOperation:
    window = value.get("window")
    return MoveReminderOperation(
        reminder_id=str(value["reminder_id"]),
        at=int(value["at"]) if value.get("at") is not None else None,
        window=_range_from_dict(_dict(window, "window")) if window else None,
    )


def projection_to_dict(item: TimelineProjection) -> dict[str, object]:
    return {
        "id": item.id,
        "operation_id": item.operation_id,
        "type": item.type.value,
        "target": _reference_to_dict(item.target),
        "visual_state": item.visual_state.value,
        "start": item.start,
        "end": item.end,
        "metadata": dict(item.metadata),
    }


def projection_from_dict(value: dict[str, object]) -> TimelineProjection:
    return TimelineProjection(
        id=str(value["id"]),
        operation_id=str(value["operation_id"]),
        type=ProjectionKind(str(value["type"])),
        target=_reference_from_dict(_dict(value["target"], "target")),
        visual_state=ProjectionVisualState(str(value["visual_state"])),
        start=int(value["start"]) if value.get("start") is not None else None,
        end=int(value["end"]) if value.get("end") is not None else None,
        metadata=_dict(value.get("metadata", {}), "metadata"),
    )


def _proposal_to_dict(value: ProposalSnapshot) -> dict[str, object]:
    return {
        "operation_id": value.operation_id,
        "version": value.version,
        "explanation": value.explanation,
        "plan_id": value.plan_id,
        "created_at": value.created_at.isoformat(),
    }


def _proposal_from_dict(value: dict[str, object]) -> ProposalSnapshot:
    return ProposalSnapshot(
        operation_id=str(value["operation_id"]),
        version=int(value["version"]),
        created_at=datetime.fromisoformat(str(value["created_at"])),
        explanation=str(value["explanation"]) if value.get("explanation") is not None else None,
        plan_id=str(value["plan_id"]) if value.get("plan_id") is not None else None,
    )


def _snapshot_from_dict(value: dict[str, object]) -> Snapshot:
    items = tuple(_item_from_dict(_dict(item, "item")) for item in _list(value["items"], "items"))
    return Snapshot(
        id=str(value["id"]),
        prompt_id=str(value["prompt_id"]),
        version=int(value["version"]),
        items=items,
        events=tuple(
            _event_from_dict(_dict(item, "event"))
            for item in _list(value.get("events", []), "events")
        ),
        directives=tuple(
            _directive_from_dict(_dict(item, "directive"))
            for item in _list(value.get("directives", []), "directives")
        ),
        answers=tuple(
            Answer(
                str(item["id"]), str(item["item_id"]),
                str(item["question_id"]), str(item["text"]),
            )
            for raw in _list(value.get("answers", []), "answers")
            for item in [_dict(raw, "answer")]
        ),
    )


def _item_from_dict(value: dict[str, object]) -> Item:
    return Item(
        str(value["id"]),
        str(value["prompt_id"]),
        _span_from_dict(_dict(value["span"], "span")),
        str(value["text"]),
    )


def _event_from_dict(value: dict[str, object]) -> Event:
    duration = value.get("duration")
    return Event(
        id=str(value["id"]),
        item_ids=tuple(str(item) for item in _list(value["item_ids"], "item_ids")),
        content=tuple(
            _content_from_dict(_dict(item, "content"))
            for item in _list(value["content"], "content")
        ),
        kind=Kind(str(value["kind"])),
        request=_semantic_request_from_dict(_dict(value["request"], "request")),
        time=_semantic_time_from_dict(_dict(value["time"], "time")),
        duration=_duration_from_dict(_dict(duration, "duration")) if duration else None,
        references=tuple(
            _semantic_reference_from_dict(_dict(item, "reference"))
            for item in _list(value.get("references", []), "references")
        ),
        relations=tuple(
            _relation_from_dict(_dict(item, "relation"))
            for item in _list(value.get("relations", []), "relations")
        ),
        gaps=tuple(
            _gap_from_dict(_dict(item, "gap"))
            for item in _list(value.get("gaps", []), "gaps")
        ),
        residue=tuple(
            _residue_from_dict(_dict(item, "residue"))
            for item in _list(value.get("residue", []), "residue")
        ),
        provenance=tuple(
            Provenance(
                Origin(str(data["source"])),
                tuple(str(item) for item in _list(data.get("item_ids", []), "item_ids")),
                tuple(
                    _span_from_dict(_dict(item, "evidence"))
                    for item in _list(data.get("evidence", []), "evidence")
                ),
            )
            for raw in _list(value.get("provenance", []), "provenance")
            for data in [_dict(raw, "provenance")]
        ),
    )


def _directive_from_dict(value: dict[str, object]) -> Directive:
    return Directive(
        str(value["id"]),
        str(value["item_id"]),
        DirectiveKind(str(value["type"])),
        tuple(
            _content_from_dict(_dict(item, "content"))
            for item in _list(value["content"], "content")
        ),
        tuple(
            _semantic_reference_from_dict(_dict(item, "reference"))
            for item in _list(value.get("references", []), "references")
        ),
        tuple(
            _residue_from_dict(_dict(item, "residue"))
            for item in _list(value.get("residue", []), "residue")
        ),
        str(value["response"]) if value.get("response") else None,
    )


def _content_from_dict(value: dict[str, object]) -> Content:
    return Content(
        str(value["item_id"]),
        _span_from_dict(_dict(value["span"], "span")),
        str(value["text"]),
    )


def _semantic_request_from_dict(value: dict[str, object]) -> SemanticRequest:
    target = value.get("target")
    return SemanticRequest(
        RequestKind(str(value["type"])),
        _semantic_reference_from_dict(_dict(target, "target")) if target else None,
        tuple(Field(str(item)) for item in _list(value.get("fields", []), "fields")),
    )


def _duration_from_dict(value: dict[str, object]) -> Duration:
    return Duration(
        DurationKind(str(value["type"])),
        minutes=int(value["minutes"]) if value.get("minutes") is not None else None,
        minimum=int(value["minimum"]) if value.get("minimum") is not None else None,
        maximum=int(value["maximum"]) if value.get("maximum") is not None else None,
    )


def _semantic_time_from_dict(value: dict[str, object]) -> SemanticTime:
    return SemanticTime(
        TimeKind(str(value["type"])),
        period=Period(str(value["period"])) if value.get("period") is not None else None,
        start=int(value["start"]) if value.get("start") is not None else None,
        end=int(value["end"]) if value.get("end") is not None else None,
        relation_id=str(value["relation_id"]) if value.get("relation_id") else None,
        text=str(value["text"]) if value.get("text") else None,
        precision=Precision(str(value.get("precision", "exact"))),
    )


def _semantic_reference_from_dict(value: dict[str, object]) -> Reference:
    return Reference(str(value["type"]), str(value["id"]))


def _relation_from_dict(value: dict[str, object]) -> Relation:
    return Relation(
        str(value["id"]), RelationKind(str(value["type"])),
        _semantic_reference_from_dict(_dict(value["target"], "target")),
    )


def _gap_from_dict(value: dict[str, object]) -> Gap:
    return Gap(
        item_id=str(value["item_id"]),
        field=str(value["field"]),
        question=str(value["question"]),
        reason=GapReason(str(value["reason"])),
        event_id=str(value["event_id"]) if value.get("event_id") else None,
        candidates=tuple(str(item) for item in _list(value.get("candidates", []), "candidates")),
    )


def _residue_from_dict(value: dict[str, object]) -> Residue:
    return Residue(
        str(value["item_id"]),
        _span_from_dict(_dict(value["span"], "span")),
        str(value["text"]),
        ResidueReason(str(value["reason"])),
        str(value["interpreter_version"]),
        str(value["hint"]) if value.get("hint") else None,
        ResidueStatus(str(value.get("status", "open"))),
    )


def _span_from_dict(value: dict[str, object]) -> Span:
    return Span(int(value["start"]), int(value["end"]))


def _plan_from_dict(value: dict[str, object]) -> Plan:
    horizon = _dict(value["horizon"], "horizon")
    return Plan(
        id=str(value["id"]),
        snapshot_id=str(value["snapshot_id"]),
        snapshot_version=int(value["snapshot_version"]),
        horizon=Horizon(int(horizon["start"]), int(horizon["end"]), str(horizon["mode"])),
        changes=tuple(
            _change_from_dict(_dict(item, "change"))
            for item in _list(value["changes"], "changes")
        ),
        conflicts=tuple(
            Conflict(str(item["event_id"]), str(item["code"]), str(item["message"]))
            for raw in _list(value.get("conflicts", []), "conflicts")
            for item in [_dict(raw, "conflict")]
        ),
        assumptions=tuple(
            Assumption(str(item["event_id"]), str(item["text"]))
            for raw in _list(value.get("assumptions", []), "assumptions")
            for item in [_dict(raw, "assumption")]
        ),
        explanation=str(value.get("explanation", "")),
    )


def _change_from_dict(value: dict[str, object]) -> Change:
    task = value.get("task")
    reminder = value.get("reminder")
    return Change(
        event_id=str(value["event_id"]),
        request=RequestKind(str(value["request"])),
        task=_task_draft_from_dict(_dict(task, "task draft")) if task else None,
        reminder=(
            _reminder_draft_from_dict(_dict(reminder, "reminder draft"))
            if reminder else None
        ),
        target_id=str(value["target_id"]) if value.get("target_id") else None,
    )


def _task_draft_from_dict(value: dict[str, object]) -> TaskDraft:
    window = value.get("window")
    return TaskDraft(
        str(value["id"]), str(value["title"]), int(value["start"]), int(value["duration"]),
        Window(int(window["start"]), int(window["end"])) if isinstance(window, dict) else None,
        _bool(value.get("fixed", False), "fixed"),
    )


def _reminder_draft_from_dict(value: dict[str, object]) -> ReminderDraft:
    window = value.get("window")
    return ReminderDraft(
        str(value["id"]), str(value["title"]), str(value["trigger"]),
        int(value["at"]) if value.get("at") is not None else None,
        Window(int(window["start"]), int(window["end"])) if isinstance(window, dict) else None,
        str(value.get("delivery", "exact")), int(value.get("priority", 3)),
    )


def _question_from_dict(value: dict[str, object]) -> ClarificationState:
    return ClarificationState(
        field=str(value["field"]),
        question=str(value["question"]),
        options=tuple(str(item) for item in _list(value.get("options", []), "options")),
    )


def _scope_to_dict(value: OperationScope) -> dict[str, object]:
    return {
        "task_ids": list(value.task_ids),
        "reminder_ids": list(value.reminder_ids),
        "time_ranges": [asdict(item) for item in value.time_ranges],
    }


def _scope_from_dict(value: dict[str, object]) -> OperationScope:
    return OperationScope(
        task_ids=tuple(str(item) for item in _list(value.get("task_ids", []), "task_ids")),
        reminder_ids=tuple(
            str(item) for item in _list(value.get("reminder_ids", []), "reminder_ids")
        ),
        time_ranges=tuple(
            _range_from_dict(_dict(item, "time range"))
            for item in _list(value.get("time_ranges", []), "time_ranges")
        ),
    )


def _reference_to_dict(value: TimelineReference) -> dict[str, object]:
    return {"type": value.type, "id": value.id, "start": value.start, "end": value.end}


def _reference_from_dict(value: dict[str, object]) -> TimelineReference:
    return TimelineReference(
        type=str(value["type"]),
        id=str(value["id"]) if value.get("id") is not None else None,
        start=int(value["start"]) if value.get("start") is not None else None,
        end=int(value["end"]) if value.get("end") is not None else None,
    )


def _range_from_dict(value: dict[str, object]) -> TimeRange:
    return TimeRange(start=int(value["start"]), end=int(value["end"]))


def _dict(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _list(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return value


def _bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _keys(value: dict[str, object], expected: set[str], name: str) -> None:
    missing = expected - value.keys()
    unknown = value.keys() - expected
    if missing:
        raise ValueError(f"{name} missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise ValueError(f"{name} has unknown fields: {', '.join(sorted(unknown))}")
