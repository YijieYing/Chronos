from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from chronos.agent.compiler import LLMChronosCompiler
from chronos.agent.log_service import ChronosLogService
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
