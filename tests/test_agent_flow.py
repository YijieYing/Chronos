import json
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from zoneinfo import ZoneInfo

from chronos.agent.flow import Flow
from chronos.agent.interpreter import Interpreter
from chronos.agent.models import OperationState
from chronos.agent.runtime import ChronosRuntime
from chronos.agent.service import OperationStore
from chronos.api.routes.v1 import V1Router
from chronos.infrastructure.sqlite_operations import SQLiteAgentOperationRepository
from chronos.infrastructure.sqlite_proposals import SQLiteProposalRepository
from chronos.infrastructure.sqlite_reminders import SQLiteReminderRepository
from chronos.infrastructure.sqlite_schedule import SQLiteScheduleRepository
from chronos.infrastructure.sqlite_transactions import SQLiteAdjustmentTransactionRepository
from chronos.reminders.service import ReminderService
from chronos.schedule.proposals import ProposalService
from chronos.schedule.service import ScheduleService


class Model:
    def __init__(self, response: str) -> None:
        self.response = response

    def generate(self, _system: str, _prompt: str) -> str:
        return self.response


class SequenceModel:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, _system: str, encoded: str) -> str:
        self.calls += 1
        payload = json.loads(encoded)
        item_id = payload["items"][0]["id"]
        prompt = payload["items"][0]["text"]
        start = prompt.index("日语")
        meaning = {
            "type": "event", "item_ids": [item_id],
            "content": [{"item_id": item_id, "start": start, "end": start + 2}],
            "kind": "task", "request": {"type": "add"},
            "time": {"type": "period", "period": "afternoon"},
        }
        if self.calls == 1:
            meaning["gaps"] = [{
                "item_id": item_id, "field": "duration",
                "question": "日语需要多久？", "reason": "missing",
            }]
        else:
            meaning["duration"] = {"type": "exact", "minutes": 30}
        return json.dumps({"meanings": [meaning]}, ensure_ascii=False)


class FlowTest(TestCase):
    def test_task_edit_runs_through_flow_plan_operations_and_runtime(self) -> None:
        with TemporaryDirectory() as temporary:
            database = Path(temporary) / "chronos.sqlite3"
            schedule = ScheduleService(SQLiteScheduleRepository(database))
            zone = ZoneInfo("Asia/Shanghai")
            schedule.create_scheduled_task(
                task_id="task-edit", title="日语", estimated_minutes=30, priority=3,
                preferred_start=datetime(2026, 8, 17, 14, 0, tzinfo=zone), source="user",
            )
            reminders = ReminderService(SQLiteReminderRepository(database))
            store = OperationStore(SQLiteAgentOperationRepository(database))
            proposals = ProposalService(
                schedule, SQLiteProposalRepository(database), reminders=reminders
            )
            runtime = ChronosRuntime(
                store, schedule, reminders,
                SQLiteAdjustmentTransactionRepository(database),
            )
            prompt = "把日语移到下午三点并改成45分钟。"
            moved = int(datetime(2026, 8, 17, 15, 0, tzinfo=zone).timestamp() * 1000)

            class EditModel(Model):
                def generate(self, _system: str, encoded: str) -> str:
                    item_id = json.loads(encoded)["items"][0]["id"]
                    return json.dumps({"meanings": [{
                        "type": "event", "item_ids": [item_id],
                        "content": [{"item_id": item_id, "start": 1, "end": 3}],
                        "kind": "task",
                        "request": {
                            "type": "edit",
                            "target": {"type": "task", "id": "task-edit"},
                            "fields": ["time", "duration"],
                        },
                        "time": {"type": "point", "start": moved},
                        "duration": {"type": "exact", "minutes": 45},
                    }]}, ensure_ascii=False)

            flow = Flow(Interpreter(EditModel("")), store, schedule)
            router = V1Router(
                schedule, proposals, reminders=reminders,
                operation_store=store, runtime=runtime, flow=flow,
            )
            now = int(datetime(2026, 8, 17, 13, 0, tzinfo=zone).timestamp() * 1000)
            _, created = router.dispatch("POST", "/api/v1/proposals", {
                "text": prompt, "interaction_context": {"current_time": now},
            })
            proposal_id = created["data"]["proposal_id"]
            persisted = store.get(proposal_id)
            self.assertEqual(persisted.plan.changes[0].duration, 45)
            self.assertEqual(persisted.scope.task_ids, ("task-edit",))

            router.dispatch("POST", f"/api/v1/proposals/{proposal_id}/accept")

            edited = schedule.get_task("task-edit")
            self.assertEqual(int(edited.preferred_start.timestamp() * 1000), moved)
            self.assertEqual(edited.estimated_minutes, 45)

    def test_autonomy_executes_canonical_flow_operation(self) -> None:
        with TemporaryDirectory() as temporary:
            database = Path(temporary) / "chronos.sqlite3"
            schedule = ScheduleService(SQLiteScheduleRepository(database))
            schedule.update_settings({"autonomy_level": "1"})
            reminders = ReminderService(SQLiteReminderRepository(database))
            store = OperationStore(SQLiteAgentOperationRepository(database))
            proposals = ProposalService(schedule, SQLiteProposalRepository(database), reminders=reminders)
            runtime = ChronosRuntime(
                store, schedule, reminders,
                SQLiteAdjustmentTransactionRepository(database),
            )
            prompt = "下午安排半小时日语。"

            class BoundModel(Model):
                def generate(self, _system: str, encoded: str) -> str:
                    item_id = json.loads(encoded)["items"][0]["id"]
                    start = prompt.index("日语")
                    return json.dumps({"meanings": [{
                        "type": "event", "item_ids": [item_id],
                        "content": [{"item_id": item_id, "start": start, "end": start + 2}],
                        "kind": "task", "request": {"type": "add"},
                        "time": {"type": "period", "period": "afternoon"},
                        "duration": {"type": "exact", "minutes": 30},
                    }]}, ensure_ascii=False)

            flow = Flow(Interpreter(BoundModel("")), store, schedule)
            router = V1Router(
                schedule, proposals, reminders=reminders,
                operation_store=store, runtime=runtime, flow=flow,
            )
            now = datetime(2026, 8, 17, 13, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
            _, envelope = router.dispatch("POST", "/api/v1/proposals", {
                "text": prompt,
                "interaction_context": {"current_time": int(now.timestamp() * 1000)},
            })

            proposal = envelope["data"]
            self.assertEqual(proposal["status"], "accepted")
            self.assertEqual(store.get(proposal["proposal_id"]).state, OperationState.COMPLETED)
            self.assertEqual([task.title for task in schedule.list_tasks()], ["日语"])

    def test_directive_completes_without_planner_or_operations(self) -> None:
        with TemporaryDirectory() as temporary:
            database = Path(temporary) / "chronos.sqlite3"
            schedule = ScheduleService(SQLiteScheduleRepository(database))
            store = OperationStore(SQLiteAgentOperationRepository(database))
            prompt = "解释一下现在的安排"

            class DirectiveModel(Model):
                def generate(self, _system: str, encoded: str) -> str:
                    item_id = json.loads(encoded)["items"][0]["id"]
                    return json.dumps({"meanings": [{
                        "type": "directive", "item_ids": [item_id], "kind": "explain",
                        "content": [{"item_id": item_id, "start": 0, "end": len(prompt)}],
                        "response": "当前时间轴没有已安排任务。",
                    }]}, ensure_ascii=False)

            operation = Flow(Interpreter(DirectiveModel("")), store, schedule).submit(
                prompt, int(datetime.now(UTC).timestamp() * 1000)
            )

            self.assertEqual(operation.state, OperationState.COMPLETED)
            self.assertEqual(operation.compiled_operations, ())
            self.assertIsNone(operation.plan)
            self.assertEqual(operation.intent.summary, "当前时间轴没有已安排任务。")

    def test_api_clarification_rebuilds_snapshot_then_proposes(self) -> None:
        with TemporaryDirectory() as temporary:
            database = Path(temporary) / "chronos.sqlite3"
            schedule = ScheduleService(SQLiteScheduleRepository(database))
            reminders = ReminderService(SQLiteReminderRepository(database))
            store = OperationStore(SQLiteAgentOperationRepository(database))
            proposals = ProposalService(schedule, SQLiteProposalRepository(database), reminders=reminders)
            flow = Flow(Interpreter(SequenceModel()), store, schedule)
            router = V1Router(schedule, proposals, reminders=reminders,
                              operation_store=store, flow=flow)
            now = int(datetime(2026, 8, 17, 13, 0,
                               tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000)
            _, envelope = router.dispatch("POST", "/api/v1/proposals", {
                "text": "下午安排日语。", "interaction_context": {"current_time": now},
            })
            pending = envelope["data"]
            question = pending["clarifications"][0]
            self.assertEqual(pending["status"], "needs_clarification")

            _, answered = router.dispatch(
                "POST", f"/api/v1/operations/{pending['proposal_id']}/clarify",
                {"field": question["field"], "question": question["question"],
                 "answer": "30分钟", "interaction_context": {"current_time": now}},
            )

            proposal = answered["data"]
            self.assertEqual(proposal["status"], "pending")
            self.assertEqual(proposal["clarifications"], [])
            refreshed = store.get(pending["proposal_id"])
            self.assertEqual(refreshed.snapshot.version, 2)
            self.assertEqual(refreshed.snapshot.answers[0].text, "30分钟")

    def test_clarification_rechecks_canonical_autonomy(self) -> None:
        with TemporaryDirectory() as temporary:
            database = Path(temporary) / "chronos.sqlite3"
            schedule = ScheduleService(SQLiteScheduleRepository(database))
            schedule.update_settings({"autonomy_level": "1"})
            reminders = ReminderService(SQLiteReminderRepository(database))
            store = OperationStore(SQLiteAgentOperationRepository(database))
            proposals = ProposalService(schedule, SQLiteProposalRepository(database), reminders=reminders)
            runtime = ChronosRuntime(
                store, schedule, reminders,
                SQLiteAdjustmentTransactionRepository(database),
            )
            flow = Flow(Interpreter(SequenceModel()), store, schedule)
            router = V1Router(
                schedule, proposals, reminders=reminders,
                operation_store=store, runtime=runtime, flow=flow,
            )
            now = int(datetime(2026, 8, 17, 13, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000)
            _, created = router.dispatch("POST", "/api/v1/proposals", {
                "text": "下午安排日语。", "interaction_context": {"current_time": now},
            })
            pending = created["data"]
            question = pending["clarifications"][0]

            _, answered = router.dispatch(
                "POST", f"/api/v1/operations/{pending['proposal_id']}/clarify",
                {"field": question["field"], "question": question["question"],
                 "answer": "30分钟", "interaction_context": {"current_time": now}},
            )

            self.assertEqual(answered["data"]["status"], "accepted")
            self.assertEqual([task.title for task in schedule.list_tasks()], ["日语"])

    def test_real_prompt_follows_one_forward_path_and_persists_semantic_truth(self) -> None:
        with TemporaryDirectory() as temporary:
            database = Path(temporary) / "chronos.sqlite3"
            schedule = ScheduleService(SQLiteScheduleRepository(database))
            reminders = ReminderService(SQLiteReminderRepository(database))
            store = OperationStore(SQLiteAgentOperationRepository(database))
            transactions = SQLiteAdjustmentTransactionRepository(database)
            proposals = ProposalService(schedule, SQLiteProposalRepository(database), reminders=reminders)
            runtime = ChronosRuntime(store, schedule, reminders, transactions)
            prompt = "下午安排半小时日语。"
            start = prompt.index("日语")
            response = json.dumps({"meanings": [{
                "type": "event",
                "item_ids": [],
                "content": [],
                "kind": "task",
                "request": {"type": "add"},
                "time": {"type": "period", "period": "afternoon"},
                "duration": {"type": "exact", "minutes": 30},
            }]}, ensure_ascii=False)

            class BoundModel(Model):
                def generate(self, system: str, encoded: str) -> str:
                    payload = json.loads(encoded)
                    item_id = payload["items"][0]["id"]
                    value = json.loads(self.response)
                    value["meanings"][0]["item_ids"] = [item_id]
                    value["meanings"][0]["content"] = [
                        {"item_id": item_id, "start": start, "end": start + 2}
                    ]
                    return json.dumps(value, ensure_ascii=False)

            now = datetime(2026, 8, 17, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
            operation = Flow(Interpreter(BoundModel(response)), store, schedule).submit(
                prompt, int(now.timestamp() * 1000), prompt_id="prompt-flow"
            )

            persisted = store.get(operation.id)
            self.assertEqual(persisted.state, OperationState.PROPOSED)
            self.assertIsNotNone(persisted.snapshot)
            self.assertIsNotNone(persisted.plan)
            self.assertEqual(persisted.plan.snapshot_id, persisted.snapshot.id)
            self.assertEqual(persisted.proposal.plan_id, persisted.plan.id)
            self.assertEqual(len(persisted.compiled_operations), 1)
            self.assertEqual(len(persisted.projections), 1)
            self.assertEqual(persisted.projections[0].metadata["title"], "日语")

            runtime.execute(persisted, persisted.compiled_operations)

            tasks = schedule.list_tasks()
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0].title, "日语")
            self.assertGreaterEqual(
                int(tasks[0].preferred_start.timestamp() * 1000),
                int(now.astimezone(UTC).timestamp() * 1000),
            )

    def test_existing_proposal_endpoint_uses_flow_and_canonical_runtime(self) -> None:
        with TemporaryDirectory() as temporary:
            database = Path(temporary) / "chronos.sqlite3"
            schedule = ScheduleService(SQLiteScheduleRepository(database))
            reminders = ReminderService(SQLiteReminderRepository(database))
            store = OperationStore(SQLiteAgentOperationRepository(database))
            proposals = ProposalService(schedule, SQLiteProposalRepository(database), reminders=reminders)
            runtime = ChronosRuntime(
                store, schedule, reminders,
                SQLiteAdjustmentTransactionRepository(database),
            )
            prompt = "下午安排半小时日语。"
            content_start = prompt.index("日语")

            class BoundModel(Model):
                def generate(self, _system: str, encoded: str) -> str:
                    item_id = json.loads(encoded)["items"][0]["id"]
                    return json.dumps({"meanings": [{
                        "type": "event", "item_ids": [item_id],
                        "content": [{"item_id": item_id, "start": content_start,
                                     "end": content_start + 2}],
                        "kind": "task", "request": {"type": "add"},
                        "time": {"type": "period", "period": "afternoon"},
                        "duration": {"type": "exact", "minutes": 30},
                    }]}, ensure_ascii=False)

            flow = Flow(Interpreter(BoundModel("")), store, schedule)
            router = V1Router(
                schedule, proposals, reminders=reminders,
                operation_store=store, runtime=runtime, flow=flow,
            )
            now = datetime(2026, 8, 17, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

            _, envelope = router.dispatch("POST", "/api/v1/proposals", {
                "text": prompt,
                "interaction_context": {"current_time": int(now.timestamp() * 1000)},
            })
            proposal = envelope["data"]
            self.assertEqual(proposal["status"], "pending")
            self.assertEqual(proposal["proposed_tasks"][0]["title"], "日语")

            _, accepted_envelope = router.dispatch(
                "POST", f"/api/v1/proposals/{proposal['proposal_id']}/accept"
            )

            self.assertEqual(accepted_envelope["data"]["status"], "accepted")
            self.assertEqual(schedule.list_tasks()[0].title, "日语")
            with self.assertRaises(KeyError):
                proposals.get(proposal["proposal_id"])

    def test_selected_time_range_supplies_missing_event_time(self) -> None:
        with TemporaryDirectory() as temporary:
            database = Path(temporary) / "chronos.sqlite3"
            schedule = ScheduleService(SQLiteScheduleRepository(database))
            store = OperationStore(SQLiteAgentOperationRepository(database))
            proposals = ProposalService(schedule, SQLiteProposalRepository(database))
            flow = Flow(Interpreter(), store, schedule)
            router = V1Router(schedule, proposals, operation_store=store, flow=flow)
            now = int(datetime(2026, 8, 17, 9, 0, tzinfo=UTC).timestamp() * 1000)
            start = int(datetime(2026, 8, 17, 10, 0, tzinfo=UTC).timestamp() * 1000)
            end = start + 30 * 60 * 1000

            _, envelope = router.dispatch("POST", "/api/v1/proposals", {
                "text": "安排30分钟阅读",
                "interaction_context": {
                    "current_time": now,
                    "selection": {"type": "time_range", "start": start, "end": end},
                },
            })

            proposal = envelope["data"]
            self.assertEqual(proposal["status"], "pending")
            operation = store.get(proposal["proposal_id"])
            self.assertEqual(operation.compiled_operations[0].task.start, start)

    def test_any_normalized_gap_blocks_plan_and_operations(self) -> None:
        with TemporaryDirectory() as temporary:
            database = Path(temporary) / "chronos.sqlite3"
            schedule = ScheduleService(SQLiteScheduleRepository(database))
            store = OperationStore(SQLiteAgentOperationRepository(database))
            prompt = "帮我在晚上1点添加任务：找附近邮局"

            class PermissiveModel(Model):
                def generate(self, _system: str, encoded: str) -> str:
                    item_id = json.loads(encoded)["items"][0]["id"]
                    return json.dumps({"meanings": [{
                        "type": "event", "item_ids": [item_id],
                        "content": [{"item_id": item_id, "start": 0, "end": len(prompt)}],
                        "kind": "task",
                        "request": {"type": "add", "fields": ["time"]},
                        "time": {"type": "point"},
                        "duration": {"type": "none"},
                        "recurrence": {"frequency": None},
                    }]}, ensure_ascii=False)

            operation = Flow(
                Interpreter(PermissiveModel("")), store, schedule
            ).submit(
                prompt,
                int(datetime(2026, 8, 31, 21, 0,
                             tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000),
            )

            self.assertEqual(operation.state, OperationState.AWAITING_CLARIFICATION)
            self.assertIsNone(operation.plan)
            self.assertEqual(operation.compiled_operations, ())
            self.assertEqual(
                {question.field.split(":", 1)[1] for question in operation.unresolved_questions},
                {"time", "duration"},
            )

    def test_provider_failure_is_failed_not_clarification(self) -> None:
        with TemporaryDirectory() as temporary:
            database = Path(temporary) / "chronos.sqlite3"
            schedule = ScheduleService(SQLiteScheduleRepository(database))
            store = OperationStore(SQLiteAgentOperationRepository(database))

            class FailingModel(Model):
                def generate(self, _system: str, _encoded: str) -> str:
                    raise RuntimeError("provider timeout")

            operation = Flow(
                Interpreter(FailingModel("")), store, schedule
            ).submit("明天下午安排30分钟阅读", 1_788_181_089_593)

            self.assertEqual(operation.state, OperationState.FAILED)
            self.assertEqual(operation.unresolved_questions, ())
            self.assertIn("provider timeout", operation.failure_reason or "")
            self.assertEqual(store.get(operation.id).state, OperationState.FAILED)
