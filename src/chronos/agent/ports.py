"""Persistence ports owned by the Agent interaction bounded context."""

from __future__ import annotations

from typing import Protocol

from chronos.agent.models import AgentOperation, ChronosLogEntry, OperationState


class OperationVersionConflictError(RuntimeError):
    """Raised when a writer compiled against an older Operation snapshot."""


class AgentOperationRepository(Protocol):
    def create(self, operation: AgentOperation) -> None: ...
    def get(self, operation_id: str) -> AgentOperation | None: ...
    def save(self, operation: AgentOperation, expected_version: int) -> None: ...
    def list(self, states: tuple[OperationState, ...] | None = None) -> list[AgentOperation]: ...


class ChronosLogRepository(Protocol):
    def append(self, entry: ChronosLogEntry) -> None: ...
    def list(self, limit: int = 200) -> list[ChronosLogEntry]: ...
    def has_operation(self, operation_id: str) -> bool: ...
