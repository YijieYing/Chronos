import json
from dataclasses import replace
from datetime import datetime
from unittest import TestCase
from zoneinfo import ZoneInfo

from chronos.agent.interpreter import Interpreter
from chronos.agent.lowerer import Lowerer
from chronos.agent.models import (
    CreateReminderOperation,
    CreateTaskOperation,
    DeleteReminderOperation,
    MoveTaskOperation,
    ResizeTaskOperation,
    UpdateTaskOperation,
    UpdateReminderOperation,
)
from chronos.agent.meaning import Answer, Object, Recurrence, Reference, Span
from chronos.agent.parser import Parser
from chronos.agent.plan import ReminderDraft, TaskDraft, Window
from chronos.agent.planner import Planner
from chronos.agent.state import State


class StaticModel:
    def __init__(self, response: str) -> None:
        self.response = response

    def generate(self, _system: str, _prompt: str) -> str:
        return self.response


class SequenceModel:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses

    def generate(self, _system: str, _prompt: str) -> str:
        return self.responses.pop(0)


class PipelineTest(TestCase):
    def test_deterministic_interpreter_creates_canonical_recurring_event(self) -> None:
        prompt = "明天下午安排30分钟阅读，每周一三五到9月30日。"
        item = Parser().parse("prompt-local", prompt).items[0]
        now = datetime(2026, 8, 28, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

        snapshot = Interpreter().interpret(
            (item,), now=int(now.timestamp() * 1000), timezone="Asia/Shanghai"
        )
        event = snapshot.events[0]
        operations = Lowerer().lower(Planner().plan(
            snapshot, State(int(now.timestamp() * 1000), "Asia/Shanghai")
        ))

        self.assertEqual(event.request.type.value, "add")
        self.assertEqual(event.recurrence.frequency, "weekly")
        self.assertEqual(event.recurrence.weekdays, (1, 3, 5))
        self.assertEqual(event.recurrence.until, "2026-09-30")
        self.assertIsInstance(operations[0], CreateTaskOperation)
        self.assertEqual(operations[0].task.recurrence.until, "2026-09-30")

    def test_deterministic_interpreter_uses_selection_for_recurrence_edit(self) -> None:
        prompt = "改成每周二四。"
        item = Parser().parse("prompt-local-edit", prompt).items[0]
        now = datetime(2026, 8, 28, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        current = TaskDraft("task-1", "阅读", int(now.timestamp() * 1000) + 3_600_000, 30)

        snapshot = Interpreter().interpret(
            (item,),
            selection=Reference("task", "task-1"),
            objects=(Object("task", "task-1", "阅读"),),
            now=int(now.timestamp() * 1000),
            timezone="Asia/Shanghai",
        )
        operation = Lowerer().lower(Planner().plan(
            snapshot,
            State(int(now.timestamp() * 1000), "Asia/Shanghai"),
            task_ids=("task-1",),
            task_drafts={"task-1": current},
        ))[0]

        self.assertIsInstance(operation, UpdateTaskOperation)
        self.assertEqual(operation.task.title, "阅读")
        self.assertEqual(operation.task.recurrence.weekdays, (2, 4))

    def test_task_recurrence_edit_preserves_current_task_fields(self) -> None:
        prompt = "把阅读改成每周一三五。"
        item = Parser().parse("prompt-recurrence-edit", prompt).items[0]
        response = json.dumps({"meanings": [{
            "type": "event", "item_ids": [item.id],
            "content": [{"item_id": item.id, "start": 1, "end": 3}],
            "kind": "task",
            "request": {
                "type": "edit", "target": {"type": "task", "id": "task-1"},
                "fields": ["recurrence"],
            },
            "time": {"type": "none"},
            "recurrence": {"frequency": "weekly", "weekdays": [0, 2, 4]},
        }]}, ensure_ascii=False)
        snapshot = Interpreter(StaticModel(response)).interpret((item,))
        now = int(datetime(2026, 8, 17, 13, 0,
                           tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000)
        current = TaskDraft(
            "task-1", "阅读", now + 3_600_000, 45, fixed=True, priority=5,
            recurrence=Recurrence("daily"),
        )

        operation = Lowerer().lower(Planner().plan(
            snapshot,
            State(now, "Asia/Shanghai"),
            task_ids=("task-1",),
            task_drafts={"task-1": current},
        ))[0]

        self.assertIsInstance(operation, UpdateTaskOperation)
        self.assertEqual(operation.task.title, "阅读")
        self.assertEqual(operation.task.duration_minutes, 45)
        self.assertTrue(operation.task.fixed)
        self.assertEqual(operation.task.adjustment_policy.priority, 5)
        self.assertEqual(operation.task.recurrence.frequency, "weekly")
        self.assertEqual(operation.task.recurrence.weekdays, (0, 2, 4))

    def test_task_title_edit_lowers_to_full_update(self) -> None:
        prompt = "把任务标题改成阅读。"
        item = Parser().parse("prompt-title-edit", prompt).items[0]
        response = json.dumps({"meanings": [{
            "type": "event", "item_ids": [item.id],
            "content": [{"item_id": item.id, "start": 7, "end": 9}],
            "kind": "task",
            "request": {
                "type": "edit", "target": {"type": "task", "id": "task-1"},
                "fields": ["content"],
            },
            "time": {"type": "none"},
        }]}, ensure_ascii=False)
        snapshot = Interpreter(StaticModel(response)).interpret((item,))
        now = int(datetime(2026, 8, 17, 13, 0,
                           tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000)
        current = TaskDraft("task-1", "旧标题", now + 3_600_000, 30, priority=4)

        operations = Lowerer().lower(
            Planner().plan(
                snapshot,
                State(now, "Asia/Shanghai"),
                task_ids=("task-1",),
                task_drafts={"task-1": current},
            )
        )

        self.assertIsInstance(operations[0], UpdateTaskOperation)
        self.assertEqual(operations[0].task.title, "阅读")
        self.assertEqual(operations[0].task.adjustment_policy.priority, 4)

    def test_reminder_title_edit_lowers_to_full_update(self) -> None:
        prompt = "把提醒改成取快递。"
        item = Parser().parse("prompt-reminder-title", prompt).items[0]
        at = int(datetime(2026, 8, 17, 15, 0,
                          tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000)
        response = json.dumps({"meanings": [{
            "type": "event", "item_ids": [item.id],
            "content": [{"item_id": item.id, "start": 5, "end": 8}],
            "kind": "reminder",
            "request": {
                "type": "edit", "target": {"type": "reminder", "id": "reminder-1"},
                "fields": ["content"],
            },
            "time": {"type": "none"},
        }]}, ensure_ascii=False)
        snapshot = Interpreter(StaticModel(response)).interpret((item,))
        current = ReminderDraft("reminder-1", "旧提醒", "time", at=at, priority=4)

        operations = Lowerer().lower(
            Planner().plan(
                snapshot,
                State(at - 3_600_000, "Asia/Shanghai"),
                reminder_ids=("reminder-1",),
                reminder_drafts={"reminder-1": current},
            )
        )

        self.assertIsInstance(operations[0], UpdateReminderOperation)
        self.assertEqual(operations[0].reminder.title, "取快递")
        self.assertEqual(operations[0].reminder.priority, 4)

    def test_planner_rejects_edit_target_missing_from_known_tasks(self) -> None:
        prompt = "把日语移到下午三点。"
        item = Parser().parse("prompt-missing-target", prompt).items[0]
        start = int(datetime(2026, 8, 17, 15, 0,
                             tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000)
        response = json.dumps({"meanings": [{
            "type": "event", "item_ids": [item.id],
            "content": [{"item_id": item.id, "start": 1, "end": 3}],
            "kind": "task",
            "request": {
                "type": "edit", "target": {"type": "task", "id": "missing"},
                "fields": ["time"],
            },
            "time": {"type": "point", "start": start},
        }]}, ensure_ascii=False)
        snapshot = Interpreter(StaticModel(response)).interpret((item,))
        now = int(datetime(2026, 8, 17, 13, 0,
                           tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000)

        plan = Planner().plan(
            snapshot,
            State(now, "Asia/Shanghai"),
            task_ids=("existing",),
        )
        self.assertEqual(plan.changes, ())
        self.assertEqual(plan.conflicts[0].code, "target_missing")
        self.assertIn("target task missing does not exist", plan.conflicts[0].message)

    def test_task_edit_lowers_to_move_and_resize_operations(self) -> None:
        prompt = "把日语移到下午三点并改成45分钟。"
        item = Parser().parse("prompt-edit", prompt).items[0]
        start = int(datetime(2026, 8, 17, 15, 0,
                             tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000)
        response = json.dumps({"meanings": [{
            "type": "event", "item_ids": [item.id],
            "content": [{"item_id": item.id, "start": 1, "end": 3}],
            "kind": "task",
            "request": {
                "type": "edit", "target": {"type": "task", "id": "task-1"},
                "fields": ["time", "duration"],
            },
            "time": {"type": "point", "start": start},
            "duration": {"type": "exact", "minutes": 45},
        }]}, ensure_ascii=False)
        snapshot = Interpreter(StaticModel(response)).interpret((item,))
        now = int(datetime(2026, 8, 17, 13, 0,
                           tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000)

        plan = Planner().plan(snapshot, State(now, "Asia/Shanghai"))
        operations = Lowerer().lower(plan)

        self.assertEqual(plan.changes[0].target_id, "task-1")
        self.assertEqual(operations, (
            MoveTaskOperation("task-1", start),
            ResizeTaskOperation("task-1", 45),
        ))

    def test_reminder_delete_lowers_to_delete_operation(self) -> None:
        prompt = "删除取快递提醒。"
        item = Parser().parse("prompt-delete", prompt).items[0]
        response = json.dumps({"meanings": [{
            "type": "event", "item_ids": [item.id],
            "content": [{"item_id": item.id, "start": 2, "end": 7}],
            "kind": "reminder",
            "request": {
                "type": "delete",
                "target": {"type": "reminder", "id": "reminder-1"},
            },
            "time": {"type": "none"},
        }]}, ensure_ascii=False)
        snapshot = Interpreter(StaticModel(response)).interpret((item,))
        now = int(datetime(2026, 8, 17, 13, 0,
                           tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000)

        operations = Lowerer().lower(
            Planner().plan(snapshot, State(now, "Asia/Shanghai"))
        )

        self.assertEqual(operations, (DeleteReminderOperation("reminder-1"),))

        conflict_plan = Planner().plan(
            snapshot,
            State(now, "Asia/Shanghai"),
            reminder_ids=("other",),
        )
        self.assertEqual(conflict_plan.changes, ())
        self.assertEqual(conflict_plan.conflicts[0].code, "target_missing")

    def test_flexible_evening_reminder_stays_symbolic_until_planner(self) -> None:
        prompt = "今晚有空的时候提醒我交材料。"
        item = Parser().parse("prompt-reminder", prompt).items[0]
        start = prompt.index("交材料")
        response = json.dumps({"meanings": [{
            "type": "event",
            "item_ids": [item.id],
            "content": [{"item_id": item.id, "start": start, "end": start + 3}],
            "kind": "reminder",
            "request": {"type": "add"},
            "time": {"type": "flexible", "period": "evening"},
        }]}, ensure_ascii=False)
        snapshot = Interpreter(StaticModel(response)).interpret((item,))
        event = snapshot.events[0]
        self.assertIsNone(event.time.start)
        now = datetime(2026, 8, 17, 17, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

        plan = Planner().plan(snapshot, State(int(now.timestamp() * 1000), "Asia/Shanghai"))
        operations = Lowerer().lower(plan)

        self.assertEqual(plan.changes[0].reminder.delivery, "context-aware")
        self.assertEqual(plan.changes[0].reminder.window.start, int(datetime(
            2026, 8, 17, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")
        ).timestamp() * 1000))
        self.assertIsInstance(operations[0], CreateReminderOperation)
        self.assertEqual(operations[0].reminder.trigger_type, "window")
        self.assertEqual(operations[0].reminder.delivery, "context-aware")

    def test_afternoon_japanese_lowers_without_inventing_a_slot_early(self) -> None:
        prompt = "下午安排半小时日语。"
        item = Parser().parse("prompt-1", prompt).items[0]
        content_start = prompt.index("日语")
        model = StaticModel(json.dumps({
            "meanings": [{
                "type": "event",
                "item_ids": [item.id],
                "content": [{
                    "item_id": item.id,
                    "start": content_start,
                    "end": content_start + 2,
                }],
                "kind": "task",
                "request": {"type": "add"},
                "time": {"type": "period", "period": "afternoon"},
                "duration": {"type": "exact", "minutes": 30},
            }]
        }, ensure_ascii=False))
        snapshot = Interpreter(model).interpret((item,))
        self.assertIsNone(snapshot.events[0].time.start)
        now = datetime(2026, 8, 17, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

        plan = Planner().plan(
            snapshot,
            State(int(now.timestamp() * 1000), "Asia/Shanghai"),
        )
        operations = Lowerer().lower(plan)

        self.assertEqual(len(plan.changes), 1)
        self.assertGreaterEqual(plan.changes[0].task.start, int(now.timestamp() * 1000))
        operation = operations[0]
        self.assertIsInstance(operation, CreateTaskOperation)
        self.assertEqual(operation.task.title, "日语")
        self.assertEqual(operation.task.duration_minutes, 30)
        self.assertEqual(operation.task.start, plan.changes[0].task.start)

    def test_planner_skips_occupied_time_and_never_places_prospective_work_in_past(self) -> None:
        prompt = "下午安排半小时日语。"
        item = Parser().parse("prompt-2", prompt).items[0]
        start = prompt.index("日语")
        response = json.dumps({"meanings": [{
            "type": "event",
            "item_ids": [item.id],
            "content": [{"item_id": item.id, "start": start, "end": start + 2}],
            "kind": "task",
            "request": {"type": "add"},
            "time": {"type": "period", "period": "afternoon"},
            "duration": {"type": "exact", "minutes": 30},
        }]}, ensure_ascii=False)
        snapshot = Interpreter(StaticModel(response)).interpret((item,))
        zone = ZoneInfo("Asia/Shanghai")
        now = datetime(2026, 8, 17, 15, 30, tzinfo=zone)
        occupied_end = datetime(2026, 8, 17, 16, 30, tzinfo=zone)

        plan = Planner().plan(
            snapshot,
            State(int(now.timestamp() * 1000), "Asia/Shanghai"),
            (Window(int(now.timestamp() * 1000), int(occupied_end.timestamp() * 1000)),),
        )

        self.assertEqual(plan.changes[0].task.start, int(occupied_end.timestamp() * 1000))

    def test_items_combined_by_clarification_lower_to_one_longer_title(self) -> None:
        prompt = "下午安排A和B"
        parsed = Parser(lambda _text: (Span(0, 5), Span(5, 7))).parse("prompt-3", prompt)
        first, second = parsed.items
        initial = json.dumps({"meanings": [
            {
                "type": "event", "item_ids": [first.id],
                "content": [{"item_id": first.id, "start": 4, "end": 5}],
                "kind": "task", "request": {"type": "add"},
                "time": {"type": "period", "period": "afternoon"},
                "duration": {"type": "exact", "minutes": 60},
            },
            {
                "type": "event", "item_ids": [second.id],
                "content": [{"item_id": second.id, "start": 6, "end": 7}],
                "kind": "task", "request": {"type": "add"},
                "time": {"type": "period", "period": "afternoon"},
                "gaps": [{"item_id": second.id, "field": "duration",
                          "question": "B需要多久？", "reason": "missing"}],
            },
        ]}, ensure_ascii=False)
        combined = json.dumps({"meanings": [{
            "type": "event", "item_ids": [first.id, second.id],
            "content": [
                {"item_id": first.id, "start": 4, "end": 5},
                {"item_id": second.id, "start": 6, "end": 7},
            ],
            "kind": "task", "request": {"type": "add"},
            "time": {"type": "period", "period": "afternoon"},
            "duration": {"type": "exact", "minutes": 60},
        }]}, ensure_ascii=False)
        interpreter = Interpreter(SequenceModel([initial, combined]))
        snapshot = interpreter.interpret(parsed.items)
        snapshot = interpreter.interpret(
            parsed.items,
            replace(snapshot, answers=(
                Answer("answer-1", second.id, "duration", "和A算在一起"),
            )),
        )
        now = datetime(2026, 8, 17, 13, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

        plan = Planner().plan(snapshot, State(int(now.timestamp() * 1000), "Asia/Shanghai"))
        operations = Lowerer().lower(plan)

        self.assertEqual(len(plan.changes), 1)
        self.assertEqual(plan.changes[0].task.title, "A · B")
        self.assertEqual(plan.changes[0].task.duration, 60)
        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0].task.title, "A · B")
