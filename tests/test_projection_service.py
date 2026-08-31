from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from chronos.agent.models import (
    ClarificationState,
    IntentSnapshot,
    OperationState,
    ProjectionKind,
    ProjectionVisualState,
    TimelineProjection,
    TimelineReference,
)
from chronos.agent.projection_service import ProjectionService, proposal_projections
from chronos.agent.service import OperationStore
from chronos.infrastructure.sqlite_operations import SQLiteAgentOperationRepository


class ProjectionServiceTest(TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        repository = SQLiteAgentOperationRepository(
            Path(self.temporary.name) / "chronos.sqlite3"
        )
        self.store = OperationStore(repository)
        self.service = ProjectionService(self.store)
        self.now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_operation_projection_is_hidden_after_becoming_stale(self) -> None:
        operation = self.store.create(
            IntentSnapshot("create_task", "创建阅读任务"),
            operation_id="operation-1",
            now=self.now,
        )
        projection = TimelineProjection(
            id="projection-1",
            operation_id=operation.id,
            type=ProjectionKind.CLARIFICATION,
            target=TimelineReference("time_range", start=100, end=200),
            visual_state=ProjectionVisualState.INCOMPLETE,
            start=100,
            end=200,
        )
        awaiting = replace(
            operation,
            state=OperationState.AWAITING_CLARIFICATION,
            unresolved_questions=(ClarificationState("duration", "需要多久？"),),
            projections=(projection,),
            ambiguity=0.8,
            version=2,
        )
        self.store.save_snapshot(awaiting, expected_version=1)

        self.assertEqual(self.service.list_active(), [projection])

        self.store.transition(
            operation.id,
            OperationState.STALE,
            expected_version=2,
            now=self.now,
        )
        self.assertEqual(self.service.list_active(), [])

    def test_active_projection_comes_only_from_canonical_operation(self) -> None:
        operation = self.store.create(
            IntentSnapshot("create_task", "创建阅读任务"),
            operation_id="shared",
            now=self.now,
        )
        projection = TimelineProjection(
            id="projection-real",
            operation_id=operation.id,
            type=ProjectionKind.CLARIFICATION,
            target=TimelineReference("time_range", start=100, end=200),
            visual_state=ProjectionVisualState.INCOMPLETE,
            start=100,
            end=200,
        )
        self.store.save_snapshot(
            replace(
                operation,
                state=OperationState.AWAITING_CLARIFICATION,
                unresolved_questions=(ClarificationState("duration", "需要多久？"),),
                projections=(projection,),
                version=2,
            ),
            expected_version=1,
        )
        self.assertEqual(self.service.list_active(), [projection])

    def test_canonical_operation_without_projection_returns_no_projection(self) -> None:
        self.store.create(
            IntentSnapshot("query", "查询时间轴"),
            operation_id="shared-empty",
            now=self.now,
        )

        projections = self.service.list_active()

        self.assertEqual(projections, [])

    def test_legacy_projection_only_exists_while_proposal_is_pending(self) -> None:
        pending = proposal_projections(self._legacy_proposal("legacy", "pending"))
        accepted = proposal_projections(self._legacy_proposal("legacy", "accepted"))

        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].visual_state, ProjectionVisualState.PROPOSED)
        self.assertEqual(pending[0].target.id, "reading")
        self.assertEqual(accepted, ())

    @staticmethod
    def _legacy_proposal(operation_id: str, status: str) -> dict[str, object]:
        return {
            "proposal_id": operation_id,
            "status": status,
            "proposed_task": {
                "id": "reading",
                "title": "阅读",
                "start": 100,
                "end": 200,
                "fixed": False,
            },
            "results": [],
            "reminder_drafts": [],
        }
