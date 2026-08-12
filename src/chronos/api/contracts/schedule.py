from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

TASK_TYPES = {
    "creative",
    "coding",
    "research",
    "communication",
    "execution",
    "meeting",
    "recovery",
}
TASK_SOURCES = {"user", "agent", "schedule"}


def scheduled_task_values(
    payload: dict[str, object],
    timezone: str,
    *,
    task_id: str | None = None,
) -> dict[str, object]:
    title = str(payload["title"]).strip()
    start_ms = int(payload["start"])
    end_ms = int(payload["end"])
    if not title or end_ms <= start_ms:
        raise ValueError("task title and positive duration are required")
    task_type = str(payload.get("task_type", "execution"))
    source = str(payload.get("source", "user"))
    if task_type not in TASK_TYPES:
        raise ValueError(f"unsupported task type: {task_type}")
    if source not in TASK_SOURCES:
        raise ValueError(f"unsupported task source: {source}")
    recurrence = _recurrence(payload.get("recurrence"))
    zone = ZoneInfo(timezone)
    values: dict[str, object] = {
        "title": title,
        "estimated_minutes": max(1, round((end_ms - start_ms) / 60_000)),
        "priority": int(payload.get("priority", 3)),
        "preferred_start": datetime.fromtimestamp(start_ms / 1000, UTC).astimezone(zone),
        "cognitive_intensity": float(payload.get("intensity", 0.5)),
        "spectrum": float(payload.get("spectrum", 0.5)),
        "task_type": task_type,
        "fixed": bool(payload.get("fixed", False)),
        "source": source,
        "recurrence": recurrence,
    }
    if task_id is not None:
        values["task_id"] = task_id
    return values


def _recurrence(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("recurrence must be an object")
    frequency = str(value.get("frequency", ""))
    result: dict[str, object] = {"frequency": frequency}
    if frequency not in {"daily", "weekly"}:
        raise ValueError("recurrence frequency must be daily or weekly")
    if frequency == "weekly":
        weekdays = sorted({int(day) for day in value.get("weekdays", [])})
        if not weekdays or any(day < 0 or day > 6 for day in weekdays):
            raise ValueError("weekdays must contain values from 0 to 6")
        result["weekdays"] = weekdays
    if value.get("until") not in {None, ""}:
        result["until"] = date.fromisoformat(str(value["until"])).isoformat()
    return result
