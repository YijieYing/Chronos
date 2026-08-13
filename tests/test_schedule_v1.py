import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from zoneinfo import ZoneInfo

from chronos.agent.log_service import ChronosLogService
from chronos.agent.projection_service import ProjectionService
from chronos.agent.service import OperationStore
from chronos.api.routes.v1 import V1Router
from chronos.infrastructure.sqlite_chronos_log import SQLiteChronosLogRepository
from chronos.infrastructure.sqlite_operations import SQLiteAgentOperationRepository
from chronos.infrastructure.sqlite_proposals import SQLiteProposalRepository
from chronos.infrastructure.sqlite_reminders import SQLiteReminderRepository
from chronos.infrastructure.sqlite_schedule import SQLiteScheduleRepository
from chronos.reminders.service import ReminderService
from chronos.schedule.agent_interpretation import (
    AgentInterpretation,
    InterpretedReminder,
    InterpretedTask,
    UnresolvedField,
)
from chronos.schedule.models import Task, TaskStatus
from chronos.schedule.proposals import ProposalService
from chronos.schedule.service import ScheduleService


class ScheduleV1Test(TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        database = Path(self.temporary.name) / "chronos.sqlite3"
        self.repository = SQLiteScheduleRepository(database)
        self.schedule = ScheduleService(self.repository)
        self.proposals = ProposalService(self.schedule, SQLiteProposalRepository(database))
        self.router = V1Router(self.schedule, self.proposals)
        self.zone = ZoneInfo("Asia/Shanghai")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_timeline_tasks_are_projected_from_activated_plans(self) -> None:
        start = datetime(2026, 8, 4, 10, 0, tzinfo=self.zone)
        fixed, first_plan = self.schedule.create_scheduled_task(
            title="Fixed meeting",
            estimated_minutes=60,
            priority=3,
            preferred_start=start,
            fixed=True,
            task_type="meeting",
        )
        flexible, second_plan = self.schedule.create_scheduled_task(
            title="Write code",
            estimated_minutes=60,
            priority=3,
            preferred_start=start,
            task_type="coding",
        )

        timeline = self.schedule.timeline()["tasks"]
        flexible_projection = next(item for item in timeline if item["id"] == flexible.task_id)

        self.assertEqual(first_plan.status.value, "active")
        self.assertEqual(second_plan.version, 2)
        self.assertEqual(flexible_projection["start"], int(start.timestamp() * 1000) + 3_600_000)
        self.assertEqual(
            next(item for item in timeline if item["id"] == fixed.task_id)["fixed"],
            True,
        )

    def test_proposal_is_persisted_but_task_waits_for_acceptance(self) -> None:
        now = datetime(2026, 8, 3, 9, 0, tzinfo=self.zone)
        proposal = self.proposals.create("明天下午安排60分钟写代码", now=now)

        self.assertEqual(proposal["status"], "pending")
        self.assertEqual(self.repository.list_tasks(), [])
        self.assertEqual(len(self.proposals.list()), 1)

        accepted = self.proposals.accept(str(proposal["proposal_id"]))

        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(len(self.repository.list_tasks()), 1)
        self.assertIsNotNone(accepted["activated_plan_id"])

        restored = self.proposals.restore(str(proposal["proposal_id"]))

        self.assertEqual(restored["status"], "restored")
        self.assertEqual(self.repository.list_tasks(), [])

    def test_v1_router_wraps_responses_and_reject_does_not_create_task(self) -> None:
        status, envelope = self.router.dispatch(
            "POST", "/api/v1/proposals", {"text": "安排30分钟阅读"}
        )
        proposal = envelope["data"]
        assert isinstance(proposal, dict)

        reject_status, rejected_envelope = self.router.dispatch(
            "POST", f"/api/v1/proposals/{proposal['proposal_id']}/reject"
        )

        self.assertEqual(status.value, 201)
        self.assertEqual(reject_status.value, 200)
        self.assertEqual(envelope["schema_version"], 1)
        self.assertIsNone(envelope["error"])
        self.assertEqual(rejected_envelope["data"]["status"], "rejected")
        self.assertEqual(self.repository.list_tasks(), [])

    def test_proposal_lifecycle_is_appended_to_chronos_log(self) -> None:
        database = Path(self.temporary.name) / "chronos.sqlite3"
        chronos_log = ChronosLogService(SQLiteChronosLogRepository(database))
        router = V1Router(self.schedule, self.proposals, chronos_log=chronos_log)

        _, envelope = router.dispatch(
            "POST", "/api/v1/proposals", {"text": "明天下午安排30分钟阅读"}
        )
        proposal = envelope["data"]
        assert isinstance(proposal, dict)
        router.dispatch("POST", f"/api/v1/proposals/{proposal['proposal_id']}/reject")
        status, log_envelope = router.dispatch("GET", "/api/v1/chronos-log")
        data = log_envelope["data"]
        assert isinstance(data, dict)

        self.assertEqual(status.value, 200)
        self.assertEqual(
            [entry["event_type"] for entry in data["entries"]],
            ["operation_rejected", "proposal_created", "user_prompt"],
        )
        self.assertTrue(
            all(entry["operation_id"] == proposal["proposal_id"] for entry in data["entries"])
        )
        self.assertEqual(data["pending_count"], 0)

    def test_interaction_context_selection_is_validated_persisted_and_logged(self) -> None:
        database = Path(self.temporary.name) / "chronos.sqlite3"
        chronos_log = ChronosLogService(SQLiteChronosLogRepository(database))
        router = V1Router(self.schedule, self.proposals, chronos_log=chronos_log)
        context = {
            "current_time": 1_786_608_000_000,
            "selection": {
                "type": "time_range",
                "start": 1_786_608_000_000,
                "end": 1_786_615_200_000,
            },
        }

        _, envelope = router.dispatch(
            "POST",
            "/api/v1/proposals",
            {"text": "这里安排30分钟阅读", "interaction_context": context},
        )
        proposal = envelope["data"]
        assert isinstance(proposal, dict)

        self.assertEqual(proposal["interaction_context"], context)
        entries = chronos_log.list()
        self.assertTrue(
            any(
                reference.type == "time_range"
                and reference.start == context["selection"]["start"]
                and reference.end == context["selection"]["end"]
                for entry in entries
                for reference in entry.references
            )
        )

        with self.assertRaisesRegex(ValueError, "positive duration"):
            router.dispatch(
                "POST",
                "/api/v1/proposals",
                {
                    "text": "安排30分钟阅读",
                    "interaction_context": {
                        "current_time": context["current_time"],
                        "selection": {
                            "type": "time_range",
                            "start": 20,
                            "end": 10,
                        },
                    },
                },
            )
        self.assertEqual(len(self.proposals.list()), 1)

    def test_pending_proposal_projection_disappears_after_resolution(self) -> None:
        database = Path(self.temporary.name) / "chronos.sqlite3"
        operation_store = OperationStore(SQLiteAgentOperationRepository(database))
        router = V1Router(
            self.schedule,
            self.proposals,
            projections=ProjectionService(operation_store),
        )
        _, envelope = router.dispatch(
            "POST", "/api/v1/proposals", {"text": "明天下午安排30分钟阅读"}
        )
        proposal = envelope["data"]
        assert isinstance(proposal, dict)

        _, projected = router.dispatch("GET", "/api/v1/timeline-projections")
        projection_data = projected["data"]
        assert isinstance(projection_data, dict)
        self.assertEqual(len(projection_data["projections"]), 1)
        self.assertEqual(
            projection_data["projections"][0]["operation_id"],
            proposal["proposal_id"],
        )

        router.dispatch("POST", f"/api/v1/proposals/{proposal['proposal_id']}/reject")
        _, projected = router.dispatch("GET", "/api/v1/timeline-projections")
        projection_data = projected["data"]
        assert isinstance(projection_data, dict)
        self.assertEqual(projection_data["projections"], [])

        _, accepted_envelope = router.dispatch(
            "POST", "/api/v1/proposals", {"text": "后天下午安排30分钟写作"}
        )
        accepted_proposal = accepted_envelope["data"]
        assert isinstance(accepted_proposal, dict)
        router.dispatch(
            "POST",
            f"/api/v1/proposals/{accepted_proposal['proposal_id']}/accept",
        )
        _, projected = router.dispatch("GET", "/api/v1/timeline-projections")
        projection_data = projected["data"]
        assert isinstance(projection_data, dict)
        self.assertEqual(projection_data["projections"], [])
        accepted_task_id = str(accepted_proposal["changes"][0]["task_id"])
        self.assertTrue(
            any(task.task_id == accepted_task_id for task in self.schedule.list_tasks())
        )

    def test_v1_router_creates_and_updates_an_independent_reminder(self) -> None:
        database = Path(self.temporary.name) / "chronos.sqlite3"
        reminders = ReminderService(SQLiteReminderRepository(database))
        router = V1Router(self.schedule, self.proposals, reminders=reminders)
        at = int(datetime(2026, 8, 13, 15, 20, tzinfo=UTC).timestamp() * 1000)

        status, envelope = router.dispatch(
            "POST",
            "/api/v1/reminders",
            {
                "id": "parcel",
                "title": "取快递",
                "trigger": {"type": "time", "at": at},
                "delivery": "exact",
                "priority": 3,
            },
        )
        updated_status, updated = router.dispatch(
            "PUT", "/api/v1/reminders/parcel", {"status": "done"}
        )

        self.assertEqual(status.value, 201)
        self.assertEqual(envelope["data"]["trigger"], {"type": "time", "at": at})
        self.assertEqual(updated_status.value, 200)
        self.assertEqual(updated["data"]["status"], "done")
        self.assertEqual(self.repository.list_tasks(), [])

    def test_deterministic_parser_never_degrades_a_reminder_into_a_task(self) -> None:
        with self.assertRaisesRegex(ValueError, "避免误建成 Task"):
            self.proposals.create(
                "16:00提醒我取快递",
                now=datetime(2026, 8, 13, 9, 0, tzinfo=self.zone),
            )

        self.assertEqual(self.repository.list_tasks(), [])

    def test_stale_proposal_cannot_overwrite_a_newer_plan(self) -> None:
        now = datetime(2026, 8, 3, 9, 0, tzinfo=self.zone)
        proposal = self.proposals.create("明天下午安排60分钟写代码", now=now)
        self.schedule.create_scheduled_task(
            title="Newer change",
            estimated_minutes=30,
            priority=3,
            preferred_start=datetime(2026, 8, 4, 10, 0, tzinfo=self.zone),
        )

        with self.assertRaisesRegex(ValueError, "stale"):
            self.proposals.accept(str(proposal["proposal_id"]))

    def test_agent_can_move_and_resize_an_existing_task(self) -> None:
        task, _ = self.schedule.create_scheduled_task(
            title="Write code",
            estimated_minutes=60,
            priority=3,
            preferred_start=datetime(2026, 8, 4, 10, 0, tzinfo=self.zone),
            task_type="coding",
        )

        moved = self.proposals.create(
            "把 Write code 移动到明天下午", now=datetime(2026, 8, 3, 9, 0, tzinfo=self.zone)
        )
        self.assertEqual(moved["command"]["type"], "update_task")
        self.assertEqual(self.repository.get_task(task.task_id).preferred_start.hour, 10)
        self.proposals.accept(str(moved["proposal_id"]))
        self.assertEqual(self.repository.get_task(task.task_id).preferred_start.hour, 14)

        resized = self.proposals.create(
            "把 Write code 延长30分钟", now=datetime(2026, 8, 3, 9, 0, tzinfo=self.zone)
        )
        self.proposals.accept(str(resized["proposal_id"]))
        self.assertEqual(self.repository.get_task(task.task_id).estimated_minutes, 90)

    def test_agent_can_delete_restore_and_query_tasks(self) -> None:
        task, _ = self.schedule.create_scheduled_task(
            title="Team sync",
            estimated_minutes=30,
            priority=3,
            preferred_start=datetime(2026, 8, 4, 11, 0, tzinfo=self.zone),
            task_type="meeting",
        )
        query = self.proposals.create(
            "查找明天的 Team sync", now=datetime(2026, 8, 3, 9, 0, tzinfo=self.zone)
        )
        self.assertEqual(query["status"], "informational")
        self.assertFalse(query["requires_confirmation"])
        self.assertEqual(query["results"][0]["id"], task.task_id)

        deletion = self.proposals.create(
            "删除 Team sync", now=datetime(2026, 8, 3, 9, 0, tzinfo=self.zone)
        )
        self.proposals.accept(str(deletion["proposal_id"]))
        self.assertIsNone(self.repository.get_task(task.task_id))
        self.proposals.restore(str(deletion["proposal_id"]))
        self.assertIsNotNone(self.repository.get_task(task.task_id))

    def test_agent_can_create_a_weekly_recurring_task(self) -> None:
        proposal = self.proposals.create(
            "每周一、三、五上午9:00安排60分钟日语学习",
            now=datetime(2026, 8, 3, 8, 0, tzinfo=self.zone),
        )

        self.assertEqual(
            proposal["command"]["recurrence"],
            {"frequency": "weekly", "weekdays": [1, 3, 5]},
        )
        self.assertEqual(proposal["proposed_task"]["title"], "日语学习")
        self.assertEqual(proposal["proposed_task"]["recurrence"]["frequency"], "weekly")
        self.assertTrue(proposal["proposed_task"]["fixed"])

        self.proposals.accept(str(proposal["proposal_id"]))
        task = self.repository.list_tasks()[0]
        self.assertEqual(task.recurrence, {"frequency": "weekly", "weekdays": [1, 3, 5]})

    def test_agent_rejects_a_multi_task_plan_instead_of_using_it_as_title(self) -> None:
        request = """请安排以下计划：
- 每天 9:00 学日语 30 分钟
- 每周一 14:00 写周报 60 分钟
"""

        with self.assertRaisesRegex(ValueError, "一次只能创建一个任务"):
            self.proposals.create(request, now=datetime(2026, 8, 3, 8, 0, tzinfo=self.zone))

    def test_interpreted_morning_evening_batch_is_previewed_and_applied_atomically(self) -> None:
        now = datetime(2026, 8, 12, 6, 0, tzinfo=self.zone)
        interpretation = AgentInterpretation(
            intent="create_schedule",
            tasks=(
                InterpretedTask(
                    title="Morning routine",
                    title_source="Morning routine",
                    duration_minutes=30,
                    duration_source="30 minutes",
                    preferred_start=now.replace(hour=7, minute=30),
                    temporal_source="07:30",
                    task_type="execution",
                    recurrence={"frequency": "daily"},
                    recurrence_sources={"frequency": ("every day",)},
                    fixed=True,
                ),
                InterpretedTask(
                    title="Evening review",
                    title_source="Evening review",
                    duration_minutes=30,
                    duration_source="30 minutes",
                    preferred_start=now.replace(hour=21, minute=30),
                    temporal_source="21:30",
                    task_type="execution",
                    recurrence={"frequency": "daily"},
                    recurrence_sources={"frequency": ("every day",)},
                    fixed=True,
                ),
            ),
        )
        service = ProposalService(
            self.schedule,
            SQLiteProposalRepository(Path(self.temporary.name) / "chronos.sqlite3"),
            _InterpretationParser(interpretation),
        )

        proposal = service.create("private regression prompt", now=now)

        self.assertEqual(proposal["status"], "pending")
        self.assertEqual(len(proposal["commands"]), 2)
        self.assertEqual(len(proposal["draft_plans"]), 14)
        self.assertEqual(len(proposal["results"]), 28)
        self.assertEqual(self.repository.list_tasks(), [])

        accepted = service.accept(str(proposal["proposal_id"]))

        stored = self.repository.list_tasks()
        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(len(stored), 2)
        self.assertTrue(all(task.recurrence == {"frequency": "daily"} for task in stored))
        self.assertEqual(
            {task.preferred_start.strftime("%H:%M") for task in stored},
            {"07:30", "21:30"},
        )

        timeline = self.schedule.timeline(horizon_days=14)["tasks"]
        self.assertEqual(len(timeline), 28)
        self.assertEqual(
            {item["series_id"] for item in timeline}, {task.task_id for task in stored}
        )
        self.assertEqual(
            {
                datetime.fromtimestamp(item["start"] / 1000, self.zone).strftime("%H:%M")
                for item in timeline
            },
            {"07:30", "21:30"},
        )

        restored = service.restore(str(proposal["proposal_id"]))
        self.assertEqual(restored["status"], "restored")
        self.assertEqual(self.repository.list_tasks(), [])

    def test_task_and_horizon_plans_roll_back_as_one_transaction(self) -> None:
        task = Task(
            task_id="atomic-task",
            title="Atomic task",
            estimated_minutes=30,
            priority=3,
            status=TaskStatus.BACKLOG,
            created_at=datetime.now(UTC),
            preferred_start=datetime(2026, 8, 12, 7, 30, tzinfo=self.zone),
            recurrence={"frequency": "daily"},
            fixed=True,
        )
        plans = self.schedule.preview_horizon([task], date(2026, 8, 12), days=2)

        with self.assertRaisesRegex(sqlite3.IntegrityError, "UNIQUE constraint"):
            self.repository.apply_task_plan_batch([task], [plans[0], plans[0]])

        self.assertIsNone(self.repository.get_task(task.task_id))
        self.assertIsNone(self.repository.get_plan(plans[0].plan_id))

    def test_unresolved_interpretation_returns_clarification_without_planning(self) -> None:
        interpretation = AgentInterpretation(
            intent="create_schedule",
            tasks=(),
            unresolved=(UnresolvedField("tasks[0].duration", "每次需要多久？"),),
        )
        service = ProposalService(
            self.schedule,
            SQLiteProposalRepository(Path(self.temporary.name) / "chronos.sqlite3"),
            _InterpretationParser(interpretation),
        )

        proposal = service.create("每天学习", now=datetime(2026, 8, 12, 6, 0, tzinfo=self.zone))

        self.assertEqual(proposal["status"], "needs_clarification")
        self.assertFalse(proposal["requires_confirmation"])
        self.assertEqual(proposal["clarifications"][0]["question"], "每次需要多久？")
        self.assertEqual(self.repository.list_tasks(), [])

    def test_bounded_daily_batch_stops_on_inclusive_until_date(self) -> None:
        now = datetime(2026, 8, 12, 6, 0, tzinfo=self.zone)
        recurrence = {"frequency": "daily", "until": "2026-08-18"}
        evidence = {"frequency": ("每天",), "until": ("到8.18为止",)}
        interpretation = AgentInterpretation(
            intent="create_schedule",
            tasks=(
                InterpretedTask(
                    title="托福口语练习",
                    title_source="托福口语练习",
                    duration_minutes=90,
                    duration_source="七点半到九点",
                    preferred_start=now.replace(hour=7, minute=30),
                    temporal_source="早上七点半到九点",
                    task_type="research",
                    recurrence=recurrence,
                    recurrence_sources=evidence,
                    fixed=True,
                ),
                InterpretedTask(
                    title="作文练习",
                    title_source="作文练习",
                    duration_minutes=90,
                    duration_source="晚上九点半到十一点",
                    preferred_start=now.replace(hour=21, minute=30),
                    temporal_source="晚上九点半到十一点",
                    task_type="creative",
                    recurrence=recurrence,
                    recurrence_sources=evidence,
                    fixed=True,
                ),
            ),
        )
        service = ProposalService(
            self.schedule,
            SQLiteProposalRepository(Path(self.temporary.name) / "chronos.sqlite3"),
            _InterpretationParser(interpretation),
        )

        proposal = service.create("bounded private regression", now=now)

        self.assertEqual(proposal["status"], "pending")
        self.assertEqual(len(proposal["results"]), 14)
        last_dates = {
            datetime.fromtimestamp(item["start"] / 1000, self.zone).date()
            for item in proposal["results"]
        }
        self.assertEqual(min(last_dates), date(2026, 8, 12))
        self.assertEqual(max(last_dates), date(2026, 8, 18))
        self.assertEqual(
            proposal["commands"][1]["provenance"]["recurrence"]["frequency"],
            ["每天"],
        )

    def test_overlapping_agent_tasks_return_proposal_conflicts(self) -> None:
        now = datetime(2026, 8, 13, 8, 0, tzinfo=self.zone)
        interpretation = AgentInterpretation(
            intent="create_schedule",
            tasks=tuple(
                InterpretedTask(
                    title=title,
                    title_source=title,
                    duration_minutes=60,
                    duration_source="一小时",
                    preferred_start=now.replace(hour=13),
                    temporal_source="下午一点到两点",
                    task_type="research",
                    recurrence={"frequency": "daily", "until": "2026-08-16"},
                    recurrence_sources={
                        "frequency": ("每天",),
                        "until": ("到本周末",),
                    },
                    fixed=True,
                )
                for title in ("阅读", "词汇")
            ),
        )
        service = ProposalService(
            self.schedule,
            SQLiteProposalRepository(Path(self.temporary.name) / "chronos.sqlite3"),
            _InterpretationParser(interpretation),
        )

        proposal = service.create("private overlapping regression", now=now)

        self.assertEqual(proposal["status"], "pending")
        self.assertEqual(len(proposal["conflicts"]), 4)
        self.assertEqual(
            {item["reason"] for item in proposal["conflicts"]},
            {"fixed_time_conflict"},
        )

    def test_existing_database_receives_additive_task_columns(self) -> None:
        database = Path(self.temporary.name) / "legacy.sqlite3"
        import sqlite3

        with sqlite3.connect(database) as connection:
            connection.execute(
                """
                CREATE TABLE tasks (
                    task_id TEXT PRIMARY KEY, title TEXT NOT NULL,
                    estimated_minutes INTEGER NOT NULL, priority INTEGER NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL, deadline TEXT,
                    splittable INTEGER NOT NULL, min_chunk_minutes INTEGER NOT NULL
                )
                """
            )

        migrated = SQLiteScheduleRepository(database)
        task = ScheduleService(migrated).create_task(
            title="Migrated", estimated_minutes=30, priority=3
        )

        self.assertEqual(migrated.get_task(task.task_id).task_type, "execution")

    def test_agent_reminder_waits_for_confirmation_and_can_restore(self) -> None:
        reminders = ReminderService(
            SQLiteReminderRepository(Path(self.temporary.name) / "chronos.sqlite3")
        )
        interpretation = AgentInterpretation(
            intent="create_reminder",
            tasks=(),
            reminders=(
                InterpretedReminder(
                    title="取快递",
                    title_source="取快递",
                    trigger_type="time",
                    trigger_at=datetime(2026, 8, 13, 15, 20, tzinfo=self.zone),
                    window_start=None,
                    window_end=None,
                    temporal_sources=("15:20",),
                    delivery="exact",
                ),
            ),
        )
        service = ProposalService(
            self.schedule,
            SQLiteProposalRepository(Path(self.temporary.name) / "chronos.sqlite3"),
            _InterpretationParser(interpretation),
            reminders,
        )

        proposal = service.create("15:20提醒我取快递")
        self.assertEqual(proposal["status"], "pending")
        self.assertEqual(reminders.list(), [])
        service.accept(str(proposal["proposal_id"]))
        self.assertEqual(reminders.list()[0]["title"], "取快递")
        service.restore(str(proposal["proposal_id"]))
        self.assertEqual(reminders.list(), [])


class _InterpretationParser:
    def __init__(self, interpretation: AgentInterpretation) -> None:
        self.interpretation = interpretation

    def interpret(self, text, now, tasks):
        return self.interpretation
