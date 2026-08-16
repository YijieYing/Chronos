from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from unittest import TestCase

from chronos.agent.models import (
    AdjustmentPolicy,
    AgentOperation,
    AutonomyPolicy,
    ClarificationState,
    CreateReminderOperation,
    CreateTaskOperation,
    IntentSnapshot,
    OperationScope,
    OperationState,
    ProjectionKind,
    ProjectionVisualState,
    ProposalSnapshot,
    RecurrenceSpec,
    ReminderSpec,
    TaskSpec,
    TimelineProjection,
    TimelineReference,
    TimeRange,
)
from chronos.agent.serialization import operation_from_dict, operation_to_dict


class AgentOperationModelTest(TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)

    def test_complete_operation_snapshot_round_trips(self) -> None:
        create_task = CreateTaskOperation(
            task_id="japanese",
            task=TaskSpec(
                title="日语",
                start=1_786_617_600_000,
                duration_minutes=30,
                recurrence=RecurrenceSpec("daily", until="2026-08-20"),
                window=TimeRange(1_786_614_000_000, 1_786_628_400_000),
                adjustment_policy=AdjustmentPolicy(
                    rigidity=0.45,
                    shrinkability=0.8,
                    min_duration=10,
                    continuity_value=0.9,
                ),
            ),
        )
        reminder = CreateReminderOperation(
            reminder_id="materials",
            reminder=ReminderSpec(
                title="交材料",
                trigger_type="window",
                window=TimeRange(1_786_614_000_000, 1_786_628_400_000),
                delivery="context-aware",
                prefer_interruptible_moment=True,
                avoid_high_focus=True,
            ),
        )
        proposal = ProposalSnapshot(
            operation_id="operation-1",
            version=2,
            created_at=self.now,
            explanation="安排灵活习惯并择机提醒。",
        )
        operation = AgentOperation(
            id="operation-1",
            state=OperationState.PROPOSED,
            intent=IntentSnapshot("plan_and_remind", "安排日语并提醒交材料", "原始输入"),
            unresolved_questions=(),
            compiled_operations=(create_task, reminder),
            projections=(
                TimelineProjection(
                    id="projection-1",
                    operation_id="operation-1",
                    type=ProjectionKind.PROPOSAL,
                    target=TimelineReference("task", id="japanese"),
                    visual_state=ProjectionVisualState.PROPOSED,
                    start=1_786_617_600_000,
                    end=1_786_619_400_000,
                ),
            ),
            references=(TimelineReference("time_range", start=1, end=2),),
            scope=OperationScope(
                task_ids=("japanese",),
                reminder_ids=("materials",),
                time_ranges=(TimeRange(1, 2),),
            ),
            ambiguity=0.1,
            risk=0.2,
            impact=0.3,
            reversible=True,
            required_autonomy_level=1,
            created_at=self.now,
            updated_at=self.now,
            version=2,
            proposal=proposal,
        )

        restored = operation_from_dict(operation_to_dict(operation))

        self.assertEqual(restored, operation)
        self.assertEqual(restored.compiled_operations[0].task.recurrence.frequency, "daily")

    def test_contracts_are_immutable_and_validate_state_invariants(self) -> None:
        intent = IntentSnapshot("create_task", "创建任务", attributes={"source": "user"})
        with self.assertRaises(FrozenInstanceError):
            intent.kind = "changed"  # type: ignore[misc]
        with self.assertRaises(TypeError):
            intent.attributes["source"] = "agent"  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "unresolved questions"):
            AgentOperation(
                id="operation-1",
                state=OperationState.AWAITING_CLARIFICATION,
                intent=intent,
                unresolved_questions=(),
                compiled_operations=(),
                projections=(),
                references=(),
                scope=OperationScope(),
                ambiguity=0.8,
                risk=0,
                impact=0,
                reversible=True,
                required_autonomy_level=0,
                created_at=self.now,
                updated_at=self.now,
                version=1,
            )

    def test_scope_overlap_is_object_and_time_aware(self) -> None:
        first = OperationScope(task_ids=("task-1",), time_ranges=(TimeRange(10, 20),))
        self.assertTrue(first.overlaps(OperationScope(task_ids=("task-1",))))
        self.assertTrue(first.overlaps(OperationScope(time_ranges=(TimeRange(19, 30),))))
        self.assertFalse(first.overlaps(OperationScope(time_ranges=(TimeRange(20, 30),))))

    def test_strict_decoder_rejects_unknown_operation_type_and_schema(self) -> None:
        clarification = AgentOperation(
            id="operation-1",
            state=OperationState.AWAITING_CLARIFICATION,
            intent=IntentSnapshot("create_task", "创建任务"),
            unresolved_questions=(ClarificationState("time", "几点？", ("上午", "晚上")),),
            compiled_operations=(),
            projections=(),
            references=(),
            scope=OperationScope(),
            ambiguity=0.8,
            risk=0.1,
            impact=0.1,
            reversible=True,
            required_autonomy_level=0,
            created_at=self.now,
            updated_at=self.now,
            version=1,
        )
        payload = operation_to_dict(clarification)
        payload["schema_version"] = 99
        with self.assertRaisesRegex(ValueError, "schema version"):
            operation_from_dict(payload)

    def test_autonomy_policy_defaults_to_suggest_only(self) -> None:
        policy = AutonomyPolicy()
        self.assertEqual(policy.level, 0)
