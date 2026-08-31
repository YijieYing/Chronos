from __future__ import annotations

from datetime import UTC, datetime
from http import HTTPStatus

from chronos.agent.autonomy import evaluate_autonomy, policy_for_level
from chronos.agent.adjustment import AdjustmentCoordinator
from chronos.agent.flow import Flow
from chronos.agent.interpreter import Interpreter
from chronos.agent.legacy_log import proposal_references
from chronos.agent.log_service import ChronosLogService
from chronos.agent.meaning import Reference, Time, TimeKind
from chronos.agent.models import (
    AgentOperation,
    AutonomyPolicy,
    InteractionContext,
    LogEventType,
    OperationScope,
    OperationState,
    TimelineReference,
    TimeRange,
    CreateReminderOperation,
    CreateTaskOperation,
    DeleteReminderOperation,
    DeleteTaskOperation,
    MoveReminderOperation,
    MoveTaskOperation,
    ResizeTaskOperation,
    UpdateReminderOperation,
    UpdateTaskOperation,
)
from chronos.agent.projection_service import ProjectionService
from chronos.agent.plan import ReminderDraft, Window
from chronos.agent.runtime import ChronosRuntime
from chronos.agent.serialization import log_entry_to_dict, projection_to_dict
from chronos.agent.service import OperationStore
from chronos.api.contracts.common import success
from chronos.api.contracts.schedule import scheduled_task_values
from chronos.reminders.models import ReminderStatus
from chronos.reminders.service import ReminderService, reminder_dict
from chronos.schedule.agent_memory import AgentMemoryService
from chronos.schedule.models import Task
from chronos.schedule.proposals import ProposalService
from chronos.schedule.service import ScheduleService, _agenda_dict

RouteResult = tuple[HTTPStatus, dict[str, object]]


def _operation_view(
    operation: AgentOperation, *, status: str | None = None
) -> dict[str, object]:
    """Legacy-shaped UI view derived only from canonical Operation state."""

    proposed_tasks = []
    reminder_drafts = []
    changes = []
    for executable in operation.compiled_operations:
        if isinstance(executable, (CreateTaskOperation, UpdateTaskOperation)):
            proposed_tasks.append({
                "task_id": executable.task_id,
                "title": executable.task.title,
                "estimated_minutes": executable.task.duration_minutes,
                "preferred_start": datetime.fromtimestamp(
                    executable.task.start / 1000, UTC
                ).isoformat(),
                "recurrence": (
                    {
                        "frequency": executable.task.recurrence.frequency,
                        **(
                            {"weekdays": list(executable.task.recurrence.weekdays)}
                            if executable.task.recurrence.weekdays else {}
                        ),
                        **(
                            {"until": executable.task.recurrence.until}
                            if executable.task.recurrence.until else {}
                        ),
                    }
                    if executable.task.recurrence is not None else None
                ),
                "fixed": executable.task.fixed,
            })
            changes.append({
                "operation": "create" if isinstance(executable, CreateTaskOperation) else "update",
                "target_type": "task",
                "target_id": executable.task_id,
                "summary": (
                    f"创建任务「{executable.task.title}」"
                    if isinstance(executable, CreateTaskOperation)
                    else f"更新任务「{executable.task.title}」"
                ),
            })
        elif isinstance(executable, (CreateReminderOperation, UpdateReminderOperation)):
            reminder = executable.reminder
            trigger = (
                {"type": "time", "at": reminder.at}
                if reminder.at is not None else {
                    "type": "window",
                    "start": reminder.window.start,
                    "end": reminder.window.end,
                }
            )
            reminder_drafts.append({"reminder": {
                "id": executable.reminder_id,
                "title": reminder.title,
                "trigger": trigger,
                "delivery": reminder.delivery,
                "priority": reminder.priority,
                "status": "pending",
                "source": "agent",
                "created_at": operation.created_at.isoformat(),
            }})
            changes.append({
                "operation": (
                    "create" if isinstance(executable, CreateReminderOperation) else "update"
                ),
                "target_type": "reminder",
                "target_id": executable.reminder_id,
                "summary": (
                    f"创建提醒「{reminder.title}」"
                    if isinstance(executable, CreateReminderOperation)
                    else f"更新提醒「{reminder.title}」"
                ),
            })
        elif isinstance(executable, MoveTaskOperation):
            changes.append({
                "operation": "move", "target_type": "task",
                "target_id": executable.task_id,
                "summary": f"移动任务至 {datetime.fromtimestamp(executable.start / 1000, UTC).isoformat()}",
            })
        elif isinstance(executable, ResizeTaskOperation):
            changes.append({
                "operation": "resize", "target_type": "task",
                "target_id": executable.task_id,
                "summary": f"调整任务时长为 {executable.duration_minutes} 分钟",
            })
        elif isinstance(executable, DeleteTaskOperation):
            changes.append({
                "operation": "delete", "target_type": "task",
                "target_id": executable.task_id, "summary": "删除任务",
            })
        elif isinstance(executable, MoveReminderOperation):
            changes.append({
                "operation": "move", "target_type": "reminder",
                "target_id": executable.reminder_id, "summary": "调整提醒时间",
            })
        elif isinstance(executable, DeleteReminderOperation):
            changes.append({
                "operation": "delete", "target_type": "reminder",
                "target_id": executable.reminder_id, "summary": "删除提醒",
            })
    mapped = {
        OperationState.AWAITING_CLARIFICATION: "needs_clarification",
        OperationState.PROPOSED: "pending",
        OperationState.COMPLETED: "accepted",
        OperationState.REJECTED: "rejected",
        OperationState.FAILED: "failed",
        OperationState.STALE: "stale",
    }
    return {
        "proposal_id": operation.id,
        "status": status or mapped.get(operation.state, operation.state.value),
        "requires_confirmation": operation.state == OperationState.PROPOSED,
        "read_only": False,
        "source": "canonical",
        "request_text": operation.intent.source_text or "",
        "proposed_task": None,
        "proposed_tasks": proposed_tasks,
        "results": [],
        "changes": changes,
        "conflicts": [
            {
                "event_id": item.event_id,
                "code": item.code,
                "message": item.message,
            }
            for item in operation.plan.conflicts
        ] if operation.plan else [],
        "explanation": [operation.intent.summary],
        "parser_mode": "semantic",
        "parser_warnings": [],
        "clarifications": [
            {"field": item.field, "question": item.question, "options": list(item.options)}
            for item in operation.unresolved_questions
        ],
        "assumptions": [item.text for item in operation.plan.assumptions]
        if operation.plan else [],
        "reminder_drafts": reminder_drafts,
        "created_at": operation.created_at.isoformat(),
        "updated_at": operation.updated_at.isoformat(),
    }


class V1Router:
    def __init__(
        self,
        schedule: ScheduleService,
        proposals: ProposalService,
        agent_memory: AgentMemoryService | None = None,
        reminders: ReminderService | None = None,
        chronos_log: ChronosLogService | None = None,
        operation_store: OperationStore | None = None,
        projections: ProjectionService | None = None,
        compiler: object | None = None,
        runtime: ChronosRuntime | None = None,
        adjustments: AdjustmentCoordinator | None = None,
        flow: Flow | None = None,
    ) -> None:
        self._schedule = schedule
        self._proposals = proposals
        self._agent_memory = agent_memory
        self._reminders = reminders
        self._chronos_log = chronos_log
        self._operation_store = operation_store
        self._projections = projections
        self._runtime = runtime
        self._adjustments = adjustments
        self._flow = flow or (
            Flow(
                Interpreter(),
                operation_store,
                schedule,
                chronos_log,
                reminder_ids=(
                    lambda: tuple(str(item["id"]) for item in reminders.list())
                    if reminders is not None else None
                ),
                reminder_drafts=(
                    lambda: _canonical_reminders(reminders)
                    if reminders is not None else None
                ),
            )
            if operation_store is not None else None
        )

    def dispatch(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> RouteResult | None:
        if not path.startswith("/api/v1/"):
            return None
        payload = payload or {}
        if method == "GET" and path == "/api/v1/replan-signals":
            operations = self._adjustments.list_captured() if self._adjustments else ()
            return HTTPStatus.OK, success(
                {
                    "proactive_enabled": False,
                    "signals": [
                        {
                            "operation_id": operation.id,
                            "state": operation.state.value,
                            "summary": operation.intent.summary,
                            **dict(operation.intent.attributes),
                            "references": [
                                {
                                    "type": reference.type,
                                    "id": reference.id,
                                    "start": reference.start,
                                    "end": reference.end,
                                }
                                for reference in operation.references
                            ],
                            "detected_at": operation.updated_at.isoformat(),
                        }
                        for operation in operations
                    ],
                }
            )
        if method == "GET" and path == "/api/v1/timeline-projections":
            if self._projections is None:
                return HTTPStatus.OK, success({"projections": []})
            projections = self._projections.list_active()
            return HTTPStatus.OK, success(
                {"projections": [projection_to_dict(item) for item in projections]}
            )
        if path == "/api/v1/agent/autonomy":
            if method == "GET":
                level = int(self._schedule.settings()["autonomy_level"])
                policy = policy_for_level(level)
                return HTTPStatus.OK, success(_autonomy_dict(policy))
            if method == "PUT":
                level = int(payload["level"])
                policy = policy_for_level(level)
                self._schedule.update_settings({"autonomy_level": str(level)})
                return HTTPStatus.OK, success(_autonomy_dict(policy))
        if self._chronos_log is not None and path == "/api/v1/chronos-log":
            if method == "GET":
                entries = [log_entry_to_dict(item) for item in self._chronos_log.list()]
                pending = (
                    [
                        item for item in self._operation_store.pending()
                        if item.snapshot is not None
                        and item.state in {
                            OperationState.AWAITING_CLARIFICATION,
                            OperationState.PROPOSED,
                        }
                    ]
                    if self._operation_store is not None
                    else []
                )
                pending_operations = [
                        {
                            "id": item.id,
                            "state": item.state.value,
                            "summary": item.intent.summary,
                            "questions": [
                                {
                                    "field": question.field,
                                    "question": question.question,
                                    "options": list(question.options),
                                }
                                for question in item.unresolved_questions
                            ],
                            "created_at": item.created_at.isoformat(),
                        }
                        for item in pending
                    ]
                return HTTPStatus.OK, success(
                    {
                        "entries": entries,
                        "pending_count": len(pending),
                        "pending_operations": pending_operations,
                    }
                )
            if method == "POST":
                entry = self._chronos_log.append(
                    LogEventType(str(payload["event_type"])),
                    str(payload["message"]),
                    operation_id=(
                        str(payload["operation_id"])
                        if payload.get("operation_id") is not None
                        else None
                    ),
                    references=_timeline_references(payload.get("references", [])),
                    metadata=(
                        dict(payload["metadata"])
                        if isinstance(payload.get("metadata"), dict)
                        else {}
                    ),
                )
                return HTTPStatus.CREATED, success(log_entry_to_dict(entry))
        if self._reminders is not None:
            if path == "/api/v1/reminders":
                if method == "GET":
                    return HTTPStatus.OK, success({"reminders": self._reminders.list()})
                if method == "POST":
                    reminder = self._reminders.create(**_reminder_values(payload))
                    stored = reminder_dict(reminder)
                    self._timeline_changed(_reminder_scope(stored))
                    return HTTPStatus.CREATED, success(stored)
            reminder_prefix = "/api/v1/reminders/"
            if path.startswith(reminder_prefix):
                reminder_id = path.removeprefix(reminder_prefix)
                if method == "PUT":
                    reminder = self._reminders.set_status(
                        reminder_id, ReminderStatus(str(payload["status"]))
                    )
                    return HTTPStatus.OK, success(reminder_dict(reminder))
                if method == "DELETE":
                    previous = self._reminders.get(reminder_id)
                    if not self._reminders.delete(reminder_id):
                        raise KeyError(reminder_id)
                    self._timeline_changed(_reminder_scope(previous))
                    return HTTPStatus.OK, success({"deleted": True, "id": reminder_id})
        if self._agent_memory is not None:
            if method == "GET" and path == "/api/v1/agent/imports":
                return HTTPStatus.OK, success({"imports": self._agent_memory.list_imports()})
            if method == "GET" and path == "/api/v1/agent/memory/candidates":
                return HTTPStatus.OK, success(
                    {"candidates": self._agent_memory.list_candidates()}
                )
            if method == "GET" and path == "/api/v1/agent/memory/items":
                return HTTPStatus.OK, success({"items": self._agent_memory.list_context()})
            item_prefix = "/api/v1/agent/memory/items/"
            if path.startswith(item_prefix):
                context_id = path.removeprefix(item_prefix)
                if method == "PUT":
                    return HTTPStatus.OK, success(
                        self._agent_memory.update_context(
                            context_id,
                            content=str(payload.get("content", "")),
                            category=(
                                str(payload["category"])
                                if payload.get("category") is not None
                                else None
                            ),
                        )
                    )
                if method == "DELETE":
                    if not self._agent_memory.delete_context(context_id):
                        raise KeyError(context_id)
                    return HTTPStatus.OK, success(
                        {"deleted": True, "context_id": context_id}
                    )
            candidate_prefix = "/api/v1/agent/memory/candidates/"
            if method == "POST" and path.startswith(candidate_prefix):
                suffix = path.removeprefix(candidate_prefix)
                if suffix.endswith("/accept"):
                    candidate_id = suffix.removesuffix("/accept")
                    return HTTPStatus.OK, success(
                        self._agent_memory.review(candidate_id, True)
                    )
                if suffix.endswith("/ignore"):
                    candidate_id = suffix.removesuffix("/ignore")
                    return HTTPStatus.OK, success(
                        self._agent_memory.review(candidate_id, False)
                    )
        if method == "GET" and path == "/api/v1/schedule/timeline":
            return HTTPStatus.OK, success(self._schedule.timeline())
        if method == "POST" and path == "/api/v1/schedule/tasks":
            values = scheduled_task_values(
                payload, self._schedule.settings()["timezone"], task_id=str(payload["id"])
            )
            task, plan = self._schedule.create_scheduled_task(**values)
            self._timeline_changed(_task_scope(task))
            return HTTPStatus.CREATED, success(
                {
                    "task_id": task.task_id,
                    "plan": _agenda_dict(plan),
                    "timeline": self._schedule.timeline(),
                }
            )
        task_prefix = "/api/v1/schedule/tasks/"
        if path.startswith(task_prefix):
            task_id = path.removeprefix(task_prefix)
            if method == "PUT":
                if str(payload.get("id", "")) != task_id:
                    raise ValueError("task id must match request path")
                values = scheduled_task_values(
                    payload, self._schedule.settings()["timezone"]
                )
                previous = self._schedule.get_task(task_id)
                task, plan = self._schedule.update_scheduled_task(task_id, **values)
                self._timeline_changed(_task_scope(previous, task))
                return HTTPStatus.OK, success(
                    {
                        "task_id": task.task_id,
                        "plan": _agenda_dict(plan),
                        "timeline": self._schedule.timeline(),
                    }
                )
            if method == "DELETE":
                previous = self._schedule.get_task(task_id)
                if not self._schedule.delete_scheduled_task(task_id):
                    raise KeyError(task_id)
                self._timeline_changed(_task_scope(previous))
                return HTTPStatus.OK, success({"deleted": True, "task_id": task_id})

        if method == "GET" and path == "/api/v1/proposals":
            canonical = (
                [_operation_view(item) for item in self._operation_store.list() if item.snapshot]
                if self._operation_store is not None else []
            )
            canonical_ids = {str(item["proposal_id"]) for item in canonical}
            legacy = [
                _legacy_view(item) for item in self._proposals.list()
                if str(item["proposal_id"]) not in canonical_ids
            ]
            return HTTPStatus.OK, success({"proposals": [*canonical, *legacy]})
        if method == "POST" and path == "/api/v1/proposals":
            text = str(payload.get("text", ""))
            context = _interaction_context(payload.get("interaction_context"))
            if self._flow is not None:
                selection = _semantic_selection(context.selection)
                operation = self._flow.submit(
                    text, context.current_time, selection=selection
                )
                proposal = self._apply_autonomy(_operation_view(operation))
                return HTTPStatus.CREATED, success(proposal)
            raise RuntimeError("canonical Flow is required for Agent writes")
        proposal_prefix = "/api/v1/proposals/"
        if path.startswith(proposal_prefix):
            suffix = path.removeprefix(proposal_prefix)
            if method == "GET" and "/" not in suffix:
                if self._operation_store is not None:
                    try:
                        operation = self._operation_store.get(suffix)
                        if operation.snapshot is not None:
                            return HTTPStatus.OK, success(_operation_view(operation))
                    except KeyError:
                        pass
                return HTTPStatus.OK, success(_legacy_view(self._proposals.get(suffix)))
            if method == "POST" and suffix.endswith("/accept"):
                proposal_id = suffix.removesuffix("/accept")
                changed_scope = (
                    self._operation_store.get(proposal_id).scope
                    if self._operation_store is not None
                    else None
                )
                if self._runtime is not None:
                    operation = (
                        self._operation_store.get(proposal_id)
                        if self._operation_store is not None else None
                    )
                    if operation is not None and operation.snapshot is not None:
                        transaction = self._runtime.execute(
                            operation, operation.compiled_operations
                        )
                        proposal = _operation_view(
                            self._operation_store.get(proposal_id), status="accepted"
                        )
                    else:
                        raise ValueError("legacy proposals are read-only")
                else:
                    raise RuntimeError("Runtime is required for Agent writes")
                if changed_scope is not None:
                    self._timeline_changed(
                        changed_scope, exclude_operation_id=proposal_id
                    )
                self._log_proposal_event(
                    proposal,
                    LogEventType.OPERATION_COMPLETED,
                    "已应用时间轴调整。",
                    {"transaction_id": transaction.id} if transaction else None,
                )
                return HTTPStatus.OK, success(proposal)
            if method == "POST" and suffix.endswith("/reject"):
                proposal_id = suffix.removesuffix("/reject")
                current = self._operation_store.get(proposal_id) if self._operation_store else None
                if current is not None and current.snapshot is not None:
                    rejected = self._operation_store.transition(
                        proposal_id, OperationState.REJECTED, expected_version=current.version
                    )
                    proposal = _operation_view(rejected)
                else:
                    raise ValueError("legacy proposals are read-only")
                self._log_proposal_event(
                    proposal, LogEventType.OPERATION_REJECTED, "已拒绝时间轴提案。"
                )
                return HTTPStatus.OK, success(proposal)
            if method == "POST" and suffix.endswith("/restore"):
                proposal_id = suffix.removesuffix("/restore")
                changed_scope = (
                    self._operation_store.get(proposal_id).scope
                    if self._operation_store is not None
                    else None
                )
                current = self._operation_store.get(proposal_id) if self._operation_store else None
                if self._runtime is not None and current is not None and current.snapshot is not None:
                    self._runtime.revert(proposal_id)
                    proposal = _operation_view(current, status="restored")
                else:
                    raise ValueError("legacy proposals are read-only")
                if changed_scope is not None:
                    self._timeline_changed(
                        changed_scope, exclude_operation_id=proposal_id
                    )
                self._log_proposal_event(
                    proposal, LogEventType.UNDO, "已恢复此次调整前的状态。"
                )
                return HTTPStatus.OK, success(proposal)
        operation_prefix = "/api/v1/operations/"
        if method == "POST" and path.startswith(operation_prefix) and path.endswith("/clarify"):
            operation_id = path.removeprefix(operation_prefix).removesuffix("/clarify")
            return HTTPStatus.OK, success(
                self._answer_clarification(operation_id, payload)
            )
        raise KeyError(path)

    def _answer_clarification(
        self, operation_id: str, payload: dict[str, object]
    ) -> dict[str, object]:
        if self._operation_store is None:
            raise RuntimeError("OperationStore is required")
        current = self._operation_store.get(operation_id)
        if self._flow is not None and current.snapshot is not None:
            answer = str(payload.get("answer", "")).strip()
            field = str(payload.get("field", "")).strip()
            context = _interaction_context(payload.get("interaction_context"))
            selection = _semantic_selection(context.selection)
            if (not answer and not isinstance(selection, Time)) or ":" not in field:
                raise ValueError("semantic clarification requires an anchored answer")
            item_id = field.split(":", 1)[0]
            refreshed = self._flow.clarify(
                operation_id,
                item_id=item_id,
                question_id=field,
                answer=answer or "使用选中的时间范围",
                now=context.current_time,
                selection=selection,
            )
            return self._apply_autonomy(_operation_view(refreshed))
        raise ValueError("legacy operations are read-only")

    def _timeline_changed(
        self, scope: OperationScope, *, exclude_operation_id: str | None = None
    ) -> None:
        if self._adjustments is not None:
            self._adjustments.scan_safely()
        if self._operation_store is None:
            return
        stale = self._operation_store.mark_conflicting_stale(
            scope, exclude_operation_id=exclude_operation_id
        )
        for operation in stale:
            if self._chronos_log is not None:
                self._chronos_log.append(
                    LogEventType.OPERATION_STALE,
                    "相关时间轴已改变，提案已失效，请重新提交请求。",
                    operation_id=operation.id,
                    references=operation.references,
                    metadata={"operation_state": "stale"},
                )

    def _apply_autonomy(self, proposal: dict[str, object]) -> dict[str, object]:
        if self._operation_store is None or proposal.get("status") != "pending":
            return proposal
        operation = self._operation_store.get(str(proposal["proposal_id"]))
        policy = policy_for_level(int(self._schedule.settings()["autonomy_level"]))
        decision = evaluate_autonomy(operation, policy)
        if not decision.execute:
            return proposal
        if self._runtime is not None:
            if operation.snapshot is not None:
                transaction = self._runtime.execute(operation, operation.compiled_operations)
                applied = _operation_view(
                    self._operation_store.get(operation.id), status="accepted"
                )
            else:
                return proposal
        else:
            return proposal
        self._log_proposal_event(
            applied,
            LogEventType.OPERATION_COMPLETED,
            f"已按 Autonomy Level {policy.level} 直接执行；可撤销。",
            {"transaction_id": transaction.id} if transaction else None,
        )
        self._timeline_changed(operation.scope, exclude_operation_id=operation.id)
        return applied

    def _log_proposal_event(
        self,
        proposal: dict[str, object],
        event_type: LogEventType,
        message: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        if self._chronos_log is None:
            return
        self._chronos_log.append(
            event_type,
            message,
            operation_id=str(proposal["proposal_id"]),
            references=proposal_references(proposal),
            metadata={"proposal_status": str(proposal["status"]), **(metadata or {})},
        )


def _reminder_values(payload: dict[str, object]) -> dict[str, object]:
    trigger = payload.get("trigger")
    if not isinstance(trigger, dict):
        raise ValueError("reminder trigger is required")
    trigger_type = str(trigger.get("type", ""))

    def parse(value: object) -> datetime | None:
        return datetime.fromtimestamp(int(value) / 1000, UTC) if value else None

    return {
        "reminder_id": str(payload["id"]),
        "title": str(payload["title"]),
        "trigger_type": trigger_type,
        "trigger_at": parse(trigger.get("at")),
        "window_start": parse(trigger.get("start")),
        "window_end": parse(trigger.get("end")),
        "delivery": str(payload.get("delivery", "exact")),
        "priority": int(payload.get("priority", 3)),
        "source": str(payload.get("source", "user")),
    }


def _canonical_reminders(service: ReminderService) -> dict[str, ReminderDraft]:
    result: dict[str, ReminderDraft] = {}
    for value in service.list():
        trigger = value["trigger"]
        if not isinstance(trigger, dict):
            continue
        reminder_id = str(value["id"])
        if trigger.get("type") == "time":
            result[reminder_id] = ReminderDraft(
                reminder_id,
                str(value["title"]),
                "time",
                at=int(trigger["at"]),
                priority=int(value.get("priority", 3)),
            )
        else:
            result[reminder_id] = ReminderDraft(
                reminder_id,
                str(value["title"]),
                "window",
                window=Window(int(trigger["start"]), int(trigger["end"])),
                delivery=str(value.get("delivery", "exact")),
                priority=int(value.get("priority", 3)),
            )
    return result


def _autonomy_dict(policy: AutonomyPolicy) -> dict[str, object]:
    labels = {
        0: "Suggest Only",
        1: "Safe Actions",
        2: "Routine Autonomy",
        3: "Full Planning",
    }
    return {
        "level": policy.level,
        "label": labels[policy.level],
        "max_risk": policy.max_risk,
        "max_ambiguity": policy.max_ambiguity,
        "max_impact": policy.max_impact,
        "require_reversible": policy.require_reversible,
    }


def _task_scope(*tasks: Task) -> OperationScope:
    ranges = tuple(
        TimeRange(
            int(task.preferred_start.timestamp() * 1000),
            int(task.preferred_start.timestamp() * 1000)
            + task.estimated_minutes * 60_000,
        )
        for task in tasks
        if task.preferred_start is not None
    )
    return OperationScope(
        task_ids=tuple(dict.fromkeys(task.task_id for task in tasks)),
        time_ranges=ranges,
    )


def _reminder_scope(reminder: dict[str, object]) -> OperationScope:
    trigger = reminder.get("trigger")
    ranges = ()
    if isinstance(trigger, dict):
        if trigger.get("type") == "time":
            at = int(trigger["at"])
            ranges = (TimeRange(at, at + 60_000),)
        elif trigger.get("type") == "window":
            ranges = (TimeRange(int(trigger["start"]), int(trigger["end"])),)
    return OperationScope(
        reminder_ids=(str(reminder["id"]),),
        time_ranges=ranges,
    )


def _timeline_references(value: object) -> tuple[TimelineReference, ...]:
    if not isinstance(value, list):
        raise ValueError("references must be an array")
    references = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("reference must be an object")
        references.append(
            TimelineReference(
                type=str(item["type"]),
                id=str(item["id"]) if item.get("id") is not None else None,
                start=int(item["start"]) if item.get("start") is not None else None,
                end=int(item["end"]) if item.get("end") is not None else None,
            )
        )
    return tuple(references)


def _interaction_context(value: object) -> InteractionContext:
    if value is None:
        return InteractionContext(current_time=int(datetime.now(UTC).timestamp() * 1000))
    if not isinstance(value, dict):
        raise ValueError("interaction_context must be an object")
    selection_value = value.get("selection")
    selection = None
    if selection_value is not None:
        parsed = _timeline_references([selection_value])
        selection = parsed[0]
    return InteractionContext(
        current_time=int(value["current_time"]),
        selection=selection,
    )


def _semantic_selection(value: TimelineReference | None) -> Reference | Time | None:
    if value is None:
        return None
    if value.type in {"task", "reminder"} and value.id is not None:
        return Reference(value.type, value.id)
    if value.type == "time_range" and value.start is not None and value.end is not None:
        return Time(TimeKind.RANGE, start=value.start, end=value.end)
    return None


def _legacy_view(value: dict[str, object]) -> dict[str, object]:
    return {
        **value,
        "requires_confirmation": False,
        "read_only": True,
        "source": "legacy",
    }


def _interaction_context_dict(context: InteractionContext) -> dict[str, object]:
    selection = context.selection
    if selection is None:
        selection_value = None
    elif selection.type == "time_range":
        selection_value = {
            "type": selection.type,
            "start": selection.start,
            "end": selection.end,
        }
    else:
        selection_value = {"type": selection.type, "id": selection.id}
    return {
        "current_time": context.current_time,
        "selection": selection_value,
    }
