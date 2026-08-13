"""Compatibility import for proposal history created before Chronos Log existed."""

from __future__ import annotations

from datetime import UTC, datetime

from chronos.agent.log_service import ChronosLogService
from chronos.agent.models import LogEventType, TimelineReference


def migrate_proposal_history(
    proposals: list[dict[str, object]], log: ChronosLogService
) -> int:
    imported = 0
    for proposal in proposals:
        operation_id = str(proposal["proposal_id"])
        if log.has_operation(operation_id):
            continue
        status = str(proposal.get("status", "informational"))
        event_type = {
            "needs_clarification": LogEventType.CLARIFICATION_REQUESTED,
            "pending": LogEventType.PROPOSAL_CREATED,
            "accepted": LogEventType.OPERATION_COMPLETED,
            "rejected": LogEventType.OPERATION_REJECTED,
            "restored": LogEventType.UNDO,
        }.get(status, LogEventType.AGENT_MESSAGE)
        explanation = proposal.get("explanation")
        message = (
            " ".join(str(item) for item in explanation)
            if isinstance(explanation, list)
            else str(proposal.get("request_text") or "Imported legacy proposal")
        )
        occurred_at = _timestamp(proposal.get("updated_at") or proposal.get("created_at"))
        log.append(
            event_type,
            message or "Imported legacy proposal",
            entry_id=f"legacy-proposal:{operation_id}:snapshot",
            operation_id=operation_id,
            occurred_at=occurred_at,
            references=proposal_references(proposal),
            metadata={"legacy_import": True, "proposal_status": status},
        )
        imported += 1
    return imported


def proposal_references(
    proposal: dict[str, object],
) -> tuple[TimelineReference, ...]:
    references: list[TimelineReference] = []
    seen: set[tuple[str, str | None, int | None, int | None]] = set()

    def add(reference: TimelineReference) -> None:
        key = (reference.type, reference.id, reference.start, reference.end)
        if key not in seen:
            seen.add(key)
            references.append(reference)

    proposed = proposal.get("proposed_task")
    if isinstance(proposed, dict):
        task_id = str(proposed.get("series_id") or proposed.get("id"))
        add(TimelineReference("task", id=task_id))
        if proposed.get("start") is not None and proposed.get("end") is not None:
            add(
                TimelineReference(
                    "time_range", start=int(proposed["start"]), end=int(proposed["end"])
                )
            )
    for item in proposal.get("proposed_tasks", []):
        if isinstance(item, dict) and item.get("task_id"):
            add(TimelineReference("task", id=str(item["task_id"])))
    for draft in proposal.get("reminder_drafts", []):
        reminder = draft.get("reminder") if isinstance(draft, dict) else None
        if isinstance(reminder, dict) and reminder.get("id"):
            add(TimelineReference("reminder", id=str(reminder["id"])))
    context = proposal.get("interaction_context")
    selection = context.get("selection") if isinstance(context, dict) else None
    if isinstance(selection, dict):
        if selection.get("type") == "time_range":
            add(
                TimelineReference(
                    "time_range",
                    start=int(selection["start"]),
                    end=int(selection["end"]),
                )
            )
        elif selection.get("type") in {"task", "reminder"}:
            add(TimelineReference(str(selection["type"]), id=str(selection["id"])))
    return tuple(references)


def _timestamp(value: object) -> datetime:
    if value:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return datetime.now(UTC)
