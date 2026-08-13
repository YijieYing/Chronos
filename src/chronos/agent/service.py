"""Application service for parallel, versioned Agent Operations."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from chronos.agent.models import (
    AgentOperation,
    IntentSnapshot,
    OperationScope,
    OperationState,
)
from chronos.agent.ports import AgentOperationRepository
from chronos.agent.state_machine import ACTION_REQUIRED_STATES, TERMINAL_STATES, validate_transition


class OperationStore:
    def __init__(self, repository: AgentOperationRepository) -> None:
        self._repository = repository

    def create(
        self,
        intent: IntentSnapshot,
        *,
        operation_id: str | None = None,
        now: datetime | None = None,
        scope: OperationScope | None = None,
    ) -> AgentOperation:
        timestamp = now or datetime.now(UTC)
        operation = AgentOperation(
            id=operation_id or str(uuid4()),
            state=OperationState.INTERPRETING,
            intent=intent,
            unresolved_questions=(),
            compiled_operations=(),
            projections=(),
            references=(),
            scope=scope or OperationScope(),
            ambiguity=1,
            risk=0,
            impact=0,
            reversible=True,
            required_autonomy_level=0,
            created_at=timestamp,
            updated_at=timestamp,
            version=1,
        )
        self._repository.create(operation)
        return operation

    def get(self, operation_id: str) -> AgentOperation:
        operation = self._repository.get(operation_id)
        if operation is None:
            raise KeyError(operation_id)
        return operation

    def save_snapshot(self, operation: AgentOperation, *, expected_version: int) -> AgentOperation:
        current = self.get(operation.id)
        if current.version != expected_version:
            from chronos.agent.ports import OperationVersionConflictError

            raise OperationVersionConflictError(operation.id)
        if operation.version != expected_version + 1:
            raise ValueError("new Operation snapshot must increment version exactly once")
        if operation.created_at != current.created_at:
            raise ValueError("Operation created_at is immutable")
        if operation.updated_at < current.updated_at:
            raise ValueError("Operation updated_at cannot move backwards")
        validate_transition(current.state, operation.state)
        self._repository.save(operation, expected_version)
        return operation

    def transition(
        self,
        operation_id: str,
        target: OperationState,
        *,
        expected_version: int,
        now: datetime | None = None,
        failure_reason: str | None = None,
    ) -> AgentOperation:
        current = self.get(operation_id)
        validate_transition(current.state, target)
        if target == OperationState.FAILED and not failure_reason:
            raise ValueError("failed Operation requires a reason")
        updated = replace(
            current,
            state=target,
            updated_at=now or datetime.now(UTC),
            version=current.version + 1,
            failure_reason=failure_reason if target == OperationState.FAILED else None,
        )
        return self.save_snapshot(updated, expected_version=expected_version)

    def pending(self) -> list[AgentOperation]:
        return self._repository.list(tuple(ACTION_REQUIRED_STATES))

    def active(self) -> list[AgentOperation]:
        return self._repository.list(
            tuple(state for state in OperationState if state not in TERMINAL_STATES)
        )

    def mark_conflicting_stale(
        self,
        changed_scope: OperationScope,
        *,
        exclude_operation_id: str | None = None,
        now: datetime | None = None,
    ) -> list[AgentOperation]:
        stale: list[AgentOperation] = []
        for operation in self.active():
            if operation.id == exclude_operation_id or not operation.scope.overlaps(changed_scope):
                continue
            if operation.state not in {
                OperationState.AWAITING_CLARIFICATION,
                OperationState.READY,
                OperationState.PROPOSED,
                OperationState.APPROVED,
            }:
                continue
            stale.append(
                self.transition(
                    operation.id,
                    OperationState.STALE,
                    expected_version=operation.version,
                    now=now,
                )
            )
        return stale
