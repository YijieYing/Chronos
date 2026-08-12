from __future__ import annotations

from datetime import UTC, datetime
from http import HTTPStatus

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
    ) -> None:
        self._schedule = schedule
        self._proposals = proposals
        self._agent_memory = agent_memory
        self._reminders = reminders

    def dispatch(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> RouteResult | None:
        if not path.startswith("/api/v1/"):
            return None
        payload = payload or {}
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
            proposal = self._proposals.create(str(payload.get("text", "")))
            return HTTPStatus.CREATED, success(proposal)
        proposal_prefix = "/api/v1/proposals/"
        if path.startswith(proposal_prefix):
            suffix = path.removeprefix(proposal_prefix)
            if method == "GET" and "/" not in suffix:
                return HTTPStatus.OK, success(self._proposals.get(suffix))
            if method == "POST" and suffix.endswith("/accept"):
                proposal_id = suffix.removesuffix("/accept")
                return HTTPStatus.OK, success(self._proposals.accept(proposal_id))
            if method == "POST" and suffix.endswith("/reject"):
                proposal_id = suffix.removesuffix("/reject")
                return HTTPStatus.OK, success(self._proposals.reject(proposal_id))
            if method == "POST" and suffix.endswith("/restore"):
                proposal_id = suffix.removesuffix("/restore")
                return HTTPStatus.OK, success(self._proposals.restore(proposal_id))
        raise KeyError(path)


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
