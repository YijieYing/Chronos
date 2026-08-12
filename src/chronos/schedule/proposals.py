"""Create explainable Schedule drafts and apply them only after confirmation."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from chronos.schedule.command_batch import ScheduleCommandBatch, ScheduleCreateCommand
from chronos.schedule.commands import (
    DeterministicScheduleCommandParser,
    ScheduleCommand,
    ScheduleCommandParser,
)
from chronos.schedule.models import (
    BlockStatus,
    Plan,
    PlanStatus,
    ScheduleBlock,
    Task,
    TaskStatus,
    UnscheduledTask,
)
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
        interpret = getattr(self._parser, "interpret", None)
        if callable(interpret):
            interpretation = interpret(text, local_now, tasks)
            if interpretation.unresolved:
                return self._create_clarification(text, interpretation)
            if interpretation.intent == "create_schedule":
                return self._create_batch(text, interpretation, local_now.date())
            if interpretation.command is None:
                raise ValueError("interpretation did not produce a command")
            command = interpretation.command
            context_used = [dict(item) for item in interpretation.context_used]
            parser_mode = interpretation.mode
            parser_warnings: list[str] = []
            if command.type == "query_schedule":
                return self._create_query(text, command, context_used, parser_mode, parser_warnings)
            return self._create_mutation(
                text, command, zone, context_used, parser_mode, parser_warnings
            )
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
            return self._create_query(text, command, context_used, parser_mode, parser_warnings)
        return self._create_mutation(
            text, command, zone, context_used, parser_mode, parser_warnings
        )

    def _create_clarification(self, text, interpretation) -> dict[str, object]:
        proposal = {
            "proposal_id": str(uuid4()),
            "status": "needs_clarification",
            "requires_confirmation": False,
            "request_text": text,
            "command": None,
            "commands": [],
            "proposed_task": None,
            "proposed_tasks": [],
            "results": [],
            "draft_plan": None,
            "draft_plans": [],
            "base_plan_version": None,
            "base_plan_versions": {},
            "changes": [],
            "conflicts": [],
            "explanation": [item.question for item in interpretation.unresolved],
            "clarifications": [
                {"field": item.field, "question": item.question}
                for item in interpretation.unresolved
            ],
            "assumptions": list(interpretation.assumptions),
            "context_used": [dict(item) for item in interpretation.context_used],
            "parser_mode": interpretation.mode,
            "parser_warnings": [],
        }
        return self._repository.save(proposal)

    def _create_batch(
        self, text, interpretation, start_date: date
    ) -> dict[str, object]:
        typed_commands: list[ScheduleCreateCommand] = []
        for item in interpretation.tasks:
            if item.duration_minutes is None or item.preferred_start is None:
                raise ValueError("resolved interpretation still contains missing fields")
            intensity, spectrum = _task_characteristics(item.task_type)
            task = Task(
                task_id=str(uuid4()),
                title=item.title,
                estimated_minutes=item.duration_minutes,
                priority=3,
                status=TaskStatus.BACKLOG,
                created_at=datetime.now(UTC),
                splittable=False,
                preferred_start=item.preferred_start,
                cognitive_intensity=intensity,
                spectrum=spectrum,
                task_type=item.task_type,
                fixed=item.fixed,
                source="agent",
                recurrence=item.recurrence,
            )
            typed_commands.append(
                ScheduleCreateCommand(
                    task=task,
                    title_source=item.title_source,
                    duration_source=item.duration_source,
                    temporal_source=item.temporal_source,
                    recurrence_source=item.recurrence_source,
                )
            )
        batch = ScheduleCommandBatch(tuple(typed_commands))
        proposed = batch.tasks
        commands = batch.to_dicts()
        plans = self._schedule.preview_horizon(
            proposed, start_date, days=batch.horizon_days
        )
        versions = {
            plan.target_date.isoformat(): self._schedule.current_plan_version(plan.target_date)
            for plan in plans
        }
        occurrences = _batch_occurrences(proposed, plans)
        conflicts = [
            {
                "date": plan.target_date.isoformat(),
                "task_id": item.task_id,
                "reason": item.reason,
                "remaining_minutes": item.remaining_minutes,
            }
            for plan in plans
            for item in plan.unscheduled
            if item.task_id in {task.task_id for task in proposed}
        ]
        proposal = {
            "proposal_id": str(uuid4()),
            "status": "pending",
            "requires_confirmation": True,
            "request_text": text,
            "command": None,
            "commands": commands,
            "proposed_task": occurrences[0] if occurrences else None,
            "proposed_tasks": [_task_dict(task) for task in proposed],
            "results": occurrences,
            "draft_plan": _plan_dict(plans[0]) if plans else None,
            "draft_plans": [_plan_dict(plan) for plan in plans],
            "base_plan_version": versions.get(start_date.isoformat()),
            "base_plan_versions": versions,
            "changes": [
                {
                    "operation": "add",
                    "task_id": task.task_id,
                    "summary": f"创建周期任务「{task.title}」",
                }
                for task in proposed
            ],
            "conflicts": conflicts,
            "explanation": [
                f"识别出 {len(proposed)} 个独立任务。",
                f"已预览 {len(plans)} 天，共 {len(occurrences)} 个周期实例。",
                "所有标题、时长、时间和周期均保留原文依据。",
                "确认后整批写入；任一写入失败则全部回滚。",
            ],
            "clarifications": [],
            "assumptions": list(interpretation.assumptions),
            "context_used": [dict(item) for item in interpretation.context_used],
            "parser_mode": interpretation.mode,
            "parser_warnings": [],
        }
        return self._repository.save(proposal)

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
            if (
                command.task_id is None
                or item.get("series_id", item["id"]) == command.task_id
            )
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
        commands = proposal.get("commands")
        if isinstance(commands, list) and commands:
            task_payloads = [
                command["after"]
                for command in commands
                if isinstance(command, dict) and isinstance(command.get("after"), dict)
            ]
            plans_payload = proposal.get("draft_plans")
            if len(task_payloads) != len(commands) or not isinstance(plans_payload, list):
                raise ValueError("batch proposal payload is incomplete")
            tasks = [_task_from_dict(item) for item in task_payloads]
            plans = [_plan_from_dict(item) for item in plans_payload if isinstance(item, dict)]
            self._schedule.apply_horizon_batch(tasks, plans)
            return self._repository.save(
                {
                    **proposal,
                    "status": "accepted",
                    "activated_plan_ids": [plan.plan_id for plan in plans],
                }
            )
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
        commands = proposal.get("commands")
        if isinstance(commands, list) and commands:
            ids = [str(item["task_id"]) for item in commands if isinstance(item, dict)]
            versions = proposal.get("base_plan_versions", {})
            dates = (
                [date.fromisoformat(value) for value in versions]
                if isinstance(versions, dict)
                else []
            )
            self._schedule.remove_horizon_batch(ids, dates)
            return self._repository.save({**proposal, "status": "restored"})
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
                current = self._schedule.current_plan_version(
                    datetime.fromisoformat(value).date()
                )
                if current != version:
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


def _task_characteristics(task_type: str) -> tuple[float, float]:
    intensity = 0.72 if task_type in {"coding", "creative", "research"} else 0.45
    spectrum = {
        "creative": 0.08,
        "coding": 0.28,
        "research": 0.2,
        "communication": 0.65,
        "execution": 0.88,
        "meeting": 0.72,
        "recovery": 0.5,
    }.get(task_type, 0.88)
    return intensity, spectrum


def _batch_occurrences(tasks: list[Task], plans: list[Plan]) -> list[dict[str, object]]:
    by_id = {task.task_id: task for task in tasks}
    results: list[dict[str, object]] = []
    for plan in plans:
        for block in plan.blocks:
            task = by_id.get(block.task_id)
            if task is None:
                continue
            results.append(
                {
                    "id": f"{task.task_id}::{int(block.start_at.timestamp() * 1000)}",
                    "series_id": task.task_id,
                    "title": task.title,
                    "start": int(block.start_at.timestamp() * 1000),
                    "end": int(block.end_at.timestamp() * 1000),
                    "predicted_end": int(block.end_at.timestamp() * 1000),
                    "intensity": task.cognitive_intensity,
                    "spectrum": task.spectrum,
                    "fixed": task.fixed,
                    "task_type": task.task_type,
                    "source": task.source,
                    "recurrence": task.recurrence,
                    "plan_id": plan.plan_id,
                    "plan_version": plan.version,
                    "scheduled": True,
                    "unscheduled_reason": None,
                }
            )
    return sorted(results, key=lambda item: int(item["start"]))


def _task_from_dict(item: dict[str, object]) -> Task:
    return Task(
        task_id=str(item["task_id"]),
        title=str(item["title"]),
        estimated_minutes=int(item["estimated_minutes"]),
        priority=int(item["priority"]),
        status=TaskStatus(str(item["status"])),
        created_at=datetime.fromisoformat(str(item["created_at"])),
        deadline=datetime.fromisoformat(str(item["deadline"])) if item.get("deadline") else None,
        splittable=bool(item["splittable"]),
        min_chunk_minutes=int(item["min_chunk_minutes"]),
        preferred_start=datetime.fromisoformat(str(item["preferred_start"]))
        if item.get("preferred_start")
        else None,
        cognitive_intensity=float(item["cognitive_intensity"]),
        spectrum=float(item["spectrum"]),
        task_type=str(item["task_type"]),
        fixed=bool(item["fixed"]),
        source=str(item["source"]),
        recurrence=item.get("recurrence") if isinstance(item.get("recurrence"), dict) else None,
    )


def _plan_from_dict(item: dict[str, object]) -> Plan:
    return Plan(
        plan_id=str(item["plan_id"]),
        version=int(item["version"]),
        target_date=date.fromisoformat(str(item["target_date"])),
        timezone=str(item["timezone"]),
        status=PlanStatus(str(item["status"])),
        created_at=datetime.fromisoformat(str(item["created_at"])),
        based_on_version=int(item["based_on_version"])
        if item.get("based_on_version") is not None
        else None,
        blocks=tuple(
            ScheduleBlock(
                block_id=str(block["block_id"]),
                task_id=str(block["task_id"]),
                title=str(block["title"]),
                start_at=datetime.fromisoformat(str(block["start_at"])),
                end_at=datetime.fromisoformat(str(block["end_at"])),
                status=BlockStatus(str(block["status"])),
                flexibility=str(block["flexibility"]),
            )
            for block in item.get("blocks", [])
            if isinstance(block, dict)
        ),
        unscheduled=tuple(
            UnscheduledTask(
                task_id=str(value["task_id"]),
                title=str(value["title"]),
                remaining_minutes=int(value["remaining_minutes"]),
                reason=str(value["reason"]),
            )
            for value in item.get("unscheduled", [])
            if isinstance(value, dict)
        ),
    )


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
        lines.append(f"调整后为 {scheduled_at:%H:%M}，预计 {task.estimated_minutes} 分钟。")
    lines.append("已由 Schedule planner 检查固定安排和现有任务。")
    lines.append("确认前不会修改当前计划。")
    return lines
