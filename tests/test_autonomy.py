from dataclasses import replace
from datetime import UTC, datetime
from unittest import TestCase

from chronos.agent.autonomy import evaluate_autonomy, policy_for_level
from chronos.agent.models import (
    AgentOperation,
    CreateReminderOperation,
    IntentSnapshot,
    OperationScope,
    OperationState,
    ProposalSnapshot,
    ReminderSpec,
)


class AutonomyGateTest(TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
        primitive = CreateReminderOperation(
            "parcel", ReminderSpec("取快递", "time", at=1_786_617_600_000)
        )
        self.operation = AgentOperation(
            id="safe-reminder",
            state=OperationState.PROPOSED,
            intent=IntentSnapshot("create_reminder", "创建提醒"),
            unresolved_questions=(),
            compiled_operations=(primitive,),
            projections=(),
            references=(),
            scope=OperationScope(reminder_ids=("parcel",)),
            ambiguity=0.05,
            risk=0.05,
            impact=0.1,
            reversible=True,
            required_autonomy_level=1,
            created_at=self.now,
            updated_at=self.now,
            version=1,
            proposal=ProposalSnapshot("safe-reminder", 1, (primitive,), self.now),
        )

    def test_suggest_only_never_executes(self) -> None:
        decision = evaluate_autonomy(self.operation, policy_for_level(0))
        self.assertFalse(decision.execute)

    def test_safe_reversible_reminder_executes_at_level_one(self) -> None:
        decision = evaluate_autonomy(self.operation, policy_for_level(1))
        self.assertTrue(decision.execute)

    def test_any_threshold_or_reversibility_failure_requires_proposal(self) -> None:
        policy = policy_for_level(3)
        for operation in (
            replace(self.operation, risk=0.8),
            replace(self.operation, ambiguity=0.4),
            replace(self.operation, impact=0.9),
            replace(self.operation, reversible=False),
        ):
            self.assertFalse(evaluate_autonomy(operation, policy).execute)
