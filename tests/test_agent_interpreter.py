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


class SequenceModel:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.prompts: list[str] = []

    def generate(self, _system: str, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.responses.pop(0)


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

    def test_invalid_model_output_becomes_clarification_event(self) -> None:
        item = Parser().parse("prompt-2", "等我脑子清醒时写报告").items[0]

        snapshot = Interpreter(StaticModel("not json"), version="events-test").interpret(
            (item,)
        )

        self.assertEqual(snapshot.directives, ())
        self.assertEqual(snapshot.events[0].kind.value, "unknown")
        self.assertEqual(snapshot.events[0].gaps[0].field, "kind")
        residue = snapshot.events[0].residue[0]
        self.assertEqual(residue.text, item.text)
        self.assertEqual(residue.interpreter_version, "events-test")

    def test_directive_carries_a_loggable_reply_without_operations(self) -> None:
        prompt = "解释一下现在的安排"
        item = Parser().parse("prompt-directive", prompt).items[0]
        response = json.dumps({"meanings": [{
            "type": "directive",
            "item_ids": [item.id],
            "kind": "explain",
            "content": [{"item_id": item.id, "start": 0, "end": len(prompt)}],
            "response": "今天下午有一项日语安排。",
        }]}, ensure_ascii=False)

        snapshot = Interpreter(StaticModel(response)).interpret((item,))

        self.assertEqual(snapshot.events, ())
        self.assertEqual(snapshot.directives[0].response, "今天下午有一项日语安排。")

    def test_permissive_task_output_normalizes_invalid_fields_into_gaps(self) -> None:
        prompt = "帮我在晚上1点添加任务：找附近邮局"
        item = Parser().parse("prompt-permissive", prompt).items[0]
        response = json.dumps({"meanings": [{
            "type": "event",
            "item_ids": [item.id],
            "content": [{"item_id": item.id, "start": 0, "end": len(prompt)}],
            "kind": "task",
            "title": "找附近邮局",
            "request": {
                "type": "add",
                "target": {"type": "task", "id": None},
                "fields": ["time"],
            },
            "time": {"type": "point"},
            "duration": {"type": "none"},
            "recurrence": {"frequency": None, "weekdays": [], "until": None},
        }]}, ensure_ascii=False)

        snapshot = Interpreter(StaticModel(response)).interpret(
            (item,), now=1_788_181_089_593, timezone="Asia/Shanghai"
        )

        event = snapshot.events[0]
        self.assertEqual(event.request.type, RequestKind.ADD)
        self.assertIsNone(event.request.target)
        self.assertEqual(event.content[0].text, "找附近邮局")
        self.assertEqual(event.title, "找附近邮局")
        self.assertEqual(event.time.type, TimeKind.UNRESOLVED)
        self.assertEqual({gap.field for gap in event.gaps}, {"time", "duration"})
        self.assertIsNone(event.recurrence)

    def test_latest_answer_closes_its_field_even_when_model_reopens_it(self) -> None:
        prompt = "提醒我晚上1点去找邮局"
        item = Parser().parse("prompt-answer-overlay", prompt).items[0]
        response = json.dumps({"meanings": [{
            "type": "event", "item_ids": [item.id],
            "content": [{"item_id": item.id, "start": 0, "end": len(prompt)}],
            "kind": "reminder",
            "time": {"type": "none"},
            "gaps": [{
                "item_id": item.id, "field": "duration",
                "question": "这个任务要多久？", "reason": "missing",
            }],
        }]}, ensure_ascii=False)
        interpreter = Interpreter(StaticModel(response))
        now = 1_788_184_800_000
        first = interpreter.interpret((item,), now=now, timezone="Asia/Shanghai")
        answered = replace(first, answers=(
            Answer("answer-time", item.id, f"{item.id}:time", "明天凌晨一点"),
        ))

        second = interpreter.interpret(
            (item,), answered, now=now, timezone="Asia/Shanghai"
        )

        self.assertEqual(second.events[0].time.type, TimeKind.POINT)
        self.assertEqual(second.events[0].request.type, RequestKind.ADD)
        self.assertEqual(second.events[0].title, "找邮局")
        self.assertIsNone(second.events[0].duration)
        self.assertEqual(second.events[0].gaps, ())

    def test_chinese_half_hour_source_and_answer_close_duration_gap(self) -> None:
        prompt = "每天早上九点安排读半小时日语，先持续一周"
        item = Parser().parse("prompt-half-hour", prompt).items[0]
        response = json.dumps({"meanings": [{
            "type": "event", "item_ids": [item.id],
            "kind": "task", "title": "读日语", "request": {"type": "add"},
            "time": {"type": "period", "period": "morning"},
            "duration": {"type": "none"},
            "gaps": [{
                "item_id": item.id, "field": "duration",
                "question": "请明确这个任务需要多长时间。", "reason": "missing",
            }],
        }]}, ensure_ascii=False)
        interpreter = Interpreter(StaticModel(response))

        first = interpreter.interpret((item,))
        self.assertEqual(first.events[0].duration.minutes, 30)
        self.assertEqual(first.events[0].gaps, ())

        answered = replace(first, answers=(
            Answer("answer-duration", item.id, f"{item.id}:duration", "半小时"),
        ))
        second = interpreter.interpret((item,), answered)
        self.assertEqual(second.events[0].duration.minutes, 30)
        self.assertEqual(second.events[0].gaps, ())

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

        self.assertEqual(len(snapshot.events), 2)
        self.assertEqual(snapshot.events[0].content[0].text, "A")
        self.assertEqual(snapshot.events[1].content[0].text, "安排B")
        self.assertEqual(
            {gap.field for gap in snapshot.events[1].gaps}, {"time", "duration"}
        )

    def test_one_incomplete_event_keeps_all_events_but_exposes_its_gap(self) -> None:
        prompt = "下午写报告并整理资料"
        item = Parser().parse("prompt-multi", prompt).items[0]
        first = prompt.index("写报告")
        second = prompt.index("整理资料")
        response = json.dumps({"meanings": [
            {
                "type": "event", "item_ids": [item.id],
                "content": [{"item_id": item.id, "start": first, "end": first + 3}],
                "kind": "task", "request": {"type": "add"},
                "time": {"type": "period", "period": "afternoon"},
                "duration": {"type": "exact", "minutes": 30},
            },
            {
                "type": "event", "item_ids": [item.id],
                "content": [{"item_id": item.id, "start": second, "end": second + 4}],
                "kind": "task", "request": {"type": "add"},
                "time": {"type": "period", "period": "afternoon"},
            },
        ]}, ensure_ascii=False)

        snapshot = Interpreter(StaticModel(response)).interpret((item,))

        self.assertEqual([event.content[0].text for event in snapshot.events], ["写报告", "整理资料"])
        self.assertEqual(snapshot.events[0].gaps, ())
        self.assertEqual([gap.field for gap in snapshot.events[1].gaps], ["duration"])

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
        previous = json.loads(model.calls[-1][1])["previous"]
        self.assertEqual(previous["events"][0]["duration"]["minutes"], 30)

    def test_combined_answer_replaces_two_items_with_one_semantic_event(self) -> None:
        prompt = "下午安排A和B"
        parsed = Parser(lambda _text: (Span(0, 5), Span(5, 7))).parse(
            "prompt-combined", prompt
        )
        first, second = parsed.items
        initial = json.dumps({"meanings": [
            {
                "type": "event",
                "item_ids": [first.id],
                "content": [{"item_id": first.id, "start": 4, "end": 5}],
                "kind": "task",
                "request": {"type": "add"},
                "time": {"type": "period", "period": "afternoon"},
                "duration": {"type": "exact", "minutes": 60},
            },
            {
                "type": "event",
                "item_ids": [second.id],
                "content": [{"item_id": second.id, "start": 6, "end": 7}],
                "kind": "task",
                "request": {"type": "add"},
                "time": {"type": "period", "period": "afternoon"},
                "gaps": [{
                    "item_id": second.id,
                    "field": "duration",
                    "question": "B需要多久？",
                    "reason": "missing",
                }],
            },
        ]}, ensure_ascii=False)
        combined = json.dumps({"meanings": [{
            "type": "event",
            "item_ids": [first.id, second.id],
            "content": [
                {"item_id": first.id, "start": 4, "end": 5},
                {"item_id": second.id, "start": 6, "end": 7},
            ],
            "kind": "task",
            "request": {"type": "add"},
            "time": {"type": "period", "period": "afternoon"},
            "duration": {"type": "exact", "minutes": 60},
        }]}, ensure_ascii=False)
        model = SequenceModel([initial, combined])
        interpreter = Interpreter(model)
        first_snapshot = interpreter.interpret(parsed.items)
        answered = replace(
            first_snapshot,
            answers=(Answer("answer-combined", second.id, "duration", "和A算在一起"),),
        )

        snapshot = interpreter.interpret(parsed.items, answered)

        self.assertEqual(len(snapshot.events), 1)
        self.assertEqual([item.text for item in snapshot.events[0].content], ["A", "B"])
        self.assertEqual(snapshot.events[0].duration.minutes, 60)
        self.assertEqual(snapshot.events[0].gaps, ())
        previous = json.loads(model.prompts[-1])["previous"]
        self.assertEqual(len(previous["events"]), 2)
        self.assertEqual(previous["answers"][0]["text"], "和A算在一起")
