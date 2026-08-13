from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from http import HTTPStatus

from chronos.agent.compiler import (
    ChronosCompiler,
    CompilerResult,
    LegacyProposalCompiler,
)
from chronos.agent.legacy_log import proposal_references
from chronos.agent.log_service import ChronosLogService
from chronos.agent.models import (
    InteractionContext,
    LogEventType,
    OperationState,
    TimelineReference,
)
from chronos.agent.projection_service import ProjectionService
from chronos.agent.serialization import log_entry_to_dict, projection_to_dict
from chronos.agent.service import OperationStore
from chronos.api.contracts.common import success
from chronos.api.contracts.schedule import scheduled_task_values
from chronos.reminders.models import ReminderStatus
from chronos.reminders.service import ReminderService, reminder_dict
from chronos.schedule.agent_memory import AgentMemoryService
from chronos.schedule.proposals import ProposalService
from chronos.schedule.service import ScheduleService, _plan_dict

RouteResult = tuple[HTTPStatus, dict[str, object]]


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
        compiler: ChronosCompiler | None = None,
    ) -> None:
        self._schedule = schedule
        self._proposals = proposals
        self._agent_memory = agent_memory
        self._reminders = reminders
        self._chronos_log = chronos_log
        self._operation_store = operation_store
        self._projections = projections
        self._semantic_compiler = compiler
        self._compiler = LegacyProposalCompiler()

    def dispatch(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> RouteResult | None:
        if not path.startswith("/api/v1/"):
            return None
        payload = payload or {}
        if method == "GET" and path == "/api/v1/timeline-projections":
            if self._projections is None:
                return HTTPStatus.OK, success({"projections": []})
            projections = self._projections.list_active(tuple(self._proposals.list()))
            return HTTPStatus.OK, success(
                {"projections": [projection_to_dict(item) for item in projections]}
            )
        if self._chronos_log is not None and path == "/api/v1/chronos-log":
            if method == "GET":
                entries = [log_entry_to_dict(item) for item in self._chronos_log.list()]
                pending_ids = (
                    {item.id for item in self._operation_store.pending()}
                    if self._operation_store is not None
                    else set()
                )
                pending_ids.update(
                    str(item["proposal_id"])
                    for item in self._proposals.list()
                    if item.get("status") in {"pending", "needs_clarification"}
                )
                return HTTPStatus.OK, success(
                    {"entries": entries, "pending_count": len(pending_ids)}
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
                    return HTTPStatus.CREATED, success(reminder_dict(reminder))
            reminder_prefix = "/api/v1/reminders/"
            if path.startswith(reminder_prefix):
                reminder_id = path.removeprefix(reminder_prefix)
                if method == "PUT":
                    reminder = self._reminders.set_status(
                        reminder_id, ReminderStatus(str(payload["status"]))
                    )
                    return HTTPStatus.OK, success(reminder_dict(reminder))
                if method == "DELETE":
                    if not self._reminders.delete(reminder_id):
                        raise KeyError(reminder_id)
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
            return HTTPStatus.CREATED, success(
                {
                    "task_id": task.task_id,
                    "plan": _plan_dict(plan),
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
                task, plan = self._schedule.update_scheduled_task(task_id, **values)
                return HTTPStatus.OK, success(
                    {
                        "task_id": task.task_id,
                        "plan": _plan_dict(plan),
                        "timeline": self._schedule.timeline(),
                    }
                )
            if method == "DELETE":
                if not self._schedule.delete_scheduled_task(task_id):
                    raise KeyError(task_id)
                return HTTPStatus.OK, success({"deleted": True, "task_id": task_id})

        if method == "GET" and path == "/api/v1/proposals":
            return HTTPStatus.OK, success({"proposals": self._proposals.list()})
        if method == "POST" and path == "/api/v1/proposals":
            text = str(payload.get("text", ""))
            context = _interaction_context(payload.get("interaction_context"))
            compiled: CompilerResult | None = None
            if self._semantic_compiler is not None:
                compile_context = replace(
                    context,
                    user_input=text,
                    timeline_context={
                        "tasks": tuple(self._schedule.list_tasks()),
                        "timezone": self._schedule.settings()["timezone"],
                    },
                )
                compiled = self._semantic_compiler.compile(compile_context)
                proposal = self._proposals.create_from_compiler(
                    compiled,
                    datetime.fromtimestamp(context.current_time / 1000, UTC),
                )
            else:
                proposal = self._proposals.create(text)
            proposal = self._proposals.attach_interaction_context(
                str(proposal["proposal_id"]), _interaction_context_dict(context)
            )
            self._compile_proposal(proposal, text, context, compiled)
            self._log_proposal_created(proposal, text)
            return HTTPStatus.CREATED, success(proposal)
        proposal_prefix = "/api/v1/proposals/"
        if path.startswith(proposal_prefix):
            suffix = path.removeprefix(proposal_prefix)
            if method == "GET" and "/" not in suffix:
                return HTTPStatus.OK, success(self._proposals.get(suffix))
            if method == "POST" and suffix.endswith("/accept"):
                proposal_id = suffix.removesuffix("/accept")
                proposal = self._proposals.accept(proposal_id)
                self._complete_operation(proposal_id)
                self._log_proposal_event(
                    proposal, LogEventType.OPERATION_COMPLETED, "已应用时间轴调整。"
                )
                return HTTPStatus.OK, success(proposal)
            if method == "POST" and suffix.endswith("/reject"):
                proposal_id = suffix.removesuffix("/reject")
                proposal = self._proposals.reject(proposal_id)
                self._reject_operation(proposal_id)
                self._log_proposal_event(
                    proposal, LogEventType.OPERATION_REJECTED, "已拒绝时间轴提案。"
                )
                return HTTPStatus.OK, success(proposal)
            if method == "POST" and suffix.endswith("/restore"):
                proposal_id = suffix.removesuffix("/restore")
                proposal = self._proposals.restore(proposal_id)
                self._log_proposal_event(
                    proposal, LogEventType.UNDO, "已恢复此次调整前的状态。"
                )
                return HTTPStatus.OK, success(proposal)
        raise KeyError(path)

    def _log_proposal_created(self, proposal: dict[str, object], text: str) -> None:
        if self._chronos_log is None:
            return
        proposal_id = str(proposal["proposal_id"])
        references = proposal_references(proposal)
        self._chronos_log.append(
            LogEventType.USER_PROMPT,
            text,
            operation_id=proposal_id,
            references=references,
        )
        status = str(proposal["status"])
        if status == "needs_clarification":
            event_type = LogEventType.CLARIFICATION_REQUESTED
        elif status == "pending":
            event_type = LogEventType.PROPOSAL_CREATED
        else:
            event_type = LogEventType.AGENT_MESSAGE
        if status == "needs_clarification":
            clarifications = proposal.get("clarifications", [])
            message = " ".join(
                str(item.get("question", ""))
                for item in clarifications
                if isinstance(item, dict)
            )
        else:
            explanation = proposal.get("explanation", [])
            message = " ".join(str(item) for item in explanation)
        self._chronos_log.append(
            event_type,
            message or "Chronos 已处理请求。",
            operation_id=proposal_id,
            references=references,
            metadata={"proposal_status": status},
        )

    def _compile_proposal(
        self,
        proposal: dict[str, object],
        text: str,
        context: InteractionContext,
        compiled: CompilerResult | None = None,
    ) -> None:
        if self._operation_store is None:
            return
        compile_context = replace(
            context,
            user_input=text,
            timeline_context={"legacy_proposal": proposal},
        )
        result = self._compiler.compile(compile_context)
        operation = result.operation
        if compiled is not None:
            operation = replace(
                operation,
                intent=compiled.operation.intent,
                unresolved_questions=compiled.operation.unresolved_questions,
                ambiguity=compiled.operation.ambiguity,
            )
        self._operation_store.create_snapshot(operation)

    def _complete_operation(self, operation_id: str) -> None:
        if self._operation_store is None:
            return
        operation = self._operation_store.get(operation_id)
        approved = self._operation_store.transition(
            operation_id, OperationState.APPROVED, expected_version=operation.version
        )
        executing = self._operation_store.transition(
            operation_id, OperationState.EXECUTING, expected_version=approved.version
        )
        self._operation_store.transition(
            operation_id, OperationState.COMPLETED, expected_version=executing.version
        )

    def _reject_operation(self, operation_id: str) -> None:
        if self._operation_store is None:
            return
        operation = self._operation_store.get(operation_id)
        self._operation_store.transition(
            operation_id, OperationState.REJECTED, expected_version=operation.version
        )

    def _log_proposal_event(
        self,
        proposal: dict[str, object],
        event_type: LogEventType,
        message: str,
    ) -> None:
        if self._chronos_log is None:
            return
        self._chronos_log.append(
            event_type,
            message,
            operation_id=str(proposal["proposal_id"]),
            references=proposal_references(proposal),
            metadata={"proposal_status": str(proposal["status"])},
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
