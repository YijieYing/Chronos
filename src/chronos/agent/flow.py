"""Forward-only orchestration of the canonical Agent pipeline."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from chronos.agent.interpreter import Interpreter
from chronos.agent.log_service import ChronosLogService
from chronos.agent.lowerer import Lowerer
from chronos.agent.meaning import Answer, Snapshot
from chronos.agent.models import (
    AgentOperation,
    ClarificationState,
    CreateReminderOperation,
    CreateTaskOperation,
    IntentSnapshot,
    LogEventType,
    OperationScope,
    OperationState,
    ProposalSnapshot,
    TimelineOperation,
    TimeRange,
)
from chronos.agent.parser import Parser
from chronos.agent.plan import Plan, Window
from chronos.agent.planner import Planner, PlanningError
from chronos.agent.service import OperationStore
from chronos.agent.state import State
from chronos.schedule.service import ScheduleService


class Flow:
    """Own lifecycle wiring; each compiler stage keeps its narrower contract."""

    def __init__(
        self,
        interpreter: Interpreter,
        operations: OperationStore,
        schedule: ScheduleService,
        chronos_log: ChronosLogService | None = None,
        *,
        parser: Parser | None = None,
        planner: Planner | None = None,
        lowerer: Lowerer | None = None,
    ) -> None:
        self._parser = parser or Parser()
        self._interpreter = interpreter
        self._planner = planner or Planner()
        self._lowerer = lowerer or Lowerer()
        self._operations = operations
        self._schedule = schedule
        self._log = chronos_log

    def submit(self, text: str, now: int, *, prompt_id: str | None = None) -> AgentOperation:
        prompt_id = prompt_id or str(uuid4())
        parsed = self._parser.parse(prompt_id, text)
        if parsed.boundary is not None:
            raise ValueError(parsed.boundary.question)
        snapshot = self._interpreter.interpret(parsed.items)
        operation = self._build(snapshot, text, now)
        self._operations.create_snapshot(operation)
        self._write(operation, text, created=True)
        return operation

    def clarify(
        self,
        operation_id: str,
        *,
        item_id: str,
        question_id: str,
        answer: str,
        now: int,
    ) -> AgentOperation:
        current = self._operations.get(operation_id)
        if current.state != OperationState.AWAITING_CLARIFICATION or current.snapshot is None:
            raise ValueError("Operation is not awaiting semantic clarification")
        if not any(item.field == question_id for item in current.unresolved_questions):
            raise ValueError("clarification question is stale")
        answered = replace(
            current.snapshot,
            answers=(*current.snapshot.answers, Answer(str(uuid4()), item_id, question_id, answer)),
        )
        snapshot = self._interpreter.interpret(answered.items, answered)
        refreshed = self._build(
            snapshot,
            current.intent.source_text or "",
            now,
            operation_id=current.id,
            created_at=current.created_at,
            version=current.version + 1,
        )
        self._operations.save_snapshot(refreshed, expected_version=current.version)
        self._write(refreshed, answer, created=False)
        return refreshed

    def _build(
        self,
        snapshot: Snapshot,
        source: str,
        now: int,
        *,
        operation_id: str | None = None,
        created_at: datetime | None = None,
        version: int = 1,
    ) -> AgentOperation:
        timestamp = datetime.now(UTC)
        questions = _questions(snapshot)
        plan: Plan | None = None
        executable: tuple[TimelineOperation, ...] = ()
        state = OperationState.AWAITING_CLARIFICATION if questions else OperationState.PROPOSED
        failure: str | None = None
        if not questions:
            try:
                plan = self._planner.plan(
                    snapshot,
                    State(now, self._schedule.settings()["timezone"]),
                    _occupied(self._schedule),
                )
                executable = self._lowerer.lower(plan)
            except PlanningError as error:
                state = OperationState.FAILED
                failure = str(error)
        operation_id = operation_id or str(uuid4())
        summary = (
            plan.explanation if plan is not None
            else " ".join(item.question for item in questions)
            if questions else failure or "Chronos 无法规划此请求。"
        )
        return AgentOperation(
            id=operation_id,
            state=state,
            intent=IntentSnapshot(
                "semantic",
                summary,
                source,
                {
                    "deprecated": "compatibility summary; Snapshot is semantic truth",
                    "planning_mode": plan.horizon.mode if plan else "prospective",
                    "planning_horizon_start": plan.horizon.start if plan else now,
                },
            ),
            unresolved_questions=questions,
            compiled_operations=executable,
            projections=(),
            references=(),
            scope=_scope(executable),
            ambiguity=0.5 if questions else 0,
            risk=0.1 if executable else 0,
            impact=min(1, len(executable) * 0.2),
            reversible=True,
            required_autonomy_level=1,
            created_at=created_at or timestamp,
            updated_at=timestamp,
            version=version,
            proposal=(
                ProposalSnapshot(operation_id, version, timestamp, summary, plan.id)
                if plan is not None else None
            ),
            failure_reason=failure,
            snapshot=snapshot,
            plan=plan,
        )

    def _write(self, operation: AgentOperation, text: str, *, created: bool) -> None:
        if self._log is None:
            return
        self._log.append(
            LogEventType.USER_PROMPT if created else LogEventType.CLARIFICATION_ANSWERED,
            text,
            operation_id=operation.id,
        )
        event = {
            OperationState.AWAITING_CLARIFICATION: LogEventType.CLARIFICATION_REQUESTED,
            OperationState.PROPOSED: LogEventType.PROPOSAL_CREATED,
            OperationState.FAILED: LogEventType.OPERATION_FAILED,
        }[operation.state]
        self._log.append(event, operation.intent.summary, operation_id=operation.id)


def _questions(snapshot: Snapshot) -> tuple[ClarificationState, ...]:
    return tuple(
        ClarificationState(f"{gap.item_id}:{gap.field}", gap.question, gap.candidates)
        for event in snapshot.events
        for gap in event.gaps
    )


def _occupied(schedule: ScheduleService) -> tuple[Window, ...]:
    return tuple(
        Window(
            int(task.preferred_start.timestamp() * 1000),
            int(task.preferred_start.timestamp() * 1000) + task.estimated_minutes * 60_000,
        )
        for task in schedule.list_tasks()
        if task.preferred_start is not None
    )


def _scope(operations: tuple[TimelineOperation, ...]) -> OperationScope:
    task_ids: list[str] = []
    reminder_ids: list[str] = []
    ranges: list[TimeRange] = []
    for operation in operations:
        if isinstance(operation, CreateTaskOperation):
            task_ids.append(operation.task_id)
            ranges.append(TimeRange(
                operation.task.start,
                operation.task.start + operation.task.duration_minutes * 60_000,
            ))
        elif isinstance(operation, CreateReminderOperation):
            reminder_ids.append(operation.reminder_id)
            if operation.reminder.window is not None:
                ranges.append(operation.reminder.window)
    return OperationScope(tuple(task_ids), tuple(reminder_ids), tuple(ranges))
