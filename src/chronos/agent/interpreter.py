"""Model-backed Item interpretation into source-grounded Event snapshots."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Protocol
from uuid import NAMESPACE_URL, uuid4, uuid5

from chronos.agent.meaning import (
    Content,
    Directive,
    DirectiveKind,
    Duration,
    DurationKind,
    Event,
    Field,
    Gap,
    GapReason,
    Item,
    Kind,
    Origin,
    Period,
    Precision,
    Provenance,
    Reference,
    Relation,
    RelationKind,
    Request,
    RequestKind,
    Residue,
    ResidueReason,
    Snapshot,
    Span,
    Time,
    TimeKind,
)


class Model(Protocol):
    def generate(self, system: str, prompt: str) -> str: ...


class Interpreter:
    """Interpret every Item without choosing a concrete Schedule slot."""

    def __init__(self, model: Model, *, version: str = "events-v1") -> None:
        if not version.strip():
            raise ValueError("Interpreter version is required")
        self._model = model
        self.version = version

    def interpret(
        self,
        items: tuple[Item, ...],
        previous: Snapshot | None = None,
        *,
        selection: Reference | None = None,
        objects: tuple[Reference, ...] = (),
    ) -> Snapshot:
        if not items:
            raise ValueError("Interpreter requires Items")
        if previous is not None and previous.items != items:
            raise ValueError("clarification must preserve the previous Items")
        snapshot_id = previous.id if previous else str(uuid4())
        version = previous.version + 1 if previous else 1
        try:
            response = self._model.generate(
                _system(),
                _prompt(items, previous, selection, objects),
            )
            meanings = _list(_json(response).get("meanings"), "meanings")
        except Exception:
            meanings = []

        events: list[Event] = []
        directives: list[Directive] = []
        covered: set[str] = set()
        by_id = {item.id: item for item in items}
        for index, raw in enumerate(meanings):
            try:
                value = _dict(raw, f"meanings[{index}]")
                item_ids = tuple(str(item) for item in _list(value.get("item_ids"), "item_ids"))
                if not item_ids or any(item_id not in by_id for item_id in item_ids):
                    raise ValueError("meaning references an unknown Item")
                if str(value.get("type")) == "event":
                    events.append(_event(snapshot_id, index, value, item_ids, by_id, self.version))
                elif str(value.get("type")) == "directive" and len(item_ids) == 1:
                    directives.append(
                        _directive(snapshot_id, index, value, by_id[item_ids[0]], self.version)
                    )
                else:
                    raise ValueError("unsupported meaning type")
                covered.update(item_ids)
            except Exception:
                continue

        for index, item in enumerate(items):
            if item.id in covered:
                continue
            directives.append(_fallback(snapshot_id, len(meanings) + index, item, self.version))

        return Snapshot(
            id=snapshot_id,
            prompt_id=items[0].prompt_id,
            version=version,
            items=items,
            events=tuple(events),
            directives=tuple(directives),
            answers=previous.answers if previous else (),
        )


def _system() -> str:
    return """You are the Chronos Interpreter. Convert Items into semantic Events or Directives.
Return JSON only: {"meanings":[...]}. Use only supplied Item ids and exact source spans.
Do not choose available Schedule slots. Do not turn symbolic periods into timestamps. Do not invent
titles, facts, references, durations, or executable operation types. A timeline Event request is
only add, edit, or delete. Use gaps for understood ambiguity and residue for unsupported source.
Every supplied Item must be represented. Unknown input is a directive with type unknown."""


def _prompt(
    items: tuple[Item, ...],
    previous: Snapshot | None,
    selection: Reference | None,
    objects: tuple[Reference, ...],
) -> str:
    return json.dumps(
        {
            "schema": {
                "meaning": "event | directive",
                "event": {
                    "type": "event",
                    "item_ids": ["item id"],
                    "content": [{"item_id": "item id", "start": 0, "end": 1}],
                    "kind": "task | reminder | state | schedule | unknown",
                    "request": {
                        "type": "add | edit | delete",
                        "target": {"type": "task", "id": "id"},
                        "fields": ["time"],
                    },
                    "time": {
                        "type": "none | period | point | range | flexible | relative | unresolved"
                    },
                    "duration": {"type": "exact | estimate | range"},
                    "references": [],
                    "relations": [],
                    "gaps": [],
                    "residue": [],
                },
                "directive": {
                    "type": "directive",
                    "item_ids": ["exactly one item id"],
                    "kind": "query | explain | replan | inform | unknown",
                    "content": [{"item_id": "item id", "start": 0, "end": 1}],
                    "references": [],
                    "residue": [],
                },
            },
            "items": [
                {
                    "id": item.id,
                    "prompt_id": item.prompt_id,
                    "start": item.span.start,
                    "end": item.span.end,
                    "text": item.text,
                }
                for item in items
            ],
            "selection": _reference_dict(selection) if selection else None,
            "objects": [_reference_dict(item) for item in objects],
            "previous": _previous(previous),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _event(
    snapshot_id: str,
    index: int,
    value: dict[str, object],
    item_ids: tuple[str, ...],
    items: dict[str, Item],
    interpreter_version: str,
) -> Event:
    event_id = str(uuid5(NAMESPACE_URL, f"{snapshot_id}:event:{index}:{','.join(item_ids)}"))
    contents = tuple(
        _content(_dict(item, "content"), items)
        for item in _list(value.get("content"), "content")
    )
    references = tuple(
        _reference(_dict(item, "reference"))
        for item in _list(value.get("references", []), "references")
    )
    relations = tuple(
        _relation(_dict(item, "relation"))
        for item in _list(value.get("relations", []), "relations")
    )
    gaps = tuple(
        _gap(_dict(item, "gap"), event_id, item_ids)
        for item in _list(value.get("gaps", []), "gaps")
    )
    residue = tuple(
        _residue(_dict(item, "residue"), items, interpreter_version)
        for item in _list(value.get("residue", []), "residue")
    )
    return Event(
        id=event_id,
        item_ids=item_ids,
        content=contents,
        kind=Kind(str(value.get("kind"))),
        request=_request(_dict(value.get("request"), "request")),
        time=_time(_dict(value.get("time"), "time")),
        duration=(
            _duration(_dict(value["duration"], "duration"))
            if value.get("duration") is not None
            else None
        ),
        references=references,
        relations=relations,
        gaps=gaps,
        residue=residue,
        provenance=(
            Provenance(Origin.PROMPT, item_ids, tuple(item.span for item in contents)),
        ),
    )


def _directive(
    snapshot_id: str,
    index: int,
    value: dict[str, object],
    item: Item,
    interpreter_version: str,
) -> Directive:
    return Directive(
        id=str(uuid5(NAMESPACE_URL, f"{snapshot_id}:directive:{index}:{item.id}")),
        item_id=item.id,
        type=DirectiveKind(str(value.get("kind"))),
        content=tuple(
            _content(_dict(content, "content"), {item.id: item})
            for content in _list(value.get("content"), "content")
        ),
        references=tuple(
            _reference(_dict(reference, "reference"))
            for reference in _list(value.get("references", []), "references")
        ),
        residue=tuple(
            _residue(_dict(residue, "residue"), {item.id: item}, interpreter_version)
            for residue in _list(value.get("residue", []), "residue")
        ),
    )


def _fallback(snapshot_id: str, index: int, item: Item, version: str) -> Directive:
    return Directive(
        id=str(uuid5(NAMESPACE_URL, f"{snapshot_id}:fallback:{index}:{item.id}")),
        item_id=item.id,
        type=DirectiveKind.UNKNOWN,
        content=(Content(item.id, item.span, item.text),),
        residue=(
            Residue(
                item.id,
                item.span,
                item.text,
                ResidueReason.LOW_CONFIDENCE,
                version,
                "Interpreter could not produce a valid semantic meaning",
            ),
        ),
    )


def _content(value: dict[str, object], items: dict[str, Item]) -> Content:
    item_id = str(value.get("item_id"))
    item = items.get(item_id)
    if item is None:
        raise ValueError("content references an unknown Item")
    span = Span(int(value["start"]), int(value["end"]))
    if span.start < item.span.start or span.end > item.span.end:
        raise ValueError("content span must stay inside its Item")
    return Content(item_id, span, _extract(item, span))


def _extract(item: Item, span: Span) -> str:
    relative_start = span.start - item.span.start
    relative_end = span.end - item.span.start
    return item.text[relative_start:relative_end]


def _request(value: dict[str, object]) -> Request:
    request_type = RequestKind(str(value.get("type")))
    target = (
        _reference(_dict(value["target"], "target"))
        if value.get("target") is not None
        else None
    )
    fields = tuple(Field(str(item)) for item in _list(value.get("fields", []), "fields"))
    return Request(request_type, target, fields)


def _time(value: dict[str, object]) -> Time:
    kind = TimeKind(str(value.get("type")))
    precision = Precision(str(value.get("precision", "exact")))
    period = Period(str(value["period"])) if value.get("period") is not None else None
    return Time(
        kind,
        period=period,
        start=int(value["start"]) if value.get("start") is not None else None,
        end=int(value["end"]) if value.get("end") is not None else None,
        relation_id=str(value["relation_id"]) if value.get("relation_id") else None,
        text=str(value["text"]) if value.get("text") else None,
        precision=precision,
    )


def _duration(value: dict[str, object]) -> Duration:
    return Duration(
        DurationKind(str(value.get("type"))),
        minutes=int(value["minutes"]) if value.get("minutes") is not None else None,
        minimum=int(value["minimum"]) if value.get("minimum") is not None else None,
        maximum=int(value["maximum"]) if value.get("maximum") is not None else None,
    )


def _reference(value: dict[str, object]) -> Reference:
    return Reference(str(value.get("type")), str(value.get("id")))


def _reference_dict(value: Reference) -> dict[str, str]:
    return {"type": value.type, "id": value.id}


def _relation(value: dict[str, object]) -> Relation:
    return Relation(
        str(value.get("id")),
        RelationKind(str(value.get("type"))),
        _reference(_dict(value.get("target"), "relation.target")),
    )


def _gap(value: dict[str, object], event_id: str, item_ids: tuple[str, ...]) -> Gap:
    item_id = str(value.get("item_id"))
    if item_id not in item_ids:
        raise ValueError("gap must anchor an Event Item")
    return Gap(
        item_id=item_id,
        event_id=event_id,
        field=str(value.get("field")),
        question=str(value.get("question")),
        reason=GapReason(str(value.get("reason"))),
        candidates=tuple(str(item) for item in _list(value.get("candidates", []), "candidates")),
    )


def _residue(
    value: dict[str, object],
    items: dict[str, Item],
    interpreter_version: str,
) -> Residue:
    item_id = str(value.get("item_id"))
    content = _content(value, items)
    return Residue(
        item_id=item_id,
        span=content.span,
        text=content.text,
        reason=ResidueReason(str(value.get("reason"))),
        interpreter_version=interpreter_version,
        hint=str(value["hint"]) if value.get("hint") else None,
    )


def _previous(previous: Snapshot | None) -> dict[str, object] | None:
    if previous is None:
        return None
    return {
        "snapshot_id": previous.id,
        "version": previous.version,
        "answers": [
            {
                "id": item.id,
                "item_id": item.item_id,
                "question_id": item.question_id,
                "text": item.text,
            }
            for item in previous.answers
        ],
    }


def _json(response: str) -> dict[str, object]:
    text = response.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1])
    return _dict(json.loads(text), "response")


def _dict(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return {str(key): item for key, item in value.items()}


def _list(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return value
