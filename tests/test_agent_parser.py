from unittest import TestCase

from chronos.agent.meaning import Span
from chronos.agent.parser import Boundary, Parser


class ParserTest(TestCase):
    def test_default_parser_preserves_the_complete_prompt_as_one_exact_item(self) -> None:
        prompt = "下午安排半小时日语。"

        result = Parser().parse("prompt-1", prompt)

        self.assertIsNone(result.boundary)
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0].text, prompt)
        self.assertEqual(result.items[0].span.extract(prompt), prompt)

    def test_parser_can_only_publish_exact_source_spans(self) -> None:
        prompt = "明天安排 A，晚上安排 B。"
        split = lambda _text: (Span(0, 7), Span(7, len(prompt)))

        result = Parser(split).parse("prompt-2", prompt)

        self.assertEqual("".join(item.text for item in result.items), prompt)
        self.assertEqual(
            tuple(item.span.extract(prompt) for item in result.items),
            tuple(item.text for item in result.items),
        )

    def test_parser_rejects_dropped_or_overlapping_source(self) -> None:
        prompt = "安排 A 和 B"
        with self.assertRaisesRegex(ValueError, "drop"):
            Parser(lambda _text: (Span(3, len(prompt)),)).parse("prompt-3", prompt)
        with self.assertRaisesRegex(ValueError, "overlap"):
            Parser(lambda _text: (Span(0, 5), Span(4, len(prompt)))).parse(
                "prompt-3", prompt
            )

    def test_parser_boundary_question_does_not_publish_partial_items(self) -> None:
        prompt = "下午安排 A 和 B"
        boundary = Boundary(Span(5, len(prompt)), "A 和 B 是一件事还是两件事？")

        result = Parser(lambda _text: boundary).parse("prompt-4", prompt)

        self.assertEqual(result.items, ())
        self.assertEqual(result.boundary, boundary)
