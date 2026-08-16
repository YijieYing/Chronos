"""Resolve Events against time and availability into one concrete Plan."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from chronos.agent.meaning import (
    DurationKind,
    Event,
    Kind,
    Period,
    RequestKind,
    Snapshot,
    TimeKind,
)
from chronos.agent.plan import Change, Horizon, Plan, ReminderDraft, TaskDraft, Window
from chronos.agent.state import State


class PlanningError(ValueError):
    pass


class Planner:
    def plan(
        self,
        snapshot: Snapshot,
        state: State,
        occupied: tuple[Window, ...] = (),
    ) -> Plan:
        changes: list[Change] = []
        horizons: list[Window] = []
        for event in snapshot.events:
            if event.gaps or event.residue:
                raise PlanningError(f"Event {event.id} has unresolved meaning")
            change, horizon = (
                _task(event, state, occupied)
                if event.kind == Kind.TASK
                else _reminder(event, state)
            )
            changes.append(change)
            horizons.append(horizon)
        if snapshot.directives:
            raise PlanningError("Directive planning is not implemented in the first slice")
        if not changes:
            raise PlanningError("Snapshot has no plannable Events")
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
            explanation=f"已规划 {len(changes)} 个时间对象。",
        )


def _task(
    event: Event,
    state: State,
    occupied: tuple[Window, ...],
) -> tuple[Change, Window]:
    if event.kind != Kind.TASK or event.request.type != RequestKind.ADD:
        raise PlanningError("first Planner slice supports only add Task Events")
    if event.duration is None or event.duration.type != DurationKind.EXACT:
        raise PlanningError("first Planner slice requires an exact duration")
    if event.time.type != TimeKind.PERIOD or event.time.period is None:
        raise PlanningError("first Planner slice requires a symbolic period")
    window = _period(event.time.period, state)
    duration_ms = event.duration.minutes * 60_000
    start = _slot(window, duration_ms, occupied, state.now)
    if start is None:
        raise PlanningError("no available slot inside the requested period")
    title = " · ".join(item.text.strip() for item in event.content if item.text.strip())
    if not title:
        raise PlanningError("Event has no displayable source content")
    task = TaskDraft(
        id=str(uuid5(NAMESPACE_URL, f"{event.id}:task")),
        title=title,
        start=start,
        duration=event.duration.minutes,
        window=window,
    )
    return Change(event.id, RequestKind.ADD, task=task), window


def _reminder(event: Event, state: State) -> tuple[Change, Window]:
    if event.kind != Kind.REMINDER or event.request.type != RequestKind.ADD:
        raise PlanningError("Planner supports add Task or Reminder Events")
    title = " · ".join(item.text.strip() for item in event.content if item.text.strip())
    if not title:
        raise PlanningError("Event has no displayable source content")
    reminder_id = str(uuid5(NAMESPACE_URL, f"{event.id}:reminder"))
    if event.time.type == TimeKind.POINT and event.time.start is not None:
        if event.time.start < state.now:
            raise PlanningError("prospective reminder cannot be placed in the past")
        horizon = Window(event.time.start, event.time.start + 1)
        draft = ReminderDraft(reminder_id, title, "time", at=event.time.start)
    elif event.time.type == TimeKind.RANGE:
        assert event.time.start is not None and event.time.end is not None
        if event.time.end <= state.now:
            raise PlanningError("prospective reminder window has already ended")
        horizon = Window(max(event.time.start, state.now), event.time.end)
        draft = ReminderDraft(reminder_id, title, "window", window=horizon)
    elif event.time.type in {TimeKind.PERIOD, TimeKind.FLEXIBLE} and event.time.period is not None:
        period = _period(event.time.period, state)
        horizon = Window(max(period.start, state.now), period.end)
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
