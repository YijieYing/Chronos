"""Validated, transactional execution boundary for Chronos Agent Operations."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from chronos.agent.log_service import ChronosLogService
from chronos.agent.models import (
    AdjustmentTransaction,
    AgentOperation,
    CreateReminderOperation,
    CreateTaskOperation,
    LogEventType,
    OperationState,
    ScheduleSnapshot,
    TimelineOperation,
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

    def execute(
        self,
        operation: AgentOperation,
        operations: tuple[TimelineOperation, ...],
    ) -> AdjustmentTransaction:
        """Execute the canonical executable IR without returning to planning.

        This is the only Runtime entry point for the new pipeline.  The explicit
        ``operations`` argument makes the executable boundary visible and guards
        against a Proposal view becoming a second source of truth.
        """

        self._validate_execution(operation, operations)
        before = self._snapshot()
        approved = self._operations.transition(
            operation.id, OperationState.APPROVED, expected_version=operation.version
        )
        executing = self._operations.transition(
            operation.id, OperationState.EXECUTING, expected_version=approved.version
        )
        try:
            for executable in operations:
                self._apply(executable, operation)
            after = self._snapshot()
            transaction = AdjustmentTransaction(
                id=str(uuid4()),
                operation_id=operation.id,
                before_state=before,
                operations=operations,
                after_state=after,
                status=TransactionStatus.APPLIED,
                created_at=datetime.now(UTC),
            )
            self._transactions.save(transaction)
            self._operations.transition(
                operation.id,
                OperationState.COMPLETED,
                expected_version=executing.version,
            )
            if self._log is not None:
                self._log.append(
                    LogEventType.OPERATION_COMPLETED,
                    "已应用时间轴调整。",
                    operation_id=operation.id,
                    references=operation.references,
                    metadata={"transaction_id": transaction.id},
                )
            return transaction
        except Exception as error:
            self._rollback_scope(operation, before)
            self._operations.transition(
                operation.id,
                OperationState.FAILED,
                expected_version=executing.version,
                failure_reason=str(error),
            )
            if self._log is not None:
                self._log.append(
                    LogEventType.OPERATION_FAILED,
                    "执行失败，已回滚到操作前状态。",
                    operation_id=operation.id,
                    references=operation.references,
                    metadata={"failure_reason": str(error), "rolled_back": True},
                )
            raise

    def execute_legacy(
        self, operation_id: str
    ) -> tuple[dict[str, object], AdjustmentTransaction]:
        """Compatibility boundary for the old ProposalService execution path.

        New pipeline code must never call this method.  It remains only until
        the existing API route has migrated to Plan -> Operations -> Runtime.
        """

        operation = self._operations.get(operation_id)
        self._validate_legacy(operation)
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

    def revert(self, operation_id: str) -> AdjustmentTransaction:
        """Restore the canonical transaction snapshot without ProposalService."""

        transaction = self._transactions.get_by_operation(operation_id)
        if transaction is None:
            raise KeyError(operation_id)
        if transaction.status != TransactionStatus.APPLIED:
            raise ValueError("transaction is already reverted")
        operation = self._operations.get(operation_id)
        self._rollback_scope(operation, transaction.before_state)
        reverted = replace(transaction, status=TransactionStatus.REVERTED)
        self._transactions.save(reverted)
        if self._log is not None:
            self._log.append(
                LogEventType.UNDO,
                "已恢复操作前的时间轴状态。",
                operation_id=operation_id,
                references=operation.references,
                metadata={"transaction_id": transaction.id},
            )
        return reverted

    def revert_legacy(self, operation_id: str) -> dict[str, object]:
        """Compatibility boundary for legacy proposal-shaped restore output."""

        transaction = self._transactions.get_by_operation(operation_id)
        if transaction is None:
            raise KeyError(operation_id)
        if transaction.status != TransactionStatus.APPLIED:
            raise ValueError("transaction is already reverted")
        proposal = self._proposals.restore(operation_id)
        self._transactions.save(replace(transaction, status=TransactionStatus.REVERTED))
        return proposal

    def _validate_execution(
        self,
        operation: AgentOperation,
        operations: tuple[TimelineOperation, ...],
    ) -> None:
        if operation.state != OperationState.PROPOSED:
            raise ValueError("Runtime requires a proposed Operation")
        if not operations or operation.proposal is None:
            raise ValueError("Runtime requires validated compiled operations")
        if operations != operation.compiled_operations:
            raise ValueError("Runtime operations must match the Operation snapshot")
        if not operation.reversible:
            raise ValueError("first-version Runtime only executes reversible Operations")

    def _validate_legacy(self, operation: AgentOperation) -> None:
        self._validate_execution(operation, operation.compiled_operations)
        proposal = self._proposals.get(operation.id)
        if proposal.get("status") != "pending":
            raise ValueError("Runtime proposal is not pending")

    def _apply(self, executable: TimelineOperation, operation: AgentOperation) -> None:
        if isinstance(executable, CreateTaskOperation):
            self._guard_time(executable.task.start, operation)
            task = executable.task
            self._schedule.create_scheduled_task(
                task_id=executable.task_id,
                title=task.title,
                estimated_minutes=task.duration_minutes,
                priority=task.adjustment_policy.priority,
                preferred_start=datetime.fromtimestamp(task.start / 1000, UTC),
                task_type=task.task_type,
                fixed=task.fixed,
                source="agent",
            )
            return
        if isinstance(executable, CreateReminderOperation):
            if self._reminders is None:
                raise ValueError("Reminder service is unavailable")
            reminder = executable.reminder
            if reminder.at is not None:
                self._guard_time(reminder.at, operation)
            elif reminder.window is not None:
                self._guard_time(reminder.window.start, operation)
            self._reminders.create(
                reminder_id=executable.reminder_id,
                title=reminder.title,
                trigger_type=reminder.trigger_type,
                trigger_at=(
                    datetime.fromtimestamp(reminder.at / 1000, UTC)
                    if reminder.at is not None
                    else None
                ),
                window_start=(
                    datetime.fromtimestamp(reminder.window.start / 1000, UTC)
                    if reminder.window is not None
                    else None
                ),
                window_end=(
                    datetime.fromtimestamp(reminder.window.end / 1000, UTC)
                    if reminder.window is not None
                    else None
                ),
                delivery=reminder.delivery,
                priority=reminder.priority,
                source="agent",
            )
            return
        raise ValueError(f"Runtime does not support operation type: {executable.type}")

    @staticmethod
    def _guard_time(start: int, operation: AgentOperation) -> None:
        mode = str(operation.intent.attributes.get("planning_mode", "prospective"))
        if mode == "historical":
            return
        horizon = operation.intent.attributes.get("planning_horizon_start")
        if not isinstance(horizon, int):
            raise ValueError("prospective Operation requires planning_horizon_start")
        if start < horizon:
            raise ValueError("prospective Operation cannot schedule before its planning horizon")

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
