from datetime import UTC, datetime
from unittest import TestCase

from chronos.agent.compiler import (
    ClarificationCompilerResult,
    InformationalCompilerResult,
    LegacyProposalCompiler,
    LLMChronosCompiler,
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
    UpdateTaskOperation,
)
from chronos.schedule.models import Task, TaskStatus
from chronos.schedule.semantic_parser import SemanticScheduleCommandParser


class StaticProvider:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    def generate(self, _system: str, _prompt: str) -> str:
        self.calls += 1
        return self.response


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


class LLMChronosCompilerTest(TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 13, 4, 0, tzinfo=UTC)

    def compile(
        self,
        text: str,
        response: str,
        *,
        selection: TimelineReference | None = None,
        tasks: tuple[Task, ...] = (),
    ):
        provider = StaticProvider(response)
        compiler = LLMChronosCompiler(
            SemanticScheduleCommandParser(provider, fallback_on_error=False)
        )
        result = compiler.compile(InteractionContext(
            current_time=int(self.now.timestamp() * 1000),
            user_input=text,
            selection=selection,
            timeline_context={"tasks": tasks, "timezone": "Asia/Shanghai"},
        ))
        return result, provider

    def test_compiles_flexible_recurring_evening_task(self) -> None:
        text = "每天晚上安排30分钟日语，但时间不用卡死。"
        response = """{
          "intent":"create_schedule","tasks":[{
            "title":"日语","title_source":"日语","duration_minutes":30,
            "duration_source":"30分钟","preferred_start":"2026-08-13T19:00:00+08:00",
            "temporal_source":"晚上","task_type":"research",
            "recurrence":{"frequency":"daily"},
            "recurrence_sources":{"frequency":["每天"]},"fixed":false
          }],"unresolved":[],"assumptions":[]
        }"""

        result, provider = self.compile(text, response)

        self.assertIsInstance(result, ProposalCompilerResult)
        operation = result.operation.compiled_operations[0]
        self.assertIsInstance(operation, CreateTaskOperation)
        self.assertEqual(operation.task.recurrence.frequency, "daily")
        self.assertFalse(operation.task.fixed)
        self.assertIsNotNone(operation.task.window)
        self.assertEqual(provider.calls, 1)

    def test_compiles_context_aware_window_reminder(self) -> None:
        text = "下午我有空的时候提醒我交材料。"
        response = """{
          "intent":"create_reminder","tasks":[],"reminders":[{
            "title":"交材料","title_source":"交材料",
            "trigger":{"type":"window","start":"2026-08-13T12:00:00+08:00",
            "end":"2026-08-13T18:00:00+08:00"},
            "temporal_sources":["下午"],"delivery":"context-aware",
            "delivery_sources":["有空的时候"],"priority":3
          }],"unresolved":[],"assumptions":[]
        }"""

        result, _ = self.compile(text, response)

        operation = result.operation.compiled_operations[0]
        self.assertIsInstance(operation, CreateReminderOperation)
        self.assertEqual(operation.reminder.delivery, "context-aware")
        self.assertTrue(operation.reminder.prefer_interruptible_moment)

    def test_selected_task_resolves_short_update(self) -> None:
        task = Task(
            "research", "Research", 60, 3, TaskStatus.PLANNED, self.now,
            preferred_start=datetime(2026, 8, 13, 6, 0, tzinfo=UTC),
        )
        response = """{
          "intent":"update_task","tasks":[],"legacy_command":{
            "type":"update_task","preferred_start":"2026-08-13T19:00:00+08:00"
          },"unresolved":[],"assumptions":[]
        }"""

        result, _ = self.compile(
            "挪到晚上。", response,
            selection=TimelineReference("task", id="research"), tasks=(task,),
        )

        operation = result.operation.compiled_operations[0]
        self.assertIsInstance(operation, UpdateTaskOperation)
        self.assertEqual(operation.task_id, "research")

    def test_selected_range_grounds_new_task(self) -> None:
        start = int(datetime(2026, 8, 13, 10, 0, tzinfo=UTC).timestamp() * 1000)
        end = start + 4 * 60 * 60_000
        response = """{
          "intent":"create_schedule","tasks":[{
            "title":"阅读","title_source":"阅读","duration_minutes":60,
            "duration_source":"一小时","preferred_start":"2026-08-13T18:00:00+08:00",
            "temporal_source":"这里","task_type":"research","recurrence":null,
            "recurrence_sources":{},"fixed":false
          }],"unresolved":[],"assumptions":[]
        }"""

        result, _ = self.compile(
            "这里安排一小时阅读。", response,
            selection=TimelineReference("time_range", start=start, end=end),
        )

        operation = result.operation.compiled_operations[0]
        self.assertIsInstance(operation, CreateTaskOperation)
        self.assertGreaterEqual(operation.task.start, start)
        self.assertLess(operation.task.start, end)

    def test_broad_replan_stays_non_executable_until_replanner_exists(self) -> None:
        response = """{
          "intent":"replan_schedule","tasks":[],"unresolved":[{
            "field":"replan.scope","question":"你希望调整今天哪些可变任务？"
          }],"assumptions":[]
        }"""

        result, provider = self.compile("我今天起晚了，重新排一下。", response)

        self.assertIsInstance(result, ClarificationCompilerResult)
        self.assertEqual(result.operation.compiled_operations, ())
        self.assertEqual(result.operation.intent.kind, "replan_schedule")
        self.assertEqual(provider.calls, 2)
