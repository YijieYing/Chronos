import json
from dataclasses import replace
from datetime import datetime
from unittest import TestCase
from zoneinfo import ZoneInfo

from chronos.agent.interpreter import Interpreter
from chronos.agent.lowerer import Lowerer
from chronos.agent.models import CreateTaskOperation
from chronos.agent.meaning import Answer, Span
from chronos.agent.parser import Parser
from chronos.agent.plan import Window
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
