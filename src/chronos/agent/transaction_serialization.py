"""Wire representation for Runtime adjustment transactions."""

from __future__ import annotations

from datetime import datetime

from chronos.agent.models import AdjustmentTransaction, ScheduleSnapshot, TransactionStatus
from chronos.agent.serialization import (
    timeline_operation_from_dict,
    timeline_operation_to_dict,
)


def transaction_to_dict(value: AdjustmentTransaction) -> dict[str, object]:
    return {
        "id": value.id,
        "operation_id": value.operation_id,
        "before_state": _snapshot_to_dict(value.before_state),
        "operations": [timeline_operation_to_dict(item) for item in value.operations],
        "after_state": _snapshot_to_dict(value.after_state),
        "status": value.status.value,
        "created_at": value.created_at.isoformat(),
    }


def transaction_from_dict(value: dict[str, object]) -> AdjustmentTransaction:
    operations = value.get("operations")
    before = value.get("before_state")
    after = value.get("after_state")
    if not isinstance(operations, list):
        raise ValueError("transaction operations must be an array")
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise ValueError("transaction snapshots must be objects")
    return AdjustmentTransaction(
        id=str(value["id"]),
        operation_id=str(value["operation_id"]),
        before_state=_snapshot_from_dict(before),
        operations=tuple(
            timeline_operation_from_dict(item)
            for item in operations
            if isinstance(item, dict)
        ),
        after_state=_snapshot_from_dict(after),
        status=TransactionStatus(str(value["status"])),
        created_at=datetime.fromisoformat(str(value["created_at"])),
    )


def _snapshot_to_dict(value: ScheduleSnapshot) -> dict[str, object]:
    return {
        "captured_at": value.captured_at.isoformat(),
        "tasks": [dict(item) for item in value.tasks],
        "reminders": [dict(item) for item in value.reminders],
        "plan_versions": dict(value.plan_versions),
    }


def _snapshot_from_dict(value: dict[str, object]) -> ScheduleSnapshot:
    tasks = value.get("tasks", [])
    reminders = value.get("reminders", [])
    versions = value.get("plan_versions", {})
    return ScheduleSnapshot(
        captured_at=datetime.fromisoformat(str(value["captured_at"])),
        tasks=tuple(dict(item) for item in tasks if isinstance(item, dict)),
        reminders=tuple(dict(item) for item in reminders if isinstance(item, dict)),
        plan_versions={str(key): item for key, item in versions.items()}
        if isinstance(versions, dict)
        else {},
    )
