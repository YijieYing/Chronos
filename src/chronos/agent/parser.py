"""Exact-span Prompt segmentation without semantic interpretation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from chronos.agent.meaning import Item, Span


@dataclass(frozen=True, slots=True)
class Boundary:
    span: Span
    question: str

    def __post_init__(self) -> None:
        if not self.question.strip():
            raise ValueError("boundary question is required")


@dataclass(frozen=True, slots=True)
class Parse:
    items: tuple[Item, ...]
    boundary: Boundary | None = None

    def __post_init__(self) -> None:
        if self.boundary is None and not self.items:
            raise ValueError("resolved parse requires at least one item")
        if self.boundary is not None and self.items:
            raise ValueError("ambiguous parse cannot publish partial items")


type Split = Callable[[str], tuple[Span, ...] | Boundary]


class Parser:
    """Validate source offsets returned by a replaceable segmentation strategy.

    The conservative default publishes the whole Prompt as one Item. A future model-backed
    strategy may choose more spans or return one Boundary, but cannot supply rewritten text or
    inferred Event fields through this interface.
    """

    def __init__(self, split: Split | None = None) -> None:
        self._split = split or _whole

    def parse(self, prompt_id: str, prompt: str) -> Parse:
        if not prompt_id or not prompt:
            raise ValueError("prompt id and text are required")
        result = self._split(prompt)
        if isinstance(result, Boundary):
            result.span.extract(prompt)
            return Parse((), result)
        spans = _validate(prompt, result)
        items = tuple(
            Item(
                id=str(uuid4()),
                prompt_id=prompt_id,
                span=span,
                text=span.extract(prompt),
            )
            for span in spans
        )
        return Parse(items)


def _whole(prompt: str) -> tuple[Span, ...]:
    return (Span(0, len(prompt)),)


def _validate(prompt: str, spans: tuple[Span, ...]) -> tuple[Span, ...]:
    if not spans:
        raise ValueError("parser must return at least one span")
    if tuple(sorted(spans, key=lambda item: item.start)) != spans:
        raise ValueError("parser spans must be ordered")
    previous = 0
    for span in spans:
        span.extract(prompt)
        if span.start < previous:
            raise ValueError("parser spans cannot overlap")
        if prompt[previous : span.start].strip():
            raise ValueError("parser spans cannot drop source text")
        previous = span.end
    if prompt[previous:].strip():
        raise ValueError("parser spans cannot drop source text")
    return spans
