"""Validated, transactional execution boundary for Chronos Agent Operations."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from chronos.agent.log_service import ChronosLogService
from chronos.agent.models import (
    AdjustmentTransaction,
    AgentOperation,
    LogEventType,
    OperationState,
    ScheduleSnapshot,
    TransactionStatus,
)
from chronos.agent.ports import AdjustmentTransactionRepository
from chronos.agent.service import OperationStore
from chronos.reminders.service import ReminderService
from chronos.schedule.proposals import ProposalService, _reminder_from_dict, _scheduled_values
from chronos.schedule.service import ScheduleService, _task_dict


class ChronosRuntime:
    def __init__(
        self,
        operations: OperationStore,
        proposals: ProposalService,
        schedule: ScheduleService,
        reminders: ReminderService | None,
        transactions: AdjustmentTransactionRepository,
        chronos_log: ChronosLogService | None = None,
    ) -> None:
        self._operations = operations
        self._proposals = proposals
        self._schedule = schedule
        self._reminders = reminders
        self._transactions = transactions
        self._log = chronos_log

    def execute(self, operation_id: str) -> tuple[dict[str, object], AdjustmentTransaction]:
        operation = self._operations.get(operation_id)
        self._validate(operation)
        before = self._snapshot()
        approved = self._operations.transition(
            operation_id, OperationState.APPROVED, expected_version=operation.version
        )
        executing = self._operations.transition(
            operation_id, OperationState.EXECUTING, expected_version=approved.version
        )
        try:
            proposal = self._proposals.accept(operation_id)
            after = self._snapshot()
            transaction = AdjustmentTransaction(
                id=str(uuid4()),
                operation_id=operation_id,
                before_state=before,
                operations=operation.compiled_operations,
                after_state=after,
                status=TransactionStatus.APPLIED,
                created_at=datetime.now(UTC),
            )
            self._transactions.save(transaction)
            self._operations.transition(
                operation_id,
                OperationState.COMPLETED,
                expected_version=executing.version,
            )
            return proposal, transaction
        except Exception as error:
            self._rollback_scope(operation, before)
            current_proposal = self._proposals.get(operation_id)
            if current_proposal.get("status") == "accepted":
                self._proposals.mark_runtime_rolled_back(operation_id)
            self._operations.transition(
                operation_id,
                OperationState.FAILED,
                expected_version=executing.version,
                failure_reason=str(error),
            )
            if self._log is not None:
                self._log.append(
                    LogEventType.OPERATION_FAILED,
                    "执行失败，已回滚到操作前状态。",
                    operation_id=operation_id,
                    references=operation.references,
                    metadata={"failure_reason": str(error), "rolled_back": True},
                )
            raise

    def revert(self, operation_id: str) -> dict[str, object]:
        transaction = self._transactions.get_by_operation(operation_id)
        if transaction is None:
            raise KeyError(operation_id)
        if transaction.status != TransactionStatus.APPLIED:
            raise ValueError("transaction is already reverted")
        proposal = self._proposals.restore(operation_id)
        self._transactions.save(replace(transaction, status=TransactionStatus.REVERTED))
        return proposal

    def _validate(self, operation: AgentOperation) -> None:
        if operation.state != OperationState.PROPOSED:
            raise ValueError("Runtime requires a proposed Operation")
        if not operation.compiled_operations or operation.proposal is None:
            raise ValueError("Runtime requires validated compiled operations")
        if not operation.reversible:
            raise ValueError("first-version Runtime only executes reversible Operations")
        proposal = self._proposals.get(operation.id)
        if proposal.get("status") != "pending":
            raise ValueError("Runtime proposal is not pending")

    def _snapshot(self) -> ScheduleSnapshot:
        tasks = tuple(_task_dict(item) for item in self._schedule.list_tasks())
        reminders = tuple(self._reminders.list()) if self._reminders is not None else ()
        versions: dict[str, int | None] = {}
        for task in self._schedule.list_tasks():
            if task.preferred_start is not None:
                target = task.preferred_start.date().isoformat()
                versions[target] = self._schedule.current_agenda_version(
                    task.preferred_start.date()
                )
        return ScheduleSnapshot(datetime.now(UTC), tasks, reminders, versions)

    def _rollback_scope(
        self, operation: AgentOperation, snapshot: ScheduleSnapshot
    ) -> None:
        before_tasks = {str(item["task_id"]): dict(item) for item in snapshot.tasks}
        for task_id in operation.scope.task_ids:
            before = before_tasks.get(task_id)
            try:
                current = self._schedule.get_task(task_id)
            except KeyError:
                current = None
            if before is None and current is not None:
                self._schedule.delete_scheduled_task(task_id)
            elif before is not None and current is None:
                self._schedule.create_scheduled_task(**_scheduled_values(before))
            elif before is not None:
                self._schedule.update_scheduled_task(
                    task_id, **_scheduled_values(before, include_id=False)
                )
        if self._reminders is None:
            return
        before_reminders = {str(item["id"]): dict(item) for item in snapshot.reminders}
        for reminder_id in operation.scope.reminder_ids:
            before = before_reminders.get(reminder_id)
            try:
                current = self._reminders.get(reminder_id)
            except KeyError:
                current = None
            if before is None and current is not None:
                self._reminders.delete(reminder_id)
            elif before is not None:
                if current is not None:
                    self._reminders.delete(reminder_id)
                self._reminders.create(**_reminder_from_dict(before))
