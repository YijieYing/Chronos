"""Structured commands produced from natural-language Schedule requests."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal, Protocol

from chronos.schedule.models import Task


CommandType = Literal["create_task", "update_task", "delete_task", "query_schedule"]


@dataclass(frozen=True, slots=True)
class ScheduleCommand:
    type: CommandType
    task_id: str | None = None
    title: str | None = None
    preferred_start: datetime | None = None
    estimated_minutes: int | None = None
    cognitive_intensity: float | None = None
    spectrum: float | None = None
    task_type: str | None = None
    query_date: date | None = None
    recurrence: dict[str, object] | None = None
    fixed: bool | None = None


class ScheduleCommandParser(Protocol):
    def parse(
        self, text: str, now: datetime, tasks: list[Task]
    ) -> ScheduleCommand: ...


class DeterministicScheduleCommandParser:
    """Local parser behind a replaceable semantic-parser port."""

    def parse(self, text: str, now: datetime, tasks: list[Task]) -> ScheduleCommand:
        lowered = text.casefold()
        if re.search(r"查询|查找|看看|有哪些|什么时候|列出|find|show|list|when", lowered):
            target = _resolve_task(text, tasks, required=False)
            return ScheduleCommand(
                type="query_schedule",
                task_id=target.task_id if target else None,
                query_date=_request_date(text, now),
            )
        if re.search(r"删除|移除|取消(?:任务|安排)|delete|remove|cancel", lowered):
            target = _resolve_task(text, tasks, required=True)
            return ScheduleCommand(type="delete_task", task_id=target.task_id)
        if re.search(r"移动|挪到|改到|改为|调整到|延长|缩短|move|reschedule|postpone|resize", lowered):
            target = _resolve_task(text, tasks, required=True)
            start = _preferred_start(text, now) if _has_time_request(text) else target.preferred_start
            duration = _updated_duration(text, target.estimated_minutes)
            return ScheduleCommand(
                type="update_task",
                task_id=target.task_id,
                preferred_start=start,
                estimated_minutes=duration,
            )

        _validate_single_create_request(text)
        duration = _duration(text) or 30
        task_type = _infer_type(text)
        recurrence = _recurrence(text, now)
        return ScheduleCommand(
            type="create_task",
            title=_create_title(text),
            preferred_start=_preferred_start(text, now, recurrence),
            estimated_minutes=duration,
            cognitive_intensity=(
                0.72 if task_type in {"coding", "creative", "research"} else 0.45
            ),
            spectrum={
                "creative": 0.08,
                "coding": 0.28,
                "research": 0.2,
                "communication": 0.65,
                "execution": 0.88,
                "meeting": 0.72,
                "recovery": 0.5,
            }[task_type],
            task_type=task_type,
            recurrence=recurrence,
            fixed=_has_exact_clock(text),
        )


def _resolve_task(text: str, tasks: list[Task], *, required: bool) -> Task | None:
    normalized = _normalize(text)
    matches = [task for task in tasks if _normalize(task.title) in normalized]
    if not matches:
        if required:
            raise ValueError("没有找到要操作的任务，请在指令中写出任务标题")
        return None
    longest = max(len(_normalize(task.title)) for task in matches)
    best = [task for task in matches if len(_normalize(task.title)) == longest]
    if len(best) != 1:
        titles = "、".join(task.title for task in best[:4])
        raise ValueError(f"任务名称不明确，请明确选择：{titles}")
    return best[0]


def _normalize(value: str) -> str:
    return re.sub(r"[\s，,。.!！？?'\"“”‘’:_-]", "", value.casefold())


def _duration(text: str) -> int | None:
    duration_match = re.search(r"(\d+)\s*(?:分钟|min|minutes?)", text, re.I)
    hour_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:小时|hours?|h)\b", text, re.I)
    if duration_match:
        return int(duration_match.group(1))
    if hour_match:
        return round(float(hour_match.group(1)) * 60)
    return None


def _updated_duration(text: str, current: int) -> int:
    amount = _duration(text)
    if amount is None:
        return current
    if re.search(r"延长|增加|加长|extend", text, re.I):
        return current + amount
    if re.search(r"缩短|减少|shorten", text, re.I):
        if amount >= current:
            raise ValueError("缩短后的任务时长必须大于 0 分钟")
        return current - amount
    return amount


def _request_date(text: str, now: datetime) -> date | None:
    if "明天" in text or "tomorrow" in text.casefold():
        return now.date() + timedelta(days=1)
    if "今天" in text or "today" in text.casefold():
        return now.date()
    return None


def _has_time_request(text: str) -> bool:
    return bool(
        re.search(
            r"今天|明天|上午|下午|晚上|早上|中午|today|tomorrow|morning|afternoon|evening|(?:^|\D)(?:[01]?\d|2[0-3])[:：][0-5]\d",
            text,
            re.I,
        )
    )


def _has_exact_clock(text: str) -> bool:
    return bool(re.search(r"(?:^|\D)(?:[01]?\d|2[0-3])[:：][0-5]\d", text))


def _preferred_start(
    text: str, now: datetime, recurrence: dict[str, object] | None = None
) -> datetime:
    day = (
        _request_date(text, now)
        or _first_recurrence_date(recurrence, now)
        or now.date()
    )
    clock_match = re.search(r"(?:^|\D)([01]?\d|2[0-3])[:：]([0-5]\d)", text)
    if clock_match:
        hour, minute = int(clock_match.group(1)), int(clock_match.group(2))
    elif "下午" in text or "afternoon" in text.casefold():
        hour, minute = 14, 0
    elif "晚上" in text or "evening" in text.casefold():
        hour, minute = 19, 0
    elif "中午" in text:
        hour, minute = 12, 0
    elif "上午" in text or "早上" in text or "morning" in text.casefold():
        hour, minute = 9, 0
    else:
        rounded = now + timedelta(minutes=20)
        minute = ((rounded.minute + 14) // 15 * 15) % 60
        hour = rounded.hour + (1 if rounded.minute >= 46 else 0)
        if hour >= 24:
            day += timedelta(days=1)
            hour %= 24
    return datetime.combine(day, datetime.min.time(), now.tzinfo).replace(
        hour=hour, minute=minute
    )


def _create_title(text: str) -> str:
    title = re.sub(
        r"帮我|请|给我|安排|创建|新建|找个时间|今天|明天|上午|下午|晚上|早上|中午|每天|每日|每周(?:[一二三四五六日天、,，至到和\-]*)|工作日|周末|周期性任务|today|tomorrow|morning|afternoon|evening|daily|every\s+day|weekly|every\s+week|(?:^|\D)(?:[01]?\d|2[0-3])[:：][0-5]\d|\d+(?:\.\d+)?\s*(?:分钟|min|minutes?|小时|hours?|h)",
        " ",
        text,
        flags=re.I,
    ).strip(" ，,。")
    title = re.sub(
        r"(?:^|\s)(?:[01]?\d|2[0-3])[:：][0-5]\d|\d+(?:\.\d+)?\s*(?:分钟|min|minutes?|小时|hours?|h)",
        " ",
        title,
        flags=re.I,
    ).strip(" ，,。")
    cleaned = re.sub(r"\s+", " ", title) or "New task"
    if len(cleaned) > 80 or "\n" in title:
        raise ValueError("无法可靠提取任务名称，请把一个任务及其周期写成一条简短指令")
    return cleaned


def _validate_single_create_request(text: str) -> None:
    list_items = re.findall(r"(?m)^\s*(?:[-*+]|\d+[.)])\s+\S", text)
    if len(list_items) > 1:
        raise ValueError("Agent 当前一次只能创建一个任务；请逐条提交时间计划")


def _recurrence(text: str, now: datetime) -> dict[str, object] | None:
    if re.search(r"每天|每日|daily|every\s+day", text, re.I):
        return {"frequency": "daily"}
    if not re.search(r"每周|工作日|周末|weekly|every\s+week", text, re.I):
        return None
    if "工作日" in text:
        weekdays = [1, 2, 3, 4, 5]
    elif "周末" in text:
        weekdays = [0, 6]
    elif re.search(r"周一\s*(?:到|至|-)\s*周五", text):
        weekdays = [1, 2, 3, 4, 5]
    else:
        compact = re.search(r"每周([一二三四五六日天、,，和及\s]+)", text)
        names = re.findall(
            r"[一二三四五六日天]", compact.group(1) if compact else ""
        ) or re.findall(r"(?:周|星期)([一二三四五六日天])", text)
        mapping = {
            "一": 1,
            "二": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "日": 0,
            "天": 0,
        }
        weekdays = sorted({mapping[name] for name in names})
    if not weekdays:
        weekdays = [(now.weekday() + 1) % 7]
    return {"frequency": "weekly", "weekdays": weekdays}


def _first_recurrence_date(
    recurrence: dict[str, object] | None, now: datetime
) -> date | None:
    if recurrence is None or recurrence.get("frequency") != "weekly":
        return None
    weekdays = {int(value) for value in recurrence.get("weekdays", [])}
    for offset in range(7):
        candidate = now.date() + timedelta(days=offset)
        if (candidate.weekday() + 1) % 7 in weekdays:
            return candidate
    return None


def _infer_type(text: str) -> str:
    if re.search(r"代码|开发|coding|backend|frontend", text, re.I):
        return "coding"
    if re.search(r"论文|阅读|研究|paper|research", text, re.I):
        return "research"
    if re.search(r"会议|同步|meeting|sync", text, re.I):
        return "meeting"
    if re.search(r"休息|散步|break|rest", text, re.I):
        return "recovery"
    if re.search(r"回复|整理|review|admin", text, re.I):
        return "execution"
    return "creative"
