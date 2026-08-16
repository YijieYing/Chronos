from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from chronos.agent.compiler import LLMChronosCompiler
from chronos.agent.log_service import ChronosLogService
from chronos.agent.models import (
    AgentOperation,
    CreateTaskOperation,
    IntentSnapshot,
    OperationScope,
    OperationState,
    ProposalSnapshot,
    TaskSpec,
    TimeRange,
)
from chronos.agent.runtime import ChronosRuntime
from chronos.agent.service import OperationStore
from chronos.api.routes.v1 import V1Router
from chronos.infrastructure.sqlite_chronos_log import SQLiteChronosLogRepository
from chronos.infrastructure.sqlite_operations import SQLiteAgentOperationRepository
from chronos.infrastructure.sqlite_proposals import SQLiteProposalRepository
from chronos.infrastructure.sqlite_reminders import SQLiteReminderRepository
from chronos.infrastructure.sqlite_schedule import SQLiteScheduleRepository
from chronos.infrastructure.sqlite_transactions import SQLiteAdjustmentTransactionRepository
from chronos.reminders.service import ReminderService
from chronos.schedule.proposals import ProposalService
from chronos.schedule.semantic_parser import SemanticScheduleCommandParser
from chronos.schedule.service import ScheduleService


class ChronosRuntimeTest(TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.database = Path(self.temporary.name) / "chronos.sqlite3"
        self.schedule = ScheduleService(SQLiteScheduleRepository(self.database))
        repository = _FailSecondReminderRepository(self.database)
        self.reminders = ReminderService(repository)
        self.proposals = ProposalService(
            self.schedule,
            SQLiteProposalRepository(self.database),
            reminders=self.reminders,
        )
        self.operations = OperationStore(SQLiteAgentOperationRepository(self.database))
        self.log = ChronosLogService(SQLiteChronosLogRepository(self.database))
        self.transactions = SQLiteAdjustmentTransactionRepository(self.database)
        self.runtime = ChronosRuntime(
            self.operations,
            self.proposals,
            self.schedule,
            self.reminders,
            self.transactions,
            self.log,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_partial_reminder_failure_rolls_back_and_marks_operation_failed(self) -> None:
        provider = _Provider("""{
          "intent":"create_reminder","tasks":[],"reminders":[
            {"title":"A","title_source":"A","trigger":{"type":"time",
             "at":"2026-08-13T15:20:00+08:00"},"temporal_sources":["15:20"],
             "delivery":"exact","delivery_sources":[],"priority":3},
            {"title":"B","title_source":"B","trigger":{"type":"time",
             "at":"2026-08-13T16:20:00+08:00"},"temporal_sources":["16:20"],
             "delivery":"exact","delivery_sources":[],"priority":3}
          ],"unresolved":[],"assumptions":[]
        }""")
        router = V1Router(
            self.schedule,
            self.proposals,
            reminders=self.reminders,
            chronos_log=self.log,
            operation_store=self.operations,
            compiler=LLMChronosCompiler(
                SemanticScheduleCommandParser(provider, fallback_on_error=False)
            ),
            runtime=self.runtime,
        )
        _, envelope = router.dispatch(
            "POST", "/api/v1/proposals",
            {
                "text": "15:20提醒我A，16:20提醒我B",
                "interaction_context": {
                    "current_time": int(datetime.now(UTC).timestamp() * 1000),
                    "selection": None,
                },
            },
        )
        proposal = envelope["data"]
        assert isinstance(proposal, dict)
        operation_id = str(proposal["proposal_id"])

        with self.assertRaisesRegex(RuntimeError, "injected reminder failure"):
            router.dispatch("POST", f"/api/v1/proposals/{operation_id}/accept")

        self.assertEqual(self.reminders.list(), [])
        operation = self.operations.get(operation_id)
        self.assertEqual(operation.state.value, "failed")
        self.assertIsNone(self.transactions.get_by_operation(operation_id))
        self.assertTrue(self.log.list()[0].metadata["rolled_back"])

    def test_success_persists_transaction_and_runtime_revert_marks_it_reverted(self) -> None:
        stable_reminders = ReminderService(SQLiteReminderRepository(self.database))
        proposals = ProposalService(
            self.schedule,
            SQLiteProposalRepository(self.database),
            reminders=stable_reminders,
        )
        runtime = ChronosRuntime(
            self.operations,
            proposals,
            self.schedule,
            stable_reminders,
            self.transactions,
            self.log,
        )
        provider = _Provider("""{
          "intent":"create_reminder","tasks":[],"reminders":[{
            "title":"取快递","title_source":"取快递","trigger":{"type":"time",
            "at":"2026-08-13T15:20:00+08:00"},"temporal_sources":["15:20"],
            "delivery":"exact","delivery_sources":[],"priority":3
          }],"unresolved":[],"assumptions":[]
        }""")
        router = V1Router(
            self.schedule,
            proposals,
            reminders=stable_reminders,
            operation_store=self.operations,
            compiler=LLMChronosCompiler(
                SemanticScheduleCommandParser(provider, fallback_on_error=False)
            ),
            runtime=runtime,
        )
        _, envelope = router.dispatch(
            "POST", "/api/v1/proposals",
            {
                "text": "15:20提醒我取快递",
                "interaction_context": {
                    "current_time": int(datetime.now(UTC).timestamp() * 1000),
                    "selection": None,
                },
            },
        )
        proposal = envelope["data"]
        assert isinstance(proposal, dict)
        operation_id = str(proposal["proposal_id"])

        router.dispatch("POST", f"/api/v1/proposals/{operation_id}/accept")
        transaction = self.transactions.get_by_operation(operation_id)
        assert transaction is not None
        self.assertEqual(transaction.status.value, "applied")
        self.assertEqual(len(transaction.before_state.reminders), 0)
        self.assertEqual(len(transaction.after_state.reminders), 1)

        router.dispatch("POST", f"/api/v1/proposals/{operation_id}/restore")
        reverted = self.transactions.get_by_operation(operation_id)
        assert reverted is not None
        self.assertEqual(reverted.status.value, "reverted")
        self.assertEqual(stable_reminders.list(), [])

    def test_canonical_runtime_executes_operations_without_proposal_payload(self) -> None:
        start = int(datetime(2026, 8, 17, 15, 30, tzinfo=UTC).timestamp() * 1000)
        executable = CreateTaskOperation(
            "task-canonical",
            TaskSpec("日语", start, 30),
        )
        operation = self._canonical_operation(executable, horizon=start)
        self.operations.create_snapshot(operation)

        transaction = self.runtime.execute(operation, (executable,))

        task = self.schedule.get_task("task-canonical")
        self.assertEqual(task.title, "日语")
        self.assertEqual(task.estimated_minutes, 30)
        self.assertEqual(task.source, "agent")
        self.assertEqual(transaction.operations, (executable,))
        self.assertEqual(self.operations.get(operation.id).state, OperationState.COMPLETED)

        reverted = self.runtime.revert(operation.id)
        self.assertEqual(reverted.status.value, "reverted")
        with self.assertRaises(KeyError):
            self.schedule.get_task("task-canonical")

    def test_canonical_runtime_rejects_prospective_work_before_horizon(self) -> None:
        start = int(datetime(2026, 8, 17, 14, 30, tzinfo=UTC).timestamp() * 1000)
        horizon = int(datetime(2026, 8, 17, 15, 30, tzinfo=UTC).timestamp() * 1000)
        executable = CreateTaskOperation("task-past", TaskSpec("A", start, 30))
        operation = self._canonical_operation(executable, horizon=horizon)
        self.operations.create_snapshot(operation)

        with self.assertRaisesRegex(ValueError, "planning horizon"):
            self.runtime.execute(operation, (executable,))

        self.assertEqual(self.operations.get(operation.id).state, OperationState.FAILED)
        with self.assertRaises(KeyError):
            self.schedule.get_task("task-past")

    def _canonical_operation(
        self,
        executable: CreateTaskOperation,
        *,
        horizon: int,
    ) -> AgentOperation:
        now = datetime.now(UTC)
        operation_id = f"operation-{executable.task_id}"
        proposal = ProposalSnapshot(
            operation_id,
            1,
            (executable,),
            now,
            "canonical runtime test",
        )
        return AgentOperation(
            id=operation_id,
            state=OperationState.PROPOSED,
            intent=IntentSnapshot(
                "add",
                executable.task.title,
                attributes={
                    "planning_mode": "prospective",
                    "planning_horizon_start": horizon,
                },
            ),
            unresolved_questions=(),
            compiled_operations=(executable,),
            projections=(),
            references=(),
            scope=OperationScope(
                task_ids=(executable.task_id,),
                time_ranges=(
                    TimeRange(
                        executable.task.start,
                        executable.task.start
                        + executable.task.duration_minutes * 60_000,
                    ),
                ),
            ),
            ambiguity=0,
            risk=0,
            impact=0.1,
            reversible=True,
            required_autonomy_level=0,
            created_at=now,
            updated_at=now,
            version=1,
            proposal=proposal,
        )


class _FailSecondReminderRepository(SQLiteReminderRepository):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.calls = 0

    def save(self, reminder) -> None:
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("injected reminder failure")
        super().save(reminder)


class _Provider:
    def __init__(self, response: str) -> None:
        self.response = response

    def generate(self, _system: str, _prompt: str) -> str:
        return self.response
