from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from zoneinfo import ZoneInfo

from chronos.api.routes.v1 import V1Router
from chronos.infrastructure.sqlite_proposals import SQLiteProposalRepository
from chronos.infrastructure.sqlite_schedule import SQLiteScheduleRepository
from chronos.schedule.proposals import ProposalService
from chronos.schedule.service import ScheduleService


class ScheduleV1Test(TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        database = Path(self.temporary.name) / "chronos.sqlite3"
        self.repository = SQLiteScheduleRepository(database)
        self.schedule = ScheduleService(self.repository)
        self.proposals = ProposalService(
            self.schedule, SQLiteProposalRepository(database)
        )
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
        flexible_projection = next(
            item for item in timeline if item["id"] == flexible.task_id
        )

        self.assertEqual(first_plan.status.value, "active")
        self.assertEqual(second_plan.version, 2)
        self.assertEqual(
            flexible_projection["start"], int(start.timestamp() * 1000) + 3_600_000
        )
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
            self.proposals.create(
                request, now=datetime(2026, 8, 3, 8, 0, tzinfo=self.zone)
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
