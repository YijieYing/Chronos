"""Create explainable Schedule drafts and apply them only after confirmation."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from chronos.schedule.commands import (
    DeterministicScheduleCommandParser,
    ScheduleCommand,
    ScheduleCommandParser,
)
from chronos.schedule.models import Task, TaskStatus
from chronos.schedule.ports import ProposalRepository
from chronos.schedule.service import (
    ScheduleService,
    _plan_dict,
    _task_dict,
    _timeline_task_dict,
)


class ProposalService:
    def __init__(
        self,
        schedule: ScheduleService,
        repository: ProposalRepository,
        parser: ScheduleCommandParser | None = None,
    ) -> None:
        self._schedule = schedule
        self._repository = repository
        self._parser = parser or DeterministicScheduleCommandParser()

    def create(self, request_text: str, now: datetime | None = None) -> dict[str, object]:
        text = request_text.strip()
        if not text:
            raise ValueError("request text is required")
        zone = ZoneInfo(self._schedule.settings()["timezone"])
        local_now = (now or datetime.now(UTC)).astimezone(zone)
        tasks = self._schedule.list_tasks()
        parse_with_context = getattr(self._parser, "parse_with_context", None)
        if callable(parse_with_context):
            parsed = parse_with_context(text, local_now, tasks)
            command = parsed.command
            context_used = [dict(item) for item in parsed.context_used]
            parser_mode = str(parsed.parser_mode)
            parser_warnings = list(parsed.warnings)
        else:
            command = self._parser.parse(text, local_now, tasks)
            context_used = []
            parser_mode = "deterministic"
            parser_warnings = []
        if command.type == "query_schedule":
            return self._create_query(
                text, command, context_used, parser_mode, parser_warnings
            )
        return self._create_mutation(
            text, command, zone, context_used, parser_mode, parser_warnings
        )

    def _create_query(
        self,
        text: str,
        command: ScheduleCommand,
        context_used: list[dict[str, object]],
        parser_mode: str,
        parser_warnings: list[str],
    ) -> dict[str, object]:
        zone = ZoneInfo(self._schedule.settings()["timezone"])
        timeline = self._schedule.timeline()["tasks"]
        assert isinstance(timeline, list)
        results = [
            item
            for item in timeline
            if (command.task_id is None or item["id"] == command.task_id)
            and (
                command.query_date is None
                or datetime.fromtimestamp(int(item["start"]) / 1000, zone).date()
                == command.query_date
            )
        ]
        results.sort(key=lambda item: int(item["start"]))
        if results:
            explanation = [f"找到 {len(results)} 个安排。"]
        else:
            explanation = ["没有找到符合条件的安排。"]
        proposal = {
            "proposal_id": str(uuid4()),
            "status": "informational",
            "requires_confirmation": False,
            "request_text": text,
            "command": _command_dict(command),
            "proposed_task": results[0] if len(results) == 1 else None,
            "results": results,
            "draft_plan": None,
            "base_plan_version": None,
            "base_plan_versions": {},
            "changes": [],
            "conflicts": [],
            "explanation": explanation,
            "context_used": context_used,
            "parser_mode": parser_mode,
            "parser_warnings": parser_warnings,
        }
        return self._repository.save(proposal)

    def _create_mutation(
        self,
        text: str,
        command: ScheduleCommand,
        zone: ZoneInfo,
        context_used: list[dict[str, object]],
        parser_mode: str,
        parser_warnings: list[str],
    ) -> dict[str, object]:
        before: Task | None = None
        if command.type == "create_task":
            task = Task(
                task_id=str(uuid4()),
                title=str(command.title),
                estimated_minutes=int(command.estimated_minutes or 30),
                priority=3,
                status=TaskStatus.BACKLOG,
                created_at=datetime.now(UTC),
                splittable=False,
                preferred_start=command.preferred_start,
                cognitive_intensity=float(command.cognitive_intensity or 0.5),
                spectrum=float(command.spectrum or 0.5),
                task_type=str(command.task_type or "execution"),
                source="agent",
                recurrence=command.recurrence,
                fixed=bool(command.fixed),
            )
            operation = "add"
            draft = self._schedule.preview_with_task(task)
        else:
            before = self._schedule.get_task(str(command.task_id))
            if before.preferred_start is None:
                raise ValueError("Agent 目前只能修改已经排入时间轴的任务")
            if command.type == "update_task":
                task = replace(
                    before,
                    status=TaskStatus.BACKLOG,
                    splittable=False,
                    preferred_start=command.preferred_start or before.preferred_start,
                    estimated_minutes=command.estimated_minutes or before.estimated_minutes,
                    source="agent",
                )
                operation = "update"
                draft = self._schedule.preview_with_task(task)
            else:
                task = before
                operation = "delete"
                draft = self._schedule.preview_without_task(before)

        proposed_task = _timeline_task_dict(
            task,
            draft if command.type != "delete_task" else None,
        )
        effective_start = (
            datetime.fromtimestamp(int(proposed_task["start"]) / 1000, zone)
            if command.type != "delete_task"
            else task.preferred_start
        )
        assert effective_start is not None
        command_payload = _command_dict(command)
        command_payload["task_id"] = task.task_id
        command_payload["before"] = _task_dict(before) if before else None
        command_payload["after"] = _task_dict(task) if command.type != "delete_task" else None
        relevant_dates = {task.preferred_start.date()} if task.preferred_start else set()
        if before and before.preferred_start:
            relevant_dates.add(before.preferred_start.date())
        versions = {
            target.isoformat(): self._schedule.current_plan_version(target)
            for target in relevant_dates
        }
        proposal = {
            "proposal_id": str(uuid4()),
            "status": "pending",
            "requires_confirmation": True,
            "request_text": text,
            "command": command_payload,
            "proposed_task": proposed_task,
            "results": [],
            "draft_plan": _plan_dict(draft),
            "base_plan_version": versions.get(draft.target_date.isoformat()),
            "base_plan_versions": versions,
            "changes": [
                {
                    "operation": operation,
                    "task_id": task.task_id,
                    "summary": _change_summary(operation, task, before, effective_start, zone),
                }
            ],
            "conflicts": [
                {
                    "task_id": item.task_id,
                    "reason": item.reason,
                    "remaining_minutes": item.remaining_minutes,
                }
                for item in draft.unscheduled
            ],
            "explanation": _explanation(command.type, task, before, effective_start),
            "context_used": context_used,
            "parser_mode": parser_mode,
            "parser_warnings": parser_warnings,
        }
        return self._repository.save(proposal)

    def get(self, proposal_id: str) -> dict[str, object]:
        proposal = self._repository.get(proposal_id)
        if proposal is None:
            raise KeyError(proposal_id)
        return proposal

    def list(self) -> list[dict[str, object]]:
        return self._repository.list()

    def accept(self, proposal_id: str) -> dict[str, object]:
        proposal = self.get(proposal_id)
        if proposal["status"] != "pending":
            raise ValueError("only pending proposals can be accepted")
        self._assert_fresh(proposal)
        command = proposal["command"]
        assert isinstance(command, dict)
        command_type = str(command["type"])
        after = command.get("after")
        if command_type == "create_task":
            assert isinstance(after, dict)
            _, plan = self._schedule.create_scheduled_task(**_scheduled_values(after))
        elif command_type == "update_task":
            assert isinstance(after, dict)
            _, plan = self._schedule.update_scheduled_task(
                str(command["task_id"]), **_scheduled_values(after, include_id=False)
            )
        elif command_type == "delete_task":
            before = command.get("before")
            assert isinstance(before, dict)
            target = datetime.fromisoformat(str(before["preferred_start"])).date()
            if not self._schedule.delete_scheduled_task(str(command["task_id"])):
                raise KeyError(command["task_id"])
            plan_id = self._schedule.timeline_plan_id(target)
            updated = {**proposal, "status": "accepted", "activated_plan_id": plan_id}
            return self._repository.save(updated)
        else:
            raise ValueError(f"unsupported proposal command: {command_type}")
        return self._repository.save(
            {**proposal, "status": "accepted", "activated_plan_id": plan.plan_id}
        )

    def reject(self, proposal_id: str) -> dict[str, object]:
        proposal = self.get(proposal_id)
        if proposal["status"] != "pending":
            raise ValueError("only pending proposals can be rejected")
        return self._repository.save({**proposal, "status": "rejected"})

    def restore(self, proposal_id: str) -> dict[str, object]:
        proposal = self.get(proposal_id)
        if proposal["status"] != "accepted":
            raise ValueError("only accepted proposals can be restored")
        command = proposal["command"]
        assert isinstance(command, dict)
        command_type = str(command["type"])
        if command_type == "create_task":
            if not self._schedule.delete_scheduled_task(str(command["task_id"])):
                raise KeyError(command["task_id"])
        elif command_type == "update_task":
            before = command.get("before")
            assert isinstance(before, dict)
            self._schedule.update_scheduled_task(
                str(command["task_id"]), **_scheduled_values(before, include_id=False)
            )
        elif command_type == "delete_task":
            before = command.get("before")
            assert isinstance(before, dict)
            self._schedule.create_scheduled_task(**_scheduled_values(before))
        else:
            raise ValueError("informational proposals cannot be restored")
        return self._repository.save({**proposal, "status": "restored"})

    def _assert_fresh(self, proposal: dict[str, object]) -> None:
        versions = proposal.get("base_plan_versions")
        if isinstance(versions, dict):
            for value, version in versions.items():
                if self._schedule.current_plan_version(datetime.fromisoformat(value).date()) != version:
                    raise ValueError("proposal is stale; generate a new proposal")
            return
        proposed = proposal.get("proposed_task")
        if isinstance(proposed, dict):
            zone = ZoneInfo(self._schedule.settings()["timezone"])
            target = datetime.fromtimestamp(int(proposed["start"]) / 1000, zone).date()
            if self._schedule.current_plan_version(target) != proposal.get("base_plan_version"):
                raise ValueError("proposal is stale; generate a new proposal")


def _command_dict(command: ScheduleCommand) -> dict[str, object]:
    return {
        "type": command.type,
        "task_id": command.task_id,
        "title": command.title,
        "preferred_start": command.preferred_start.isoformat() if command.preferred_start else None,
        "estimated_minutes": command.estimated_minutes,
        "cognitive_intensity": command.cognitive_intensity,
        "spectrum": command.spectrum,
        "task_type": command.task_type,
        "query_date": command.query_date.isoformat() if command.query_date else None,
        "recurrence": command.recurrence,
        "fixed": command.fixed,
    }


def _scheduled_values(task: dict[str, object], *, include_id: bool = True) -> dict[str, object]:
    values: dict[str, object] = {
        "title": str(task["title"]),
        "estimated_minutes": int(task["estimated_minutes"]),
        "priority": int(task["priority"]),
        "deadline": datetime.fromisoformat(str(task["deadline"])) if task.get("deadline") else None,
        "preferred_start": datetime.fromisoformat(str(task["preferred_start"])),
        "cognitive_intensity": float(task["cognitive_intensity"]),
        "spectrum": float(task["spectrum"]),
        "task_type": str(task["task_type"]),
        "fixed": bool(task["fixed"]),
        "source": str(task["source"]),
        "recurrence": task.get("recurrence"),
    }
    if include_id:
        values["task_id"] = str(task["task_id"])
        values["created_at"] = datetime.fromisoformat(str(task["created_at"]))
    return values


def _change_summary(
    operation: str, task: Task, before: Task | None, start: datetime, zone: ZoneInfo
) -> str:
    end = start + timedelta(minutes=task.estimated_minutes)
    if operation == "delete":
        return f"删除 {task.title}（{start.astimezone(zone):%H:%M}）"
    if operation == "update" and before and before.preferred_start:
        return (
            f"{task.title}: {before.preferred_start.astimezone(zone):%H:%M} / "
            f"{before.estimated_minutes} 分钟 → {start.astimezone(zone):%H:%M} / "
            f"{task.estimated_minutes} 分钟"
        )
    return f"{start.astimezone(zone):%H:%M}–{end.astimezone(zone):%H:%M} {task.title}"


def _explanation(
    command_type: str, task: Task, before: Task | None, scheduled_at: datetime
) -> list[str]:
    action = {
        "create_task": "创建",
        "update_task": "调整",
        "delete_task": "删除",
    }[command_type]
    lines = [f"识别为{action}任务「{task.title}」。"]
    if command_type == "update_task" and before:
        lines.append(
            f"调整后为 {scheduled_at:%H:%M}，预计 {task.estimated_minutes} 分钟。"
        )
    lines.append("已由 Schedule planner 检查固定安排和现有任务。")
    lines.append("确认前不会修改当前计划。")
    return lines
