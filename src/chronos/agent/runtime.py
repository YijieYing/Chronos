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
    DeleteReminderOperation,
    DeleteTaskOperation,
    LogEventType,
    MoveReminderOperation,
    MoveTaskOperation,
    OperationState,
    RecurrenceSpec,
    ResizeTaskOperation,
    ScheduleSnapshot,
    TimelineOperation,
    TransactionStatus,
    UpdateReminderOperation,
    UpdateTaskOperation,
)
from chronos.agent.ports import AdjustmentTransactionRepository
from chronos.agent.service import OperationStore
from chronos.reminders.service import ReminderService
from chronos.schedule.proposals import _reminder_from_dict, _scheduled_values
from chronos.schedule.service import ScheduleService, _task_dict


class ChronosRuntime:
    def __init__(
        self,
        operations: OperationStore,
        schedule: ScheduleService,
        reminders: ReminderService | None,
        transactions: AdjustmentTransactionRepository,
        chronos_log: ChronosLogService | None = None,
    ) -> None:
        self._operations = operations
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

    def revert(self, operation_id: str) -> AdjustmentTransaction:
        """Restore the canonical transaction snapshot directly through domain services."""

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

    def _validate_execution(
        self,
        operation: AgentOperation,
        operations: tuple[TimelineOperation, ...],
    ) -> None:
        if operation.state != OperationState.PROPOSED:
            raise ValueError(
                "Runtime requires a proposed Operation; "
                f"received {operation.state.value}"
            )
        if not operations or operation.proposal is None:
            raise ValueError("Runtime requires validated compiled operations")
        if operations != operation.compiled_operations:
            raise ValueError("Runtime operations must match the Operation snapshot")
        if not operation.reversible:
            raise ValueError("first-version Runtime only executes reversible Operations")

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
                recurrence=_recurrence_dict(task.recurrence),
                source="agent",
            )
            return
        if isinstance(executable, UpdateTaskOperation):
            self._guard_time(executable.task.start, operation)
            task = executable.task
            self._schedule.update_scheduled_task(
                executable.task_id,
                title=task.title,
                estimated_minutes=task.duration_minutes,
                priority=task.adjustment_policy.priority,
                preferred_start=datetime.fromtimestamp(task.start / 1000, UTC),
                task_type=task.task_type,
                fixed=task.fixed,
                recurrence=_recurrence_dict(task.recurrence),
            )
            return
        if isinstance(executable, MoveTaskOperation):
            self._guard_time(executable.start, operation)
            self._schedule.update_scheduled_task(
                executable.task_id,
                preferred_start=datetime.fromtimestamp(executable.start / 1000, UTC),
            )
            return
        if isinstance(executable, ResizeTaskOperation):
            self._schedule.update_scheduled_task(
                executable.task_id, estimated_minutes=executable.duration_minutes
            )
            return
        if isinstance(executable, DeleteTaskOperation):
            if not self._schedule.delete_scheduled_task(executable.task_id):
                raise KeyError(executable.task_id)
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
        if isinstance(executable, UpdateReminderOperation):
            reminder = executable.reminder
            if reminder.at is not None:
                self._guard_time(reminder.at, operation)
            elif reminder.window is not None:
                self._guard_time(reminder.window.start, operation)
            current = self._reminder(executable.reminder_id)
            self._reminders.delete(executable.reminder_id)
            self._reminders.create(
                reminder_id=executable.reminder_id,
                title=reminder.title,
                trigger_type=reminder.trigger_type,
                trigger_at=(
                    datetime.fromtimestamp(reminder.at / 1000, UTC)
                    if reminder.at is not None else None
                ),
                window_start=(
                    datetime.fromtimestamp(reminder.window.start / 1000, UTC)
                    if reminder.window is not None else None
                ),
                window_end=(
                    datetime.fromtimestamp(reminder.window.end / 1000, UTC)
                    if reminder.window is not None else None
                ),
                delivery=reminder.delivery,
                priority=reminder.priority,
                source=str(current["source"]),
                created_at=datetime.fromisoformat(str(current["created_at"])),
            )
            return
        if isinstance(executable, MoveReminderOperation):
            start = executable.at or executable.window.start  # type: ignore[union-attr]
            self._guard_time(start, operation)
            current = self._reminder(executable.reminder_id)
            values = _reminder_from_dict(current)
            values.update({
                "trigger_type": "time" if executable.at is not None else "window",
                "trigger_at": (
                    datetime.fromtimestamp(executable.at / 1000, UTC)
                    if executable.at is not None else None
                ),
                "window_start": (
                    datetime.fromtimestamp(executable.window.start / 1000, UTC)
                    if executable.window is not None else None
                ),
                "window_end": (
                    datetime.fromtimestamp(executable.window.end / 1000, UTC)
                    if executable.window is not None else None
                ),
            })
            if executable.at is not None:
                values["delivery"] = "exact"
            self._reminders.delete(executable.reminder_id)
            self._reminders.create(**values)
            return
        if isinstance(executable, DeleteReminderOperation):
            self._reminder(executable.reminder_id)
            self._reminders.delete(executable.reminder_id)
            return
        raise ValueError(f"Runtime does not support operation type: {executable.type}")

    def _reminder(self, reminder_id: str) -> dict[str, object]:
        if self._reminders is None:
            raise ValueError("Reminder service is unavailable")
        return self._reminders.get(reminder_id)

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


def _recurrence_dict(value: RecurrenceSpec | None) -> dict[str, object] | None:
    if value is None:
        return None
    result: dict[str, object] = {"frequency": value.frequency}
    if value.weekdays:
        result["weekdays"] = list(value.weekdays)
    if value.until is not None:
        result["until"] = value.until
    return result
