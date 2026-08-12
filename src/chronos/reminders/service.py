from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from chronos.reminders.models import Reminder, ReminderStatus
from chronos.reminders.ports import ReminderRepository


class ReminderService:
    def __init__(self, repository: ReminderRepository) -> None:
        self._repository = repository

    def list(self) -> list[dict[str, object]]:
        return [reminder_dict(item) for item in self._repository.list()]

    def create(
        self,
        *,
        title: str,
        trigger_type: str,
        trigger_at: datetime | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        delivery: str = "exact",
        priority: int = 3,
        source: str = "user",
        reminder_id: str | None = None,
        created_at: datetime | None = None,
    ) -> Reminder:
        reminder = Reminder(
            reminder_id=reminder_id or str(uuid4()),
            title=title.strip(),
            trigger_type=trigger_type,
            trigger_at=trigger_at,
            window_start=window_start,
            window_end=window_end,
            delivery=delivery,
            priority=priority,
            status=ReminderStatus.PENDING,
            created_at=created_at or datetime.now(UTC),
            source=source,
        )
        self._repository.save(reminder)
        return reminder

    def set_status(self, reminder_id: str, status: ReminderStatus) -> Reminder:
        current = self._repository.get(reminder_id)
        if current is None:
            raise KeyError(reminder_id)
        updated = replace(current, status=status)
        self._repository.save(updated)
        return updated

    def delete(self, reminder_id: str) -> bool:
        return self._repository.delete(reminder_id)


def reminder_dict(reminder: Reminder) -> dict[str, object]:
    trigger: dict[str, object]
    if reminder.trigger_type == "time":
        assert reminder.trigger_at is not None
        trigger = {"type": "time", "at": int(reminder.trigger_at.timestamp() * 1000)}
    else:
        assert reminder.window_start is not None and reminder.window_end is not None
        trigger = {
            "type": "window",
            "start": int(reminder.window_start.timestamp() * 1000),
            "end": int(reminder.window_end.timestamp() * 1000),
        }
    return {
        "id": reminder.reminder_id,
        "title": reminder.title,
        "trigger": trigger,
        "delivery": reminder.delivery,
        "priority": reminder.priority,
        "status": reminder.status.value,
        "source": reminder.source,
        "created_at": reminder.created_at.isoformat(),
    }
