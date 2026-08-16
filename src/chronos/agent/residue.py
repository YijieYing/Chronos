"""Persistent capability-gap records produced by Interpreter Residue."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from chronos.agent.meaning import ResidueStatus, Snapshot


@dataclass(frozen=True, slots=True)
class Record:
    id: str
    operation_id: str
    snapshot_id: str
    snapshot_version: int
    event_id: str | None
    item_id: str
    start: int
    end: int
    text: str
    reason: str
    hint: str | None
    interpreter_version: str
    status: ResidueStatus
    created_at: datetime


class Repository(Protocol):
    def add(self, records: tuple[Record, ...]) -> int: ...
    def list(self, status: ResidueStatus | None = None, limit: int = 500) -> list[Record]: ...
    def update(self, record_id: str, status: ResidueStatus) -> Record: ...


class Registry:
    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    def capture(self, operation_id: str, snapshot: Snapshot) -> int:
        if not operation_id:
            raise ValueError("operation id is required")
        now = datetime.now(UTC)
        records: list[Record] = []
        for event in snapshot.events:
            records.extend(
                _record(operation_id, snapshot, event.id, item, now)
                for item in event.residue
            )
        for directive in snapshot.directives:
            records.extend(
                _record(operation_id, snapshot, None, item, now)
                for item in directive.residue
            )
        return self._repository.add(tuple(records))

    def list(
        self, status: ResidueStatus | None = None, limit: int = 500
    ) -> list[Record]:
        return self._repository.list(status, limit)

    def resolve(self, record_id: str, status: ResidueStatus) -> Record:
        if status == ResidueStatus.OPEN:
            raise ValueError("resolved Residue cannot return to open")
        return self._repository.update(record_id, status)

    def export(self, limit: int = 500) -> list[dict[str, object]]:
        return [
            {
                "id": item.id,
                "operation_id": item.operation_id,
                "snapshot_id": item.snapshot_id,
                "snapshot_version": item.snapshot_version,
                "event_id": item.event_id,
                "item_id": item.item_id,
                "span": {"start": item.start, "end": item.end},
                "text": item.text,
                "reason": item.reason,
                "hint": item.hint,
                "interpreter_version": item.interpreter_version,
                "status": item.status.value,
                "created_at": item.created_at.isoformat(),
            }
            for item in self._repository.list(None, limit)
        ]


def _record(operation_id, snapshot, event_id, residue, now) -> Record:
    key = (
        f"{operation_id}:{snapshot.id}:{snapshot.version}:{event_id}:"
        f"{residue.item_id}:{residue.span.start}:{residue.span.end}"
    )
    return Record(
        id=str(uuid5(NAMESPACE_URL, key)),
        operation_id=operation_id,
        snapshot_id=snapshot.id,
        snapshot_version=snapshot.version,
        event_id=event_id,
        item_id=residue.item_id,
        start=residue.span.start,
        end=residue.span.end,
        text=_sanitize(residue.text),
        reason=residue.reason.value,
        hint=residue.hint,
        interpreter_version=residue.interpreter_version,
        status=residue.status,
        created_at=now,
    )


def _sanitize(text: str) -> str:
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email]", text)
    value = re.sub(r"\b(?:sk|key)-[A-Za-z0-9_-]{8,}\b", "[secret]", value)
    return value[:500]
