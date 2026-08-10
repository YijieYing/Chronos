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

        duration = _duration(text) or 30
        task_type = _infer_type(text)
        return ScheduleCommand(
            type="create_task",
            title=_create_title(text),
            preferred_start=_preferred_start(text, now),
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


def _preferred_start(text: str, now: datetime) -> datetime:
    day = _request_date(text, now) or now.date()
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
        r"帮我|请|给我|安排|创建|新建|找个时间|今天|明天|上午|下午|晚上|早上|中午|today|tomorrow|morning|afternoon|evening|(?:^|\D)(?:[01]?\d|2[0-3])[:：][0-5]\d|\d+(?:\.\d+)?\s*(?:分钟|min|minutes?|小时|hours?|h)",
        " ",
        text,
        flags=re.I,
    ).strip(" ，,。")
    return re.sub(r"\s+", " ", title) or "New task"


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
