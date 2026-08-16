import json
from datetime import datetime
from unittest import TestCase
from zoneinfo import ZoneInfo

from chronos.agent.interpreter import Interpreter
from chronos.agent.lowerer import Lowerer
from chronos.agent.models import CreateTaskOperation
from chronos.agent.parser import Parser
from chronos.agent.plan import Window
from chronos.agent.planner import Planner
from chronos.agent.state import State


class StaticModel:
    def __init__(self, response: str) -> None:
        self.response = response

    def generate(self, _system: str, _prompt: str) -> str:
        return self.response


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
