from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from chronos.agent.models import (
    AgentOperation,
    ClarificationState,
    DeleteTaskOperation,
    IntentSnapshot,
    OperationScope,
    OperationState,
    ProposalSnapshot,
    TimeRange,
)
from chronos.agent.ports import OperationVersionConflictError
from chronos.agent.service import OperationStore
from chronos.infrastructure.sqlite_operations import SQLiteAgentOperationRepository


class AgentOperationStoreTest(TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        repository = SQLiteAgentOperationRepository(
            Path(self.temporary.name) / "nested" / "chronos.sqlite3"
        )
        self.store = OperationStore(repository)
        self.now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_multiple_clarifications_are_persisted_independently(self) -> None:
        first = self._clarification("first", "日语安排在哪个时间段？")
        second = self._clarification("second", "最晚什么时候提醒？")

        pending = self.store.pending()

        self.assertEqual({item.id for item in pending}, {first.id, second.id})
        self.assertTrue(
            all(item.state == OperationState.AWAITING_CLARIFICATION for item in pending)
        )

    def test_full_snapshot_refresh_increments_version_without_nested_state(self) -> None:
        first = self._clarification("first", "日语安排在哪个时间段？")
        refreshed = replace(
            first,
            intent=IntentSnapshot("create_task", "晚上安排日语", "晚上"),
            unresolved_questions=(ClarificationState("duration", "需要多久？"),),
            updated_at=self.now + timedelta(minutes=1),
            version=3,
        )

        saved = self.store.save_snapshot(refreshed, expected_version=2)

        self.assertEqual(saved.version, 3)
        self.assertEqual(self.store.get("first").unresolved_questions[0].field, "duration")

    def test_illegal_terminal_transition_is_rejected(self) -> None:
        operation = self.store.create(
            IntentSnapshot("query", "查询日程"), operation_id="query", now=self.now
        )
        executing = self.store.transition(
            operation.id,
            OperationState.READY,
            expected_version=1,
            now=self.now + timedelta(seconds=1),
        )
        executing = self.store.transition(
            operation.id,
            OperationState.EXECUTING,
            expected_version=executing.version,
            now=self.now + timedelta(seconds=2),
        )
        completed = self.store.transition(
            operation.id,
            OperationState.COMPLETED,
            expected_version=executing.version,
            now=self.now + timedelta(seconds=3),
        )

        with self.assertRaisesRegex(ValueError, "completed -> executing"):
            self.store.transition(
                operation.id,
                OperationState.EXECUTING,
                expected_version=completed.version,
            )

    def test_optimistic_version_check_prevents_lost_updates(self) -> None:
        current = self.store.create(
            IntentSnapshot("create_task", "创建任务"), operation_id="versioned", now=self.now
        )
        first_update = replace(
            current,
            updated_at=self.now + timedelta(seconds=1),
            version=2,
        )
        self.store.save_snapshot(first_update, expected_version=1)

        stale_writer = replace(
            current,
            updated_at=self.now + timedelta(seconds=2),
            version=2,
        )
        with self.assertRaises(OperationVersionConflictError):
            self.store.save_snapshot(stale_writer, expected_version=1)

    def test_overlapping_pending_proposals_are_marked_stale(self) -> None:
        first = self.store.create(
            IntentSnapshot("move_task", "移动日语"),
            operation_id="task-scope",
            now=self.now,
            scope=OperationScope(task_ids=("japanese",)),
        )
        first = self.store.transition(
            first.id, OperationState.READY, expected_version=1, now=self.now
        )
        first = self._proposed(first, "japanese")
        second = self.store.create(
            IntentSnapshot("make_space", "腾出晚上"),
            operation_id="range-scope",
            now=self.now,
            scope=OperationScope(time_ranges=(TimeRange(18, 22),)),
        )
        second = self.store.transition(
            second.id, OperationState.READY, expected_version=1, now=self.now
        )
        second = self._proposed(second, "evening")

        stale = self.store.mark_conflicting_stale(
            OperationScope(task_ids=("japanese",), time_ranges=(TimeRange(20, 24),)),
            now=self.now + timedelta(minutes=1),
        )

        self.assertEqual({item.id for item in stale}, {first.id, second.id})
        self.assertTrue(all(item.state == OperationState.STALE for item in stale))

    def test_clarification_with_known_scope_can_become_stale(self) -> None:
        operation = self.store.create(
            IntentSnapshot("move_task", "移动任务"),
            operation_id="clarification-scope",
            now=self.now,
            scope=OperationScope(task_ids=("research",)),
        )
        snapshot = replace(
            operation,
            state=OperationState.AWAITING_CLARIFICATION,
            unresolved_questions=(ClarificationState("time", "挪到几点？"),),
            version=2,
        )
        self.store.save_snapshot(snapshot, expected_version=1)

        stale = self.store.mark_conflicting_stale(
            OperationScope(task_ids=("research",)),
            now=self.now + timedelta(minutes=1),
        )

        self.assertEqual(stale[0].state, OperationState.STALE)

    def _clarification(self, operation_id: str, question: str) -> AgentOperation:
        operation = self.store.create(
            IntentSnapshot("create_task", "创建日程"),
            operation_id=operation_id,
            now=self.now,
        )
        snapshot = replace(
            operation,
            state=OperationState.AWAITING_CLARIFICATION,
            unresolved_questions=(ClarificationState("time", question),),
            updated_at=self.now + timedelta(seconds=1),
            version=2,
        )
        return self.store.save_snapshot(snapshot, expected_version=1)

    def _proposed(self, operation: AgentOperation, task_id: str) -> AgentOperation:
        primitive = DeleteTaskOperation(task_id)
        version = operation.version + 1
        snapshot = replace(
            operation,
            state=OperationState.PROPOSED,
            compiled_operations=(primitive,),
            proposal=ProposalSnapshot(
                operation_id=operation.id,
                version=version,
                operations=(primitive,),
                created_at=self.now,
            ),
            version=version,
        )
        return self.store.save_snapshot(snapshot, expected_version=operation.version)
