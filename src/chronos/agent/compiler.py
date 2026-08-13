"""Chronos Compiler port, strict results, and legacy Proposal compatibility adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from chronos.agent.legacy_log import proposal_references
from chronos.agent.models import (
    AgentOperation,
    ClarificationState,
    CreateReminderOperation,
    CreateTaskOperation,
    DeleteTaskOperation,
    InteractionContext,
    IntentSnapshot,
    OperationScope,
    OperationState,
    ProposalSnapshot,
    RecurrenceSpec,
    ReminderSpec,
    TaskSpec,
    TimelineOperation,
    TimelineProjection,
    TimeRange,
    UpdateTaskOperation,
)
from chronos.agent.projection_service import proposal_projections
from chronos.agent.serialization import operation_from_dict, operation_to_dict

COMPILER_RESULT_SCHEMA_VERSION = 1


class CompilerOutcome(StrEnum):
    CLARIFICATION = "clarification"
    PROPOSAL = "proposal"
    INFORMATIONAL = "informational"


@dataclass(frozen=True, slots=True)
class ClarificationCompilerResult:
    operation: AgentOperation
    message: str
    context_used: tuple[dict[str, object], ...] = ()
    warnings: tuple[str, ...] = ()
    outcome: CompilerOutcome = CompilerOutcome.CLARIFICATION

    def __post_init__(self) -> None:
        if self.operation.state != OperationState.AWAITING_CLARIFICATION:
            raise ValueError("clarification result requires awaiting_clarification Operation")
        _validate_result(self.message, self.context_used)


@dataclass(frozen=True, slots=True)
class ProposalCompilerResult:
    operation: AgentOperation
    message: str
    context_used: tuple[dict[str, object], ...] = ()
    warnings: tuple[str, ...] = ()
    outcome: CompilerOutcome = CompilerOutcome.PROPOSAL

    def __post_init__(self) -> None:
        if self.operation.state != OperationState.PROPOSED:
            raise ValueError("proposal result requires proposed Operation")
        _validate_result(self.message, self.context_used)


@dataclass(frozen=True, slots=True)
class InformationalCompilerResult:
    operation: AgentOperation
    message: str
    context_used: tuple[dict[str, object], ...] = ()
    warnings: tuple[str, ...] = ()
    outcome: CompilerOutcome = CompilerOutcome.INFORMATIONAL

    def __post_init__(self) -> None:
        if self.operation.state != OperationState.COMPLETED:
            raise ValueError("informational result requires completed Operation")
        _validate_result(self.message, self.context_used)


type CompilerResult = (
    ClarificationCompilerResult
    | ProposalCompilerResult
    | InformationalCompilerResult
)


class ChronosCompiler(Protocol):
    def compile(self, context: InteractionContext) -> CompilerResult: ...


class LegacyProposalCompiler:
    """Translate one already-planned legacy Proposal into strict Chronos IR.

    The adapter is intentionally pure: its only input is InteractionContext and it does not own
    parser, planner, repository, or Runtime dependencies.
    """

    def compile(self, context: InteractionContext) -> CompilerResult:
        proposal = context.timeline_context.get("legacy_proposal")
        if not isinstance(proposal, dict):
            raise ValueError("legacy compiler requires timeline_context.legacy_proposal")
        operation = _operation_from_proposal(proposal, context)
        message = _message(proposal)
        common = {
            "operation": operation,
            "message": message,
            "context_used": tuple(
                dict(item)
                for item in proposal.get("context_used", [])
                if isinstance(item, dict)
            ),
            "warnings": tuple(str(item) for item in proposal.get("parser_warnings", [])),
        }
        if operation.state == OperationState.AWAITING_CLARIFICATION:
            return ClarificationCompilerResult(**common)
        if operation.state == OperationState.PROPOSED:
            return ProposalCompilerResult(**common)
        return InformationalCompilerResult(**common)


def compiler_result_to_dict(result: CompilerResult) -> dict[str, object]:
    return {
        "schema_version": COMPILER_RESULT_SCHEMA_VERSION,
        "outcome": result.outcome.value,
        "operation": operation_to_dict(result.operation),
        "message": result.message,
        "context_used": [dict(item) for item in result.context_used],
        "warnings": list(result.warnings),
    }


def compiler_result_from_dict(payload: dict[str, object]) -> CompilerResult:
    expected = {
        "schema_version",
        "outcome",
        "operation",
        "message",
        "context_used",
        "warnings",
    }
    if set(payload) != expected:
        raise ValueError("compiler result fields do not match schema")
    if payload["schema_version"] != COMPILER_RESULT_SCHEMA_VERSION:
        raise ValueError("unsupported CompilerResult schema version")
    operation_payload = payload["operation"]
    if not isinstance(operation_payload, dict):
        raise ValueError("compiler result operation must be an object")
    context_used = payload["context_used"]
    warnings = payload["warnings"]
    if not isinstance(context_used, list) or not all(
        isinstance(item, dict) for item in context_used
    ):
        raise ValueError("compiler result context_used must contain objects")
    if not isinstance(warnings, list):
        raise ValueError("compiler result warnings must be an array")
    values = {
        "operation": operation_from_dict(operation_payload),
        "message": str(payload["message"]),
        "context_used": tuple(dict(item) for item in context_used),
        "warnings": tuple(str(item) for item in warnings),
    }
    outcome = CompilerOutcome(str(payload["outcome"]))
    constructors = {
        CompilerOutcome.CLARIFICATION: ClarificationCompilerResult,
        CompilerOutcome.PROPOSAL: ProposalCompilerResult,
        CompilerOutcome.INFORMATIONAL: InformationalCompilerResult,
    }
    return constructors[outcome](**values)


def _operation_from_proposal(
    proposal: dict[str, object], context: InteractionContext
) -> AgentOperation:
    operation_id = str(proposal["proposal_id"])
    status = str(proposal.get("status", "informational"))
    operations = _timeline_operations(proposal)
    questions = tuple(
        ClarificationState(str(item["field"]), str(item["question"]))
        for item in proposal.get("clarifications", [])
        if isinstance(item, dict)
    )
    state = {
        "needs_clarification": OperationState.AWAITING_CLARIFICATION,
        "pending": OperationState.PROPOSED,
    }.get(status, OperationState.COMPLETED)
    projections = tuple(
        _owned_projection(item) for item in proposal_projections(proposal)
    )
    references = proposal_references(proposal)
    now = datetime.fromtimestamp(context.current_time / 1000, UTC)
    intent_kind = _intent_kind(proposal)
    proposal_snapshot = (
        ProposalSnapshot(
            operation_id=operation_id,
            version=1,
            operations=operations,
            explanation=_message(proposal),
            created_at=now,
        )
        if state == OperationState.PROPOSED
        else None
    )
    return AgentOperation(
        id=operation_id,
        state=state,
        intent=IntentSnapshot(
            intent_kind,
            _message(proposal),
            context.user_input,
            attributes={"compiler": "legacy_proposal", "parser_mode": proposal.get("parser_mode")},
        ),
        unresolved_questions=questions,
        compiled_operations=operations,
        projections=projections,
        references=references,
        scope=_scope(operations, projections),
        ambiguity=0.8 if questions else 0.1,
        risk=0.15 if operations else 0,
        impact=min(1, len(operations) * 0.2),
        reversible=True,
        required_autonomy_level=0,
        created_at=now,
        updated_at=now,
        version=1,
        proposal=proposal_snapshot,
    )


def _timeline_operations(proposal: dict[str, object]) -> tuple[TimelineOperation, ...]:
    reminder_operations = _reminder_operations(proposal)
    if reminder_operations:
        return reminder_operations
    command = proposal.get("command")
    commands = proposal.get("commands")
    command_values = commands if isinstance(commands, list) and commands else [command]
    operations: list[TimelineOperation] = []
    for item in command_values:
        if not isinstance(item, dict):
            continue
        command_type = str(item.get("type", ""))
        task_id = str(item.get("task_id") or _task_id(item.get("after")))
        if command_type == "delete_task":
            operations.append(DeleteTaskOperation(task_id))
        elif command_type in {"create_task", "update_task"}:
            after = item.get("after")
            if not isinstance(after, dict):
                continue
            task = _task_spec(after)
            constructor = (
                CreateTaskOperation
                if command_type == "create_task"
                else UpdateTaskOperation
            )
            operations.append(constructor(task_id, task))
    return tuple(operations)


def _reminder_operations(proposal: dict[str, object]) -> tuple[TimelineOperation, ...]:
    operations: list[TimelineOperation] = []
    for draft in proposal.get("reminder_drafts", []):
        reminder = draft.get("reminder") if isinstance(draft, dict) else None
        trigger = reminder.get("trigger") if isinstance(reminder, dict) else None
        if not isinstance(reminder, dict) or not isinstance(trigger, dict):
            continue
        trigger_type = str(trigger["type"])
        spec = ReminderSpec(
            title=str(reminder["title"]),
            trigger_type=trigger_type,
            at=int(trigger["at"]) if trigger_type == "time" else None,
            window=(
                TimeRange(int(trigger["start"]), int(trigger["end"]))
                if trigger_type == "window"
                else None
            ),
            delivery=str(reminder["delivery"]),
            priority=int(reminder["priority"]),
            prefer_interruptible_moment=str(reminder["delivery"]) == "context-aware",
        )
        operations.append(CreateReminderOperation(str(reminder["id"]), spec))
    return tuple(operations)


def _task_spec(value: dict[str, object]) -> TaskSpec:
    recurrence_value = value.get("recurrence")
    recurrence = None
    if isinstance(recurrence_value, dict):
        recurrence = RecurrenceSpec(
            frequency=str(recurrence_value["frequency"]),
            weekdays=tuple(int(item) for item in recurrence_value.get("weekdays", [])),
            until=(str(recurrence_value["until"]) if recurrence_value.get("until") else None),
        )
    start = int(datetime.fromisoformat(str(value["preferred_start"])).timestamp() * 1000)
    return TaskSpec(
        title=str(value["title"]),
        start=start,
        duration_minutes=int(value["estimated_minutes"]),
        task_type=str(value.get("task_type", "execution")),
        fixed=bool(value.get("fixed", False)),
        recurrence=recurrence,
    )


def _scope(
    operations: tuple[TimelineOperation, ...],
    projections: tuple[TimelineProjection, ...],
) -> OperationScope:
    task_ids = tuple(dict.fromkeys(
        str(getattr(item, "task_id"))
        for item in operations
        if hasattr(item, "task_id")
    ))
    reminder_ids = tuple(dict.fromkeys(
        str(getattr(item, "reminder_id"))
        for item in operations
        if hasattr(item, "reminder_id")
    ))
    ranges = tuple(
        TimeRange(item.start, item.end)
        for item in projections
        if item.start is not None and item.end is not None
    )
    return OperationScope(task_ids, reminder_ids, ranges)


def _owned_projection(projection: TimelineProjection) -> TimelineProjection:
    metadata = dict(projection.metadata)
    metadata.pop("legacy_adapter", None)
    return TimelineProjection(
        projection.id.replace("legacy:", "compiler:", 1),
        projection.operation_id,
        projection.type,
        projection.target,
        projection.visual_state,
        projection.start,
        projection.end,
        {"compiler": "legacy_proposal", **metadata},
    )


def _task_id(value: object) -> str:
    return str(value.get("task_id")) if isinstance(value, dict) else ""


def _intent_kind(proposal: dict[str, object]) -> str:
    command = proposal.get("command")
    if isinstance(command, dict) and command.get("type"):
        return str(command["type"])
    if proposal.get("reminder_drafts"):
        return "create_reminder"
    if proposal.get("commands"):
        return "create_schedule"
    return "informational"


def _message(proposal: dict[str, object]) -> str:
    explanation = proposal.get("explanation", [])
    message = " ".join(str(item) for item in explanation) if isinstance(explanation, list) else ""
    return message or "Chronos 已处理请求。"


def _validate_result(message: str, context_used: tuple[dict[str, object], ...]) -> None:
    if not message.strip() or any(not isinstance(item, dict) for item in context_used):
        raise ValueError("CompilerResult requires message and object context entries")
