"""Application service for user-driven Schedule commands."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from chronos.schedule.models import (
    AvailabilityWindow,
    FixedBlock,
    Plan,
    Task,
    TaskStatus,
)
from chronos.schedule.planner import DailyPlanner, task_occurrence
from chronos.schedule.ports import ScheduleRepository


class ScheduleService:
    DEFAULTS = {
        "timezone": "Asia/Shanghai",
        "autonomy_level": "0",
    }

    def __init__(self, repository: ScheduleRepository, planner: DailyPlanner | None = None) -> None:
        self._repository = repository
        self._planner = planner or DailyPlanner()

    def settings(self) -> dict[str, str]:
        return {
            key: self._repository.get_setting(key) or default
            for key, default in self.DEFAULTS.items()
        }

    def update_settings(self, values: dict[str, str]) -> dict[str, str]:
        allowed = set(self.DEFAULTS)
        merged = self.settings()
        for key, value in values.items():
            if key not in allowed:
                continue
            if key == "timezone":
                try:
                    ZoneInfo(value)
                except ZoneInfoNotFoundError as error:
                    raise ValueError(f"unknown timezone: {value}") from error
            elif key == "autonomy_level" and str(value) not in {"0", "1", "2", "3"}:
                raise ValueError("autonomy level must be from 0 to 3")
            merged[key] = value
        for key, value in merged.items():
            self._repository.set_setting(key, value)
        return merged

    def create_task(
        self,
        *,
        title: str,
        estimated_minutes: int,
        priority: int,
        deadline: datetime | None = None,
        splittable: bool = True,
        min_chunk_minutes: int = 25,
        preferred_start: datetime | None = None,
        cognitive_intensity: float = 0.5,
        spectrum: float = 0.5,
        task_type: str = "execution",
        fixed: bool = False,
        source: str = "user",
        recurrence: dict[str, object] | None = None,
        task_id: str | None = None,
        created_at: datetime | None = None,
    ) -> Task:
        task = Task(
            task_id=task_id or str(uuid4()),
            title=title.strip(),
            estimated_minutes=estimated_minutes,
            priority=priority,
            status=TaskStatus.BACKLOG,
            created_at=created_at or datetime.now(UTC),
            deadline=deadline,
            splittable=splittable,
            min_chunk_minutes=min_chunk_minutes,
            preferred_start=preferred_start,
            cognitive_intensity=cognitive_intensity,
            spectrum=spectrum,
            task_type=task_type,
            fixed=fixed,
            source=source,
            recurrence=recurrence,
        )
        self._repository.save_task(task)
        return task

    def create_scheduled_task(self, **values) -> tuple[Task, Plan]:
        task = self.create_task(splittable=False, **values)
        if task.preferred_start is None:
            self._repository.delete_task(task.task_id)
            raise ValueError("scheduled tasks require preferred_start")
        try:
            plan = self.generate_plan(task.preferred_start.date())
            return task, self.activate_plan(plan.plan_id)
        except Exception:
            self._repository.delete_task(task.task_id)
            raise

    def update_scheduled_task(self, task_id: str, **values) -> tuple[Task, Plan]:
        previous = self._repository.get_task(task_id)
        if previous is None:
            raise KeyError(task_id)
        updated = replace(previous, status=TaskStatus.BACKLOG, splittable=False, **values)
        if updated.preferred_start is None:
            raise ValueError("scheduled tasks require preferred_start")
        self._repository.save_task(updated)
        try:
            plan = self.generate_plan(updated.preferred_start.date())
            activated = self.activate_plan(plan.plan_id)
        except Exception:
            self._repository.save_task(previous)
            raise
        if (
            previous.preferred_start is not None
            and previous.preferred_start.date() != updated.preferred_start.date()
        ):
            previous_day = self.generate_plan(previous.preferred_start.date())
            self.activate_plan(previous_day.plan_id)
        return updated, activated

    def delete_scheduled_task(self, task_id: str) -> bool:
        task = self._repository.get_task(task_id)
        if task is None:
            return False
        deleted = self._repository.delete_task(task_id)
        if deleted and task.preferred_start is not None:
            plan = self.generate_plan(task.preferred_start.date())
            self.activate_plan(plan.plan_id)
        return deleted

    def preview_with_task(self, task: Task) -> Plan:
        if task.preferred_start is None:
            raise ValueError("proposal tasks require preferred_start")
        tasks = [
            existing
            for existing in self._repository.list_tasks()
            if existing.task_id != task.task_id
        ]
        return self._build_plan(task.preferred_start.date(), [*tasks, task])

    def preview_without_task(self, task: Task) -> Plan:
        if task.preferred_start is None:
            raise ValueError("proposal tasks require preferred_start")
        tasks = [
            existing
            for existing in self._repository.list_tasks()
            if existing.task_id != task.task_id
        ]
        return self._build_plan(task.preferred_start.date(), tasks)

    def preview_horizon(
        self, proposed_tasks: list[Task], start_date: date, days: int = 14
    ) -> list[Plan]:
        if not 1 <= days <= 90:
            raise ValueError("preview horizon must be between 1 and 90 days")
        proposed_ids = {task.task_id for task in proposed_tasks}
        existing = [
            task for task in self._repository.list_tasks() if task.task_id not in proposed_ids
        ]
        combined = [*existing, *proposed_tasks]
        return [
            self._build_plan(start_date + timedelta(days=offset), combined)
            for offset in range(days)
        ]

    def apply_horizon_batch(self, tasks: list[Task], plans: list[Plan]) -> None:
        if not tasks or not plans:
            raise ValueError("batch requires tasks and plans")
        planned_ids = {block.task_id for plan in plans for block in plan.blocks}
        stored_tasks = [
            replace(task, status=TaskStatus.PLANNED) if task.task_id in planned_ids else task
            for task in tasks
        ]
        self._repository.apply_task_plan_batch(stored_tasks, plans)

    def remove_horizon_batch(self, task_ids: list[str], target_dates: list[date]) -> None:
        removed = set(task_ids)
        remaining = [task for task in self._repository.list_tasks() if task.task_id not in removed]
        plans = [self._build_plan(target, remaining) for target in target_dates]
        self._repository.replace_task_plan_batch(task_ids, plans)

    def get_task(self, task_id: str) -> Task:
        task = self._repository.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        return task

    def list_tasks(self) -> list[Task]:
        return self._repository.list_tasks()

    def current_plan_version(self, target_date: date) -> int | None:
        plan = self._repository.latest_plan(target_date)
        return plan.version if plan else None

    def timeline_plan_id(self, target_date: date) -> str | None:
        plan = self._repository.latest_plan(target_date)
        return plan.plan_id if plan else None

    def timeline(self, horizon_days: int = 14) -> dict[str, object]:
        if not 1 <= horizon_days <= 90:
            raise ValueError("timeline horizon must be between 1 and 90 days")
        tasks = self._repository.list_tasks()
        zone = ZoneInfo(self.settings()["timezone"])
        today = datetime.now(zone).date()
        recurring_dates: set[date] = set()
        for task in tasks:
            if task.preferred_start is None or task.recurrence is None:
                continue
            start = max(today, task.preferred_start.date())
            for offset in range(horizon_days):
                target = start + timedelta(days=offset)
                if task_occurrence(task, target) is not None:
                    recurring_dates.add(target)
        for target in sorted(recurring_dates):
            if self._repository.latest_plan(target) is None:
                plan = self._build_plan(target, tasks)
                self._repository.save_plan(plan)
                self.activate_plan(plan.plan_id)

        projected: list[dict[str, object]] = []
        for task in tasks:
            if task.preferred_start is None:
                continue
            if task.recurrence is None:
                target = task.preferred_start.date()
                projected.append(_timeline_task_dict(task, self._repository.latest_plan(target)))
                continue
            start = max(today, task.preferred_start.date())
            for offset in range(horizon_days):
                target = start + timedelta(days=offset)
                occurrence = task_occurrence(task, target)
                if occurrence is None:
                    continue
                plan = self._repository.latest_plan(target)
                item = _timeline_task_dict(occurrence, plan)
                item["series_id"] = task.task_id
                item["series_start"] = int(task.preferred_start.timestamp() * 1000)
                item["id"] = f"{task.task_id}::{item['start']}"
                projected.append(item)
        return {
            "tasks": sorted(projected, key=lambda item: int(item["start"])),
            "settings": self.settings(),
        }

    def import_legacy_timeline_tasks(self, items: list[dict[str, object]]) -> int:
        zone = ZoneInfo(self.settings()["timezone"])
        imported = 0
        target_dates: set[date] = set()
        for item in items:
            task_id = str(item["id"])
            if self._repository.get_task(task_id) is not None:
                continue
            start = datetime.fromtimestamp(int(item["start"]) / 1000, UTC).astimezone(zone)
            duration = max(1, int((int(item["end"]) - int(item["start"])) / 60_000))
            self.create_task(
                task_id=task_id,
                title=str(item["title"]),
                estimated_minutes=duration,
                priority=3,
                splittable=False,
                preferred_start=start,
                cognitive_intensity=float(item.get("intensity", 0.5)),
                spectrum=float(item.get("spectrum", 0.5)),
                task_type=str(item.get("task_type", "execution")),
                fixed=bool(item.get("fixed", False)),
                source=str(item.get("source", "user")),
                recurrence=item.get("recurrence")
                if isinstance(item.get("recurrence"), dict)
                else None,
                created_at=datetime.fromtimestamp(
                    int(item.get("created_at", item["start"])) / 1000, UTC
                ),
            )
            imported += 1
            target_dates.add(start.date())
        for target in sorted(target_dates):
            plan = self.generate_plan(target)
            self.activate_plan(plan.plan_id)
        return imported

    def set_task_status(self, task_id: str, status: TaskStatus) -> Task:
        task = self._repository.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        updated = replace(task, status=status)
        self._repository.save_task(updated)
        return updated

    def delete_task(self, task_id: str) -> bool:
        return self._repository.delete_task(task_id)

    def create_fixed_block(
        self,
        *,
        title: str,
        target_date: date,
        start_time: time,
        end_time: time,
    ) -> FixedBlock:
        zone = ZoneInfo(self.settings()["timezone"])
        block = FixedBlock(
            block_id=str(uuid4()),
            title=title.strip(),
            start_at=datetime.combine(target_date, start_time, zone),
            end_at=datetime.combine(target_date, end_time, zone),
        )
        self._repository.save_fixed_block(block)
        return block

    def delete_fixed_block(self, block_id: str) -> bool:
        return self._repository.delete_fixed_block(block_id)

    def generate_plan(self, target_date: date) -> Plan:
        plan = self._build_plan(target_date, self._repository.list_tasks())
        self._repository.save_plan(plan)
        return plan

    def _build_plan(self, target_date: date, tasks: list[Task]) -> Plan:
        settings = self.settings()
        zone = ZoneInfo(settings["timezone"])
        day_start = datetime.combine(target_date, time.min, zone)
        availability = AvailabilityWindow(
            start_at=day_start,
            end_at=day_start + timedelta(days=1),
        )
        latest = self._repository.latest_plan(target_date)
        eligible_tasks = [
            task
            for task in tasks
            if not (
                task.preferred_start is None
                and task.status == TaskStatus.PLANNED
                and (
                    latest is None
                    or not any(block.task_id == task.task_id for block in latest.blocks)
                )
            )
        ]
        plan = self._planner.generate(
            tasks=eligible_tasks,
            fixed_blocks=self._repository.list_fixed_blocks(target_date),
            availability=availability,
            target_date=target_date,
            timezone=settings["timezone"],
            version=self._repository.next_plan_version(target_date),
            based_on_version=latest.version if latest else None,
        )
        return plan

    def activate_plan(self, plan_id: str) -> Plan:
        plan = self._repository.activate_plan(plan_id)
        for task_id in {block.task_id for block in plan.blocks}:
            task = self._repository.get_task(task_id)
            if task and task.status == TaskStatus.BACKLOG:
                self._repository.save_task(replace(task, status=TaskStatus.PLANNED))
        return plan

    def snapshot(self, target_date: date) -> dict[str, object]:
        return {
            "type": "chronos.schedule_snapshot",
            "schema_version": 1,
            "target_date": target_date.isoformat(),
            "settings": self.settings(),
            "tasks": [_task_dict(task) for task in self._repository.list_tasks()],
            "fixed_blocks": [
                _fixed_dict(block) for block in self._repository.list_fixed_blocks(target_date)
            ],
            "plan": _plan_dict(self._repository.latest_plan(target_date)),
        }


def _task_dict(task: Task) -> dict[str, object]:
    return {
        "task_id": task.task_id,
        "title": task.title,
        "estimated_minutes": task.estimated_minutes,
        "priority": task.priority,
        "status": task.status.value,
        "created_at": task.created_at.isoformat(),
        "deadline": task.deadline.isoformat() if task.deadline else None,
        "splittable": task.splittable,
        "min_chunk_minutes": task.min_chunk_minutes,
        "preferred_start": task.preferred_start.isoformat() if task.preferred_start else None,
        "cognitive_intensity": task.cognitive_intensity,
        "spectrum": task.spectrum,
        "task_type": task.task_type,
        "fixed": task.fixed,
        "source": task.source,
        "recurrence": task.recurrence,
    }


def _fixed_dict(block: FixedBlock) -> dict[str, object]:
    return {
        "block_id": block.block_id,
        "title": block.title,
        "start_at": block.start_at.isoformat(),
        "end_at": block.end_at.isoformat(),
        "source": block.source,
    }


def _plan_dict(plan: Plan | None) -> dict[str, object] | None:
    if plan is None:
        return None
    return {
        "plan_id": plan.plan_id,
        "version": plan.version,
        "target_date": plan.target_date.isoformat(),
        "timezone": plan.timezone,
        "status": plan.status.value,
        "created_at": plan.created_at.isoformat(),
        "based_on_version": plan.based_on_version,
        "blocks": [
            {
                "block_id": block.block_id,
                "task_id": block.task_id,
                "title": block.title,
                "start_at": block.start_at.isoformat(),
                "end_at": block.end_at.isoformat(),
                "duration_minutes": block.duration_minutes,
                "status": block.status.value,
                "flexibility": block.flexibility,
            }
            for block in plan.blocks
        ],
        "unscheduled": [
            {
                "task_id": item.task_id,
                "title": item.title,
                "remaining_minutes": item.remaining_minutes,
                "reason": item.reason,
            }
            for item in plan.unscheduled
        ],
    }


def _timeline_task_dict(task: Task, plan: Plan | None) -> dict[str, object]:
    blocks = [block for block in plan.blocks if block.task_id == task.task_id] if plan else []
    unscheduled = (
        next((item for item in plan.unscheduled if item.task_id == task.task_id), None)
        if plan
        else None
    )
    start = blocks[0].start_at if blocks else task.preferred_start
    assert start is not None
    end = blocks[-1].end_at if blocks else start + timedelta(minutes=task.estimated_minutes)
    return {
        "id": task.task_id,
        "title": task.title,
        "start": int(start.timestamp() * 1000),
        "end": int(end.timestamp() * 1000),
        "predicted_end": int(end.timestamp() * 1000),
        "intensity": task.cognitive_intensity,
        "spectrum": task.spectrum,
        "fixed": task.fixed,
        "task_type": task.task_type,
        "source": task.source,
        "recurrence": task.recurrence,
        "plan_id": plan.plan_id if plan else None,
        "plan_version": plan.version if plan else None,
        "scheduled": bool(blocks),
        "unscheduled_reason": unscheduled.reason if unscheduled else None,
    }
