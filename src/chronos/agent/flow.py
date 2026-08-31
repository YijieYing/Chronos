"""Forward-only orchestration of the canonical Agent pipeline."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from collections.abc import Callable, Mapping
from uuid import NAMESPACE_URL, uuid4, uuid5

from chronos.agent.interpreter import Interpreter
from chronos.agent.log_service import ChronosLogService
from chronos.agent.lowerer import Lowerer
from chronos.agent.meaning import (
    Answer,
    Content,
    Directive,
    DirectiveKind,
    Item,
    Object,
    Recurrence,
    Reference,
    Residue,
    ResidueReason,
    Snapshot,
    Time,
)
from chronos.agent.models import (
    AgentOperation,
    ClarificationState,
    CreateReminderOperation,
    CreateTaskOperation,
    DeleteReminderOperation,
    DeleteTaskOperation,
    IntentSnapshot,
    LogEventType,
    OperationScope,
    OperationState,
    ProjectionKind,
    ProjectionVisualState,
    ProposalSnapshot,
    MoveReminderOperation,
    MoveTaskOperation,
    ResizeTaskOperation,
    TimelineProjection,
    TimelineReference,
    TimelineOperation,
    TimeRange,
    UpdateReminderOperation,
    UpdateTaskOperation,
)
from chronos.agent.parser import Parser
from chronos.agent.plan import Plan, ReminderDraft, TaskDraft, Window
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
        reminder_ids: Callable[[], tuple[str, ...]] | None = None,
        reminder_drafts: Callable[[], Mapping[str, ReminderDraft]] | None = None,
    ) -> None:
        self._parser = parser or Parser()
        self._interpreter = interpreter
        self._planner = planner or Planner()
        self._lowerer = lowerer or Lowerer()
        self._operations = operations
        self._schedule = schedule
        self._log = chronos_log
        self._reminder_ids = reminder_ids
        self._reminder_drafts = reminder_drafts

    def submit(
        self,
        text: str,
        now: int,
        *,
        prompt_id: str | None = None,
        selection: Reference | Time | None = None,
    ) -> AgentOperation:
        prompt_id = prompt_id or str(uuid4())
        parsed = self._parser.parse(prompt_id, text)
        if parsed.boundary is not None:
            raise ValueError(parsed.boundary.question)
        timezone = self._schedule.settings()["timezone"]
        try:
            snapshot = self._interpreter.interpret(
                parsed.items,
                selection=selection,
                objects=self._objects(),
                now=now,
                timezone=timezone,
            )
        except Exception as error:
            operation = self._technical_failure(parsed.items, text, now, str(error))
            self._operations.create_snapshot(operation)
            self._write(operation, text, created=True)
            return operation
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
        selection: Reference | Time | None = None,
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
        timezone = self._schedule.settings()["timezone"]
        try:
            snapshot = self._interpreter.interpret(
                answered.items,
                answered,
                selection=selection,
                objects=self._objects(),
                now=now,
                timezone=timezone,
            )
        except Exception as error:
            failed = replace(
                current,
                state=OperationState.FAILED,
                updated_at=datetime.now(UTC),
                version=current.version + 1,
                failure_reason=f"Interpreter provider failed: {error}",
            )
            self._operations.save_snapshot(failed, expected_version=current.version)
            self._write(failed, answer, created=False)
            return failed
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

    def _objects(self) -> tuple[Object, ...]:
        tasks = tuple(Object("task", task.task_id, task.title) for task in self._schedule.list_tasks())
        reminders = tuple(
            Object("reminder", reminder.id, reminder.title)
            for reminder in (self._reminder_drafts() or {}).values()
        ) if self._reminder_drafts else ()
        return (*tasks, *reminders)

    def _technical_failure(
        self, items: tuple[Item, ...], source: str, now: int, reason: str
    ) -> AgentOperation:
        timestamp = datetime.now(UTC)
        snapshot_id = str(uuid4())
        directives = tuple(
            Directive(
                str(uuid5(NAMESPACE_URL, f"{snapshot_id}:provider:{item.id}")),
                item.id,
                DirectiveKind.UNKNOWN,
                (Content(item.id, item.span, item.text),),
                residue=(Residue(
                    item.id, item.span, item.text, ResidueReason.LOW_CONFIDENCE,
                    self._interpreter.version, "provider failure",
                ),),
            )
            for item in items
        )
        snapshot = Snapshot(snapshot_id, items[0].prompt_id, 1, items, (), directives, ())
        return AgentOperation(
            id=str(uuid4()),
            state=OperationState.FAILED,
            intent=IntentSnapshot(
                "semantic", f"Interpreter provider failed: {reason}", source,
                {"planning_mode": "prospective", "planning_horizon_start": now},
            ),
            unresolved_questions=(),
            compiled_operations=(),
            projections=(),
            references=(),
            scope=OperationScope(),
            ambiguity=0,
            risk=0,
            impact=0,
            reversible=True,
            required_autonomy_level=1,
            created_at=timestamp,
            updated_at=timestamp,
            version=1,
            failure_reason=f"Interpreter provider failed: {reason}",
            snapshot=snapshot,
        )

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
        directive = snapshot.directives[0] if snapshot.directives and not snapshot.events else None
        state = (
            OperationState.COMPLETED if directive and directive.response
            else OperationState.FAILED if directive
            else OperationState.AWAITING_CLARIFICATION if questions
            else OperationState.PROPOSED
        )
        failure: str | None = (
            "Interpreter 无法识别此请求。" if directive and not directive.response else None
        )
        if not questions and directive is None:
            try:
                plan = self._planner.plan(
                    snapshot,
                    State(now, self._schedule.settings()["timezone"]),
                    _occupied(self._schedule),
                    task_ids=tuple(task.task_id for task in self._schedule.list_tasks()),
                    reminder_ids=self._reminder_ids() if self._reminder_ids else None,
                    task_drafts=_task_drafts(self._schedule),
                    reminder_drafts=(
                        self._reminder_drafts() if self._reminder_drafts else None
                    ),
                )
                if plan.conflicts:
                    state = OperationState.FAILED
                    failure = "；".join(item.message for item in plan.conflicts)
                else:
                    executable = self._lowerer.lower(plan)
            except PlanningError as error:
                state = OperationState.FAILED
                failure = str(error)
        operation_id = operation_id or str(uuid4())
        projections = _projections(operation_id, executable)
        references = tuple(dict.fromkeys(item.target for item in projections))
        summary = (
            plan.explanation if plan is not None
            else directive.response if directive and directive.response
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
            projections=projections,
            references=references,
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
            OperationState.COMPLETED: LogEventType.AGENT_MESSAGE,
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


def _task_drafts(schedule: ScheduleService) -> dict[str, TaskDraft]:
    drafts: dict[str, TaskDraft] = {}
    for task in schedule.list_tasks():
        if task.preferred_start is None:
            continue
        drafts[task.task_id] = TaskDraft(
            task.task_id,
            task.title,
            int(task.preferred_start.timestamp() * 1000),
            task.estimated_minutes,
            fixed=task.fixed,
            priority=task.priority,
            recurrence=(
                Recurrence(
                    str(task.recurrence["frequency"]),
                    tuple(int(item) for item in task.recurrence.get("weekdays", [])),
                    str(task.recurrence["until"]) if task.recurrence.get("until") else None,
                )
                if task.recurrence is not None else None
            ),
        )
    return drafts


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
        elif isinstance(operation, (
            UpdateTaskOperation, MoveTaskOperation, ResizeTaskOperation, DeleteTaskOperation
        )):
            task_ids.append(operation.task_id)
        elif isinstance(operation, (
            UpdateReminderOperation, MoveReminderOperation, DeleteReminderOperation
        )):
            reminder_ids.append(operation.reminder_id)
            if isinstance(operation, MoveReminderOperation) and operation.window is not None:
                ranges.append(operation.window)
    return OperationScope(
        tuple(dict.fromkeys(task_ids)),
        tuple(dict.fromkeys(reminder_ids)),
        tuple(ranges),
    )


def _projections(
    operation_id: str,
    operations: tuple[TimelineOperation, ...],
) -> tuple[TimelineProjection, ...]:
    result: list[TimelineProjection] = []
    for index, operation in enumerate(operations):
        if isinstance(operation, CreateTaskOperation):
            target = TimelineReference("task", id=operation.task_id)
            start = operation.task.start
            end = start + operation.task.duration_minutes * 60_000
            metadata = {"title": operation.task.title, "kind": "task"}
        elif isinstance(operation, CreateReminderOperation):
            target = TimelineReference("reminder", id=operation.reminder_id)
            if operation.reminder.at is not None:
                start = operation.reminder.at
                end = start + 1
            else:
                assert operation.reminder.window is not None
                start = operation.reminder.window.start
                end = operation.reminder.window.end
            metadata = {"title": operation.reminder.title, "kind": "reminder"}
        elif isinstance(operation, MoveTaskOperation):
            target = TimelineReference("task", id=operation.task_id)
            start = operation.start
            end = start + 1
            metadata = {"kind": "task", "change": "move"}
        elif isinstance(operation, UpdateTaskOperation):
            target = TimelineReference("task", id=operation.task_id)
            start = operation.task.start
            end = start + operation.task.duration_minutes * 60_000
            metadata = {"title": operation.task.title, "kind": "task", "change": "update"}
        elif isinstance(operation, ResizeTaskOperation):
            target = TimelineReference("task", id=operation.task_id)
            start = end = None
            metadata = {
                "kind": "task", "change": "resize",
                "duration_minutes": operation.duration_minutes,
            }
        elif isinstance(operation, DeleteTaskOperation):
            target = TimelineReference("task", id=operation.task_id)
            start = end = None
            metadata = {"kind": "task", "change": "delete"}
        elif isinstance(operation, MoveReminderOperation):
            target = TimelineReference("reminder", id=operation.reminder_id)
            if operation.at is not None:
                start = operation.at
                end = start + 1
            else:
                assert operation.window is not None
                start = operation.window.start
                end = operation.window.end
            metadata = {"kind": "reminder", "change": "move"}
        elif isinstance(operation, UpdateReminderOperation):
            target = TimelineReference("reminder", id=operation.reminder_id)
            if operation.reminder.at is not None:
                start = operation.reminder.at
                end = start + 1
            else:
                assert operation.reminder.window is not None
                start = operation.reminder.window.start
                end = operation.reminder.window.end
            metadata = {
                "title": operation.reminder.title,
                "kind": "reminder",
                "change": "update",
            }
        elif isinstance(operation, DeleteReminderOperation):
            target = TimelineReference("reminder", id=operation.reminder_id)
            start = end = None
            metadata = {"kind": "reminder", "change": "delete"}
        else:
            continue
        result.append(TimelineProjection(
            id=str(uuid5(NAMESPACE_URL, f"{operation_id}:projection:{index}")),
            operation_id=operation_id,
            type=ProjectionKind.PROPOSAL,
            target=target,
            visual_state=ProjectionVisualState.PROPOSED,
            start=start,
            end=end,
            metadata=metadata,
        ))
    return tuple(result)
