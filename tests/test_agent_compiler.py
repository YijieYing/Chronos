from datetime import UTC, datetime
from unittest import TestCase

from chronos.agent.compiler import (
    ClarificationCompilerResult,
    InformationalCompilerResult,
    LegacyProposalCompiler,
    ProposalCompilerResult,
    compiler_result_from_dict,
    compiler_result_to_dict,
)
from chronos.agent.models import (
    CreateReminderOperation,
    CreateTaskOperation,
    InteractionContext,
    OperationState,
    TimelineReference,
)


class AgentCompilerTest(TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
        self.context = InteractionContext(
            current_time=int(self.now.timestamp() * 1000),
            user_input="每天晚上安排30分钟日语",
            selection=TimelineReference("time_range", start=100, end=200),
        )
        self.compiler = LegacyProposalCompiler()

    def test_task_proposal_becomes_strict_ir_and_round_trips(self) -> None:
        proposal = self._task_proposal()
        context = self._with_proposal(proposal)

        result = self.compiler.compile(context)

        self.assertIsInstance(result, ProposalCompilerResult)
        self.assertEqual(result.operation.state, OperationState.PROPOSED)
        primitive = result.operation.compiled_operations[0]
        self.assertIsInstance(primitive, CreateTaskOperation)
        self.assertEqual(primitive.task.recurrence.frequency, "daily")
        self.assertEqual(result.operation.proposal.operations, (primitive,))
        self.assertEqual(compiler_result_from_dict(compiler_result_to_dict(result)), result)

    def test_reminder_and_clarification_results_have_distinct_states(self) -> None:
        reminder = {
            **self._base("reminder", "pending"),
            "command": {"type": "create_reminder"},
            "reminder_drafts": [
                {
                    "reminder": {
                        "id": "materials",
                        "title": "交材料",
                        "trigger": {"type": "window", "start": 100, "end": 200},
                        "delivery": "context-aware",
                        "priority": 3,
                    }
                }
            ],
            "changes": [],
        }
        clarification = {
            **self._base("clarification", "needs_clarification"),
            "command": None,
            "clarifications": [{"field": "duration", "question": "需要多久？"}],
            "interaction_context": {
                "selection": {"type": "time_range", "start": 100, "end": 200}
            },
        }

        reminder_result = self.compiler.compile(self._with_proposal(reminder))
        clarification_result = self.compiler.compile(self._with_proposal(clarification))

        self.assertIsInstance(reminder_result, ProposalCompilerResult)
        self.assertIsInstance(
            reminder_result.operation.compiled_operations[0], CreateReminderOperation
        )
        self.assertIsInstance(clarification_result, ClarificationCompilerResult)
        self.assertEqual(len(clarification_result.operation.unresolved_questions), 1)
        self.assertEqual(clarification_result.operation.projections[0].start, 100)

    def test_informational_result_cannot_contain_executable_state(self) -> None:
        informational = {
            **self._base("query", "informational"),
            "command": {"type": "query_schedule"},
        }

        result = self.compiler.compile(self._with_proposal(informational))

        self.assertIsInstance(result, InformationalCompilerResult)
        self.assertEqual(result.operation.state, OperationState.COMPLETED)
        payload = compiler_result_to_dict(result)
        payload["outcome"] = "proposal"
        with self.assertRaisesRegex(ValueError, "requires proposed"):
            compiler_result_from_dict(payload)

    def test_compiler_has_no_repository_side_effect(self) -> None:
        proposal = self._task_proposal()

        first = self.compiler.compile(self._with_proposal(proposal))
        second = self.compiler.compile(self._with_proposal(proposal))

        self.assertEqual(first, second)

    def _with_proposal(self, proposal: dict[str, object]) -> InteractionContext:
        return InteractionContext(
            current_time=self.context.current_time,
            user_input=self.context.user_input,
            selection=self.context.selection,
            timeline_context={"legacy_proposal": proposal},
        )

    def _task_proposal(self) -> dict[str, object]:
        task = {
            "task_id": "japanese",
            "title": "日语",
            "estimated_minutes": 30,
            "preferred_start": "2026-08-13T19:00:00+08:00",
            "task_type": "research",
            "fixed": False,
            "recurrence": {"frequency": "daily"},
        }
        return {
            **self._base("task", "pending"),
            "command": {"type": "create_task", "task_id": "japanese", "after": task},
            "changes": [{"operation": "add", "task_id": "japanese"}],
            "proposed_task": {
                "id": "japanese",
                "title": "日语",
                "start": 1_786_617_600_000,
                "end": 1_786_619_400_000,
                "fixed": False,
            },
        }

    @staticmethod
    def _base(operation_id: str, status: str) -> dict[str, object]:
        return {
            "proposal_id": operation_id,
            "status": status,
            "explanation": ["编译完成。"],
            "context_used": [],
            "parser_warnings": [],
            "parser_mode": "deterministic",
            "command": None,
            "commands": [],
            "changes": [],
            "proposed_task": None,
            "results": [],
            "reminder_drafts": [],
            "clarifications": [],
        }
