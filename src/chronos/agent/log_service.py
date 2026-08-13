"""Append-only Chronos Log application service."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from chronos.agent.models import ChronosLogEntry, LogEventType, TimelineReference
from chronos.agent.ports import ChronosLogRepository


class ChronosLogService:
    def __init__(self, repository: ChronosLogRepository) -> None:
        self._repository = repository

    def append(
        self,
        event_type: LogEventType,
        message: str,
        *,
        operation_id: str | None = None,
        references: tuple[TimelineReference, ...] = (),
        metadata: dict[str, object] | None = None,
        entry_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> ChronosLogEntry:
        entry = ChronosLogEntry(
            id=entry_id or str(uuid4()),
            event_type=event_type,
            occurred_at=occurred_at or datetime.now(UTC),
            message=message,
            operation_id=operation_id,
            references=references,
            metadata=metadata or {},
        )
        self._repository.append(entry)
        return entry

    def list(self, limit: int = 200) -> list[ChronosLogEntry]:
        if not 1 <= limit <= 1000:
            raise ValueError("Chronos Log limit must be between 1 and 1000")
        return self._repository.list(limit)

    def has_operation(self, operation_id: str) -> bool:
        return self._repository.has_operation(operation_id)
