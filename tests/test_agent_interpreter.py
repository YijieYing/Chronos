import json
from dataclasses import replace
from unittest import TestCase

from chronos.agent.interpreter import Interpreter
from chronos.agent.meaning import (
    Answer,
    DirectiveKind,
    DurationKind,
    Period,
    RequestKind,
    Span,
    TimeKind,
)
from chronos.agent.parser import Parser


class StaticModel:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def generate(self, system: str, prompt: str) -> str:
        self.calls.append((system, prompt))
        return self.response


class InterpreterTest(TestCase):
    def test_symbolic_task_becomes_an_event_without_a_concrete_slot(self) -> None:
        prompt = "下午安排半小时日语。"
        item = Parser().parse("prompt-1", prompt).items[0]
        start = prompt.index("日语")
        model = StaticModel(json.dumps({
            "meanings": [{
                "type": "event",
                "item_ids": [item.id],
                "content": [{"item_id": item.id, "start": start, "end": start + 2}],
                "kind": "task",
                "request": {"type": "add", "fields": []},
                "time": {"type": "period", "period": "afternoon"},
                "duration": {"type": "exact", "minutes": 30},
            }]
        }, ensure_ascii=False))

        snapshot = Interpreter(model).interpret((item,))

        self.assertEqual(len(snapshot.events), 1)
        event = snapshot.events[0]
        self.assertEqual(event.content[0].text, "日语")
        self.assertEqual(event.request.type, RequestKind.ADD)
        self.assertEqual(event.time.type, TimeKind.PERIOD)
        self.assertEqual(event.time.period, Period.AFTERNOON)
        self.assertEqual(event.duration.type, DurationKind.EXACT)
        self.assertEqual(event.duration.minutes, 30)
        self.assertIsNone(event.time.start)

    def test_invalid_model_output_preserves_item_as_unknown_residue(self) -> None:
        item = Parser().parse("prompt-2", "等我脑子清醒时写报告").items[0]

        snapshot = Interpreter(StaticModel("not json"), version="events-test").interpret(
            (item,)
        )

        self.assertEqual(snapshot.events, ())
        self.assertEqual(snapshot.directives[0].type, DirectiveKind.UNKNOWN)
        residue = snapshot.directives[0].residue[0]
        self.assertEqual(residue.text, item.text)
        self.assertEqual(residue.interpreter_version, "events-test")

    def test_one_bad_meaning_does_not_discard_other_items(self) -> None:
        prompt = "安排A安排B"
        parsed = Parser(lambda _text: (
            Span(0, 3),
            Span(3, 6),
        )).parse("prompt-3", prompt)
        first, second = parsed.items
        response = json.dumps({
            "meanings": [
                {
                    "type": "event",
                    "item_ids": [first.id],
                    "content": [{"item_id": first.id, "start": 2, "end": 3}],
                    "kind": "task",
                    "request": {"type": "add"},
                    "time": {"type": "none"},
                },
                {
                    "type": "event",
                    "item_ids": [second.id],
                    "content": [{"item_id": second.id, "start": 99, "end": 100}],
                    "kind": "task",
                    "request": {"type": "add"},
                    "time": {"type": "none"},
                },
            ]
        }, ensure_ascii=False)

        snapshot = Interpreter(StaticModel(response)).interpret(parsed.items)

        self.assertEqual(len(snapshot.events), 1)
        self.assertEqual(snapshot.events[0].content[0].text, "A")
        self.assertEqual(len(snapshot.directives), 1)
        self.assertEqual(snapshot.directives[0].item_id, second.id)

    def test_clarification_rebuilds_the_same_snapshot_with_history(self) -> None:
        prompt = "安排日语"
        item = Parser().parse("prompt-4", prompt).items[0]
        start = prompt.index("日语")
        response = json.dumps({
            "meanings": [{
                "type": "event",
                "item_ids": [item.id],
                "content": [{"item_id": item.id, "start": start, "end": start + 2}],
                "kind": "task",
                "request": {"type": "add"},
                "time": {"type": "none"},
                "duration": {"type": "exact", "minutes": 30},
            }]
        }, ensure_ascii=False)
        model = StaticModel(response)
        interpreter = Interpreter(model)
        first = interpreter.interpret((item,))
        answered = replace(
            first,
            answers=(Answer("answer-1", item.id, "duration", "30分钟"),),
        )

        second = interpreter.interpret((item,), answered)

        self.assertEqual(second.id, first.id)
        self.assertEqual(second.version, first.version + 1)
        self.assertEqual(second.answers, answered.answers)
        self.assertIn("30分钟", model.calls[-1][1])
