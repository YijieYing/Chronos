from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from chronos.agent.adjustment import AdjustmentCoordinator, AdjustmentEngine
from chronos.agent.models import OperationState, ReplanSignalType
from chronos.agent.service import OperationStore
from chronos.api.routes.v1 import V1Router
from chronos.infrastructure.sqlite_cognitive_state import SQLiteCognitiveStateRepository
from chronos.infrastructure.sqlite_operations import SQLiteAgentOperationRepository
from chronos.infrastructure.sqlite_proposals import SQLiteProposalRepository
from chronos.infrastructure.sqlite_schedule import SQLiteScheduleRepository
from chronos.monitor.cognitive import CognitiveStatePoint, RecoveryState
from chronos.schedule.models import TaskStatus
from chronos.schedule.proposals import ProposalService
from chronos.schedule.service import ScheduleService


class AdjustmentEngineTest(TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.database = Path(self.temporary.name) / "chronos.sqlite3"
        self.schedule = ScheduleService(SQLiteScheduleRepository(self.database))
        self.cognitive = SQLiteCognitiveStateRepository(self.database)
        self.operations = OperationStore(SQLiteAgentOperationRepository(self.database))
        self.engine = AdjustmentEngine(self.schedule, self.cognitive)
        self.coordinator = AdjustmentCoordinator(self.engine, self.operations)
        self.now = datetime(2026, 8, 13, 4, 0, tzinfo=UTC)  # 12:00 Asia/Shanghai

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_detects_missed_task_and_fixed_conflict_from_schedule(self) -> None:
        for task_id, start, minutes in (
            ("fixed-a", self.now - timedelta(minutes=30), 60),
            ("fixed-b", self.now - timedelta(minutes=15), 60),
        ):
            self.schedule.create_task(
                task_id=task_id,
                title=task_id,
                estimated_minutes=minutes,
                priority=3,
                preferred_start=start,
                fixed=True,
            )
        self.schedule.create_task(
            task_id="missed",
            title="Missed",
            estimated_minutes=30,
            priority=3,
            preferred_start=self.now - timedelta(hours=2),
        )

        detected = self.engine.detect(self.now)

        types = [item.signal.type for item in detected]
        self.assertIn(ReplanSignalType.FIXED_CONFLICT, types)
        self.assertIn(ReplanSignalType.MISSED_TASK, types)
        conflict = next(
            item for item in detected if item.signal.type == ReplanSignalType.FIXED_CONFLICT
        )
        self.assertEqual(
            {reference.id for reference in conflict.signal.references},
            {"fixed-a", "fixed-b"},
        )

    def test_cognitive_overload_requires_fresh_confident_backend_state(self) -> None:
        self.cognitive.upsert(
            CognitiveStatePoint(
                device_id="macbook",
                time=self.now - timedelta(minutes=5),
                cognitive_load=0.86,
                mental_fatigue=0.7,
                focus=0.8,
                task_type="coding",
                task_confidence=0.9,
                recovery_state=RecoveryState.WORKING,
            )
        )

        detected = self.engine.detect(self.now)

        overload = next(
            item for item in detected
            if item.signal.type == ReplanSignalType.COGNITIVE_OVERLOAD
        )
        self.assertEqual(overload.source, "monitor.cognitive_state")
        self.assertTrue(overload.signal.threshold_reached)

    def test_signal_ingestion_is_idempotent_and_never_changes_schedule(self) -> None:
        self.schedule.create_task(
            task_id="missed",
            title="Missed",
            estimated_minutes=30,
            priority=3,
            preferred_start=self.now - timedelta(hours=2),
        )
        before = self.schedule.get_task("missed")

        first = self.coordinator.scan(self.now)
        second = self.coordinator.scan(self.now)

        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].id, second[0].id)
        self.assertEqual(first[0].state, OperationState.COMPLETED)
        self.assertEqual(first[0].intent.kind, "replan_signal")
        self.assertFalse(first[0].intent.attributes["proactive_enabled"])
        self.assertEqual(self.operations.pending(), [])
        self.assertEqual(self.schedule.get_task("missed"), before)
        self.assertEqual(len(self.operations.list()), 1)

    def test_future_and_completed_tasks_do_not_emit_missed_signal(self) -> None:
        future = self.schedule.create_task(
            task_id="future",
            title="Future",
            estimated_minutes=30,
            priority=3,
            preferred_start=self.now + timedelta(hours=1),
        )
        completed = self.schedule.create_task(
            task_id="done",
            title="Done",
            estimated_minutes=30,
            priority=3,
            preferred_start=self.now - timedelta(hours=2),
        )
        self.schedule.set_task_status(completed.task_id, TaskStatus.COMPLETED)

        detected = self.engine.detect(self.now)

        missed_ids = {
            reference.id
            for item in detected
            if item.signal.type == ReplanSignalType.MISSED_TASK
            for reference in item.signal.references
        }
        self.assertNotIn(future.task_id, missed_ids)
        self.assertNotIn(completed.task_id, missed_ids)

    def test_safe_scan_isolated_from_schedule_and_monitor_write_paths(self) -> None:
        class FailingEngine:
            def detect(self, now=None):
                raise RuntimeError("detector unavailable")

        coordinator = AdjustmentCoordinator(FailingEngine(), self.operations)

        with self.assertLogs("chronos.agent.adjustment", level="ERROR"):
            result = coordinator.scan_safely(self.now)

        self.assertEqual(result, ())
        self.assertEqual(self.operations.list(), [])

    def test_diagnostic_endpoint_exposes_passive_operation_without_pending_work(self) -> None:
        self.schedule.create_task(
            task_id="missed",
            title="Missed",
            estimated_minutes=30,
            priority=3,
            preferred_start=self.now - timedelta(hours=2),
        )
        self.coordinator.scan(self.now)
        router = V1Router(
            self.schedule,
            ProposalService(self.schedule, SQLiteProposalRepository(self.database)),
            operation_store=self.operations,
            adjustments=self.coordinator,
        )

        status, response = router.dispatch("GET", "/api/v1/replan-signals")

        self.assertEqual(status, 200)
        self.assertFalse(response["data"]["proactive_enabled"])
        self.assertEqual(response["data"]["signals"][0]["signal_type"], "missed_task")
        self.assertEqual(response["data"]["signals"][0]["state"], "completed")
