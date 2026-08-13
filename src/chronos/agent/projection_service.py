"""Read model for active Timeline projections and legacy proposal compatibility."""

from __future__ import annotations

from chronos.agent.models import (
    OperationState,
    ProjectionKind,
    ProjectionVisualState,
    TimelineProjection,
    TimelineReference,
)
from chronos.agent.service import OperationStore


class ProjectionService:
    def __init__(self, operations: OperationStore) -> None:
        self._operations = operations

    def list_active(
        self, legacy_proposals: tuple[dict[str, object], ...] = ()
    ) -> list[TimelineProjection]:
        operations = self._operations.active()
        active = [
            projection
            for operation in operations
            if operation.state != OperationState.STALE
            for projection in operation.projections
        ]
        operation_ids = {item.id for item in operations}
        for proposal in legacy_proposals:
            operation_id = str(proposal["proposal_id"])
            if operation_id in operation_ids:
                continue
            active.extend(proposal_projections(proposal))
        return active


def proposal_projections(proposal: dict[str, object]) -> tuple[TimelineProjection, ...]:
    status = str(proposal.get("status", ""))
    if status not in {"pending", "needs_clarification"}:
        return ()
    operation_id = str(proposal["proposal_id"])
    kind = (
        ProjectionKind.CLARIFICATION
        if status == "needs_clarification"
        else ProjectionKind.PROPOSAL
    )
    visual_state = (
        ProjectionVisualState.INCOMPLETE
        if status == "needs_clarification"
        else ProjectionVisualState.PROPOSED
    )
    items: list[TimelineProjection] = []
    candidates = _projection_candidates(proposal)
    for index, candidate in enumerate(candidates):
        target, start, end, metadata = candidate
        items.append(
            TimelineProjection(
                id=f"legacy:{operation_id}:{index}",
                operation_id=operation_id,
                type=kind,
                target=target,
                visual_state=visual_state,
                start=start,
                end=end,
                metadata={"legacy_adapter": True, **metadata},
            )
        )
    return tuple(items)


def _projection_candidates(
    proposal: dict[str, object],
) -> list[tuple[TimelineReference, int | None, int | None, dict[str, object]]]:
    candidates = []
    seen: set[tuple[str, str | None, int | None, int | None]] = set()
    changes = proposal.get("changes", [])
    if not isinstance(changes, list):
        changes = []
    task_operations = {
        str(change["task_id"]): str(change.get("operation", "update"))
        for change in changes
        if isinstance(change, dict) and change.get("task_id")
    }

    def add(
        reference: TimelineReference,
        start: int | None,
        end: int | None,
        metadata: dict[str, object],
    ) -> None:
        key = (reference.type, reference.id, start, end)
        if key not in seen:
            seen.add(key)
            candidates.append((reference, start, end, metadata))

    results = proposal.get("results", [])
    if not isinstance(results, list):
        results = []
    for task in [proposal.get("proposed_task"), *results]:
        if not isinstance(task, dict) or task.get("start") is None or task.get("end") is None:
            continue
        task_id = str(task.get("series_id") or task.get("id"))
        add(
            TimelineReference("task", id=task_id),
            int(task["start"]),
            int(task["end"]),
            {
                "object_type": "task",
                "title": str(task.get("title", "Proposed task")),
                "fixed": bool(task.get("fixed", False)),
                "operation": task_operations.get(task_id, "update"),
            },
        )
    reminder_drafts = proposal.get("reminder_drafts", [])
    if not isinstance(reminder_drafts, list):
        reminder_drafts = []
    for draft in reminder_drafts:
        reminder = draft.get("reminder") if isinstance(draft, dict) else None
        trigger = reminder.get("trigger") if isinstance(reminder, dict) else None
        if not isinstance(reminder, dict) or not isinstance(trigger, dict):
            continue
        if trigger.get("type") == "time":
            start = end = None
            trigger_metadata = {"at": int(trigger["at"])}
        else:
            start, end = int(trigger["start"]), int(trigger["end"])
            trigger_metadata = {}
        add(
            TimelineReference("reminder", id=str(reminder["id"])),
            start,
            end,
            {
                "object_type": "reminder",
                "title": str(reminder.get("title", "Proposed reminder")),
                "trigger_type": str(trigger.get("type", "time")),
                **trigger_metadata,
            },
        )
    context = proposal.get("interaction_context")
    selection = context.get("selection") if isinstance(context, dict) else None
    if not candidates and isinstance(selection, dict) and selection.get("type") == "time_range":
        start, end = int(selection["start"]), int(selection["end"])
        add(
            TimelineReference("time_range", start=start, end=end),
            start,
            end,
            {"object_type": "range", "title": "Unresolved time range"},
        )
    return candidates
