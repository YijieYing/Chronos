"""Resolve Events against time and availability into one concrete Plan."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, time, timedelta
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from chronos.agent.meaning import (
    DurationKind,
    Event,
    Field,
    Kind,
    Period,
    Precision,
    Reference,
    RequestKind,
    Snapshot,
    TimeKind,
)
from chronos.agent.plan import (
    Change,
    Conflict,
    Horizon,
    Plan,
    ReminderDraft,
    TaskDraft,
    Window,
)
from chronos.agent.state import State


class PlanningError(ValueError):
    pass


class Planner:
    def plan(
        self,
        snapshot: Snapshot,
        state: State,
        occupied: tuple[Window, ...] = (),
        *,
        task_ids: tuple[str, ...] | None = None,
        reminder_ids: tuple[str, ...] | None = None,
        task_drafts: Mapping[str, TaskDraft] | None = None,
        reminder_drafts: Mapping[str, ReminderDraft] | None = None,
    ) -> Plan:
        changes: list[Change] = []
        conflicts: list[Conflict] = []
        horizons: list[Window] = []
        for event in snapshot.events:
            if event.gaps or event.residue:
                raise PlanningError(f"Event {event.id} has unresolved meaning")
            try:
                change, horizon = _change(
                    event,
                    state,
                    occupied,
                    set(task_ids) if task_ids is not None else None,
                    set(reminder_ids) if reminder_ids is not None else None,
                    task_drafts,
                    reminder_drafts,
                )
            except PlanningError as error:
                if "does not exist" not in str(error):
                    raise
                conflicts.append(Conflict(event.id, "target_missing", str(error)))
                continue
            changes.append(change)
            horizons.append(horizon)
        if snapshot.directives:
            raise PlanningError("Directive planning is not implemented in the first slice")
        if not changes and not conflicts:
            raise PlanningError("Snapshot has no plannable Events")
        if not horizons:
            horizons.append(Window(state.now, state.now + 1))
        horizon = Horizon(
            max(state.now, min(item.start for item in horizons)),
            max(item.end for item in horizons),
        )
        return Plan(
            id=str(uuid5(NAMESPACE_URL, f"{snapshot.id}:plan:{snapshot.version}")),
            snapshot_id=snapshot.id,
            snapshot_version=snapshot.version,
            horizon=horizon,
            changes=tuple(changes),
            conflicts=tuple(conflicts),
            explanation=f"已规划 {len(changes)} 个时间对象。",
        )


def _change(
    event: Event,
    state: State,
    occupied: tuple[Window, ...],
    task_ids: set[str] | None,
    reminder_ids: set[str] | None,
    task_drafts: Mapping[str, TaskDraft] | None,
    reminder_drafts: Mapping[str, ReminderDraft] | None,
) -> tuple[Change, Window]:
    if event.request.type == RequestKind.DELETE:
        target = _target(event, task_ids, reminder_ids)
        return (
            Change(
                event.id,
                RequestKind.DELETE,
                target_id=target.id,
                target_type=target.type,
            ),
            Window(state.now, state.now + 1),
        )
    if event.request.type == RequestKind.EDIT:
        return _edit(event, state, task_ids, reminder_ids, task_drafts, reminder_drafts)
    if event.kind == Kind.TASK:
        return _task(event, state, occupied)
    if event.kind == Kind.REMINDER:
        return _reminder(event, state)
    raise PlanningError("Planner supports Task or Reminder Events")


def _edit(
    event: Event,
    state: State,
    task_ids: set[str] | None,
    reminder_ids: set[str] | None,
    task_drafts: Mapping[str, TaskDraft] | None,
    reminder_drafts: Mapping[str, ReminderDraft] | None,
) -> tuple[Change, Window]:
    target = _target(event, task_ids, reminder_ids)
    fields = set(event.request.fields)
    if target.type == "task":
        if event.kind != Kind.TASK or fields - {
            Field.CONTENT, Field.TIME, Field.DURATION, Field.RECURRENCE
        }:
            raise PlanningError("Planner supports Task content, time, duration, and recurrence edits")
        if Field.CONTENT in fields or Field.RECURRENCE in fields:
            if task_drafts is None or target.id not in task_drafts:
                raise PlanningError(f"current Task {target.id} is required for full edits")
            current = task_drafts[target.id]
            title = current.title
            if Field.CONTENT in fields:
                title = _title(event)
                if not title:
                    raise PlanningError("Task title edit requires source content")
            start = current.start
            if Field.TIME in fields:
                if event.time.type != TimeKind.POINT or event.time.start is None:
                    raise PlanningError("Task time edit requires an exact point")
                start = event.time.start
            duration = current.duration
            if Field.DURATION in fields:
                if event.duration is None or event.duration.type != DurationKind.EXACT:
                    raise PlanningError("Task duration edit requires an exact duration")
                duration = event.duration.minutes
            recurrence = current.recurrence
            if Field.RECURRENCE in fields:
                if event.recurrence is None:
                    raise PlanningError("Task recurrence edit requires recurrence")
                recurrence = event.recurrence
            if start < state.now:
                raise PlanningError("prospective Task edit cannot move into the past")
            draft = TaskDraft(
                current.id,
                title,
                start,
                duration,
                current.window,
                current.fixed,
                current.priority,
                recurrence,
            )
            return (
                Change(
                    event.id,
                    RequestKind.EDIT,
                    task=draft,
                    target_id=target.id,
                    target_type=target.type,
                ),
                Window(start, start + 1),
            )
        at: int | None = None
        duration: int | None = None
        if Field.TIME in fields:
            if (
                event.time.type != TimeKind.POINT
                or event.time.start is None
                or event.time.precision != Precision.EXACT
            ):
                raise PlanningError("Task time edit requires an exact point")
            if event.time.start < state.now:
                raise PlanningError("prospective Task edit cannot move into the past")
            at = event.time.start
        if Field.DURATION in fields:
            if event.duration is None or event.duration.type != DurationKind.EXACT:
                raise PlanningError("Task duration edit requires an exact duration")
            duration = event.duration.minutes
        horizon_start = at if at is not None else state.now
        return (
            Change(
                event.id,
                RequestKind.EDIT,
                target_id=target.id,
                target_type=target.type,
                at=at,
                duration=duration,
            ),
            Window(horizon_start, horizon_start + 1),
        )
    if target.type != "reminder" or event.kind != Kind.REMINDER:
        raise PlanningError("Planner supports Reminder content and time edits")
    if Field.CONTENT in fields:
        if fields != {Field.CONTENT}:
            raise PlanningError("Reminder content edits cannot combine with other fields yet")
        if reminder_drafts is None or target.id not in reminder_drafts:
            raise PlanningError(f"current Reminder {target.id} is required for content edits")
        current = reminder_drafts[target.id]
        title = _title(event)
        if not title:
            raise PlanningError("Reminder title edit requires source content")
        return (
            Change(
                event.id,
                RequestKind.EDIT,
                reminder=ReminderDraft(
                    current.id,
                    title,
                    current.trigger,
                    current.at,
                    current.window,
                    current.delivery,
                    current.priority,
                ),
                target_id=target.id,
                target_type=target.type,
            ),
            Window(state.now, state.now + 1),
        )
    if fields != {Field.TIME}:
        raise PlanningError("Planner supports Reminder content and time edits")
    if event.time.type == TimeKind.POINT and event.time.start is not None:
        if event.time.start < state.now:
            raise PlanningError("prospective Reminder edit cannot move into the past")
        return (
            Change(
                event.id,
                RequestKind.EDIT,
                target_id=target.id,
                target_type=target.type,
                at=event.time.start,
            ),
            Window(event.time.start, event.time.start + 1),
        )
    window = _reminder_window(event, state)
    return (
        Change(
            event.id,
            RequestKind.EDIT,
            target_id=target.id,
            target_type=target.type,
            window=window,
        ),
        window,
    )


def _target(
    event: Event,
    task_ids: set[str] | None,
    reminder_ids: set[str] | None,
) -> Reference:
    target = event.request.target
    if target is None or target.type not in {"task", "reminder"}:
        raise PlanningError("edit/delete Event requires a Task or Reminder target")
    if target.type != event.kind.value:
        raise PlanningError("Event kind must match its target")
    known = task_ids if target.type == "task" else reminder_ids
    if known is not None and target.id not in known:
        raise PlanningError(f"target {target.type} {target.id} does not exist")
    return target


def _task(
    event: Event,
    state: State,
    occupied: tuple[Window, ...],
) -> tuple[Change, Window]:
    if event.kind != Kind.TASK or event.request.type != RequestKind.ADD:
        raise PlanningError("first Planner slice supports only add Task Events")
    if event.duration is None or event.duration.type != DurationKind.EXACT:
        raise PlanningError("first Planner slice requires an exact duration")
    fixed = False
    if event.time.type == TimeKind.PERIOD and event.time.period is not None:
        window = _period(event.time.period, state)
    elif event.time.type == TimeKind.RANGE:
        assert event.time.start is not None and event.time.end is not None
        window = Window(max(event.time.start, state.now), event.time.end)
    elif event.time.type == TimeKind.POINT and event.time.start is not None:
        if event.time.start < state.now:
            raise PlanningError("prospective Task cannot be placed in the past")
        window = Window(
            event.time.start,
            event.time.start + event.duration.minutes * 60_000,
        )
        fixed = True
    else:
        raise PlanningError("Task creation requires a symbolic period or concrete range")
    duration_ms = event.duration.minutes * 60_000
    start = _slot(window, duration_ms, occupied, state.now)
    if start is None:
        raise PlanningError("no available slot inside the requested period")
    title = _title(event)
    if not title:
        raise PlanningError("Event has no displayable source content")
    task = TaskDraft(
        id=str(uuid5(NAMESPACE_URL, f"{event.id}:task")),
        title=title,
        start=start,
        duration=event.duration.minutes,
        window=window,
        fixed=fixed,
        recurrence=event.recurrence,
    )
    return Change(event.id, RequestKind.ADD, task=task), window


def _reminder(event: Event, state: State) -> tuple[Change, Window]:
    if event.kind != Kind.REMINDER or event.request.type != RequestKind.ADD:
        raise PlanningError("Planner supports add Task or Reminder Events")
    title = _title(event)
    if not title:
        raise PlanningError("Event has no displayable source content")
    reminder_id = str(uuid5(NAMESPACE_URL, f"{event.id}:reminder"))
    if event.time.type == TimeKind.POINT and event.time.start is not None:
        if event.time.start < state.now:
            raise PlanningError("prospective reminder cannot be placed in the past")
        horizon = Window(event.time.start, event.time.start + 1)
        draft = ReminderDraft(reminder_id, title, "time", at=event.time.start)
    elif event.time.type in {TimeKind.RANGE, TimeKind.PERIOD, TimeKind.FLEXIBLE}:
        horizon = _reminder_window(event, state)
        draft = ReminderDraft(
            reminder_id,
            title,
            "window",
            window=horizon,
            delivery="context-aware" if event.time.type == TimeKind.FLEXIBLE else "exact",
        )
    else:
        raise PlanningError("Reminder requires point, range, or symbolic period time")
    return Change(event.id, RequestKind.ADD, reminder=draft), horizon


def _title(event: Event) -> str:
    title = event.title or " · ".join(
        item.text.strip() for item in event.content if item.text.strip()
    )
    if not title.strip():
        raise PlanningError("Event has no displayable title")
    return title.strip()


def _reminder_window(event: Event, state: State) -> Window:
    if event.time.type == TimeKind.RANGE:
        assert event.time.start is not None and event.time.end is not None
        if event.time.end <= state.now:
            raise PlanningError("prospective reminder window has already ended")
        return Window(max(event.time.start, state.now), event.time.end)
    if event.time.type in {TimeKind.PERIOD, TimeKind.FLEXIBLE} and event.time.period is not None:
        period = _period(event.time.period, state)
        return Window(max(period.start, state.now), period.end)
    raise PlanningError("Reminder time edit requires a point, range, or symbolic period")


def _period(period: Period, state: State) -> Window:
    zone = ZoneInfo(state.timezone)
    now = datetime.fromtimestamp(state.now / 1000, zone)
    hours = {
        Period.MORNING: (6, 12),
        Period.AFTERNOON: (12, 18),
        Period.EVENING: (18, 24),
    }[period]
    day = now.date()
    start = datetime.combine(day, time(hour=hours[0]), zone)
    end = datetime.combine(day + timedelta(days=hours[1] // 24), time(hour=hours[1] % 24), zone)
    if now >= end:
        start += timedelta(days=1)
        end += timedelta(days=1)
    return Window(_milliseconds(start), _milliseconds(end))


def _slot(
    window: Window,
    duration: int,
    occupied: tuple[Window, ...],
    now: int,
) -> int | None:
    cursor = max(window.start, now)
    for item in sorted(occupied, key=lambda value: value.start):
        if item.end <= cursor or item.start >= window.end:
            continue
        if cursor + duration <= item.start:
            return cursor
        cursor = max(cursor, item.end)
    return cursor if cursor + duration <= window.end else None


def _milliseconds(value: datetime) -> int:
    return int(value.timestamp() * 1000)
