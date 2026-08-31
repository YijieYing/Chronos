"""Model-backed Item interpretation into source-grounded Event snapshots."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Protocol
from uuid import NAMESPACE_URL, uuid4, uuid5
from zoneinfo import ZoneInfo

from chronos.agent.meaning import (
    Answer,
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
    Object,
    Origin,
    Period,
    Precision,
    Provenance,
    Recurrence,
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

    def __init__(self, model: Model | None = None, *, version: str = "events-v1") -> None:
        if not version.strip():
            raise ValueError("Interpreter version is required")
        self._model = model
        self.version = version

    def interpret(
        self,
        items: tuple[Item, ...],
        previous: Snapshot | None = None,
        *,
        selection: Reference | Time | None = None,
        objects: tuple[Object, ...] = (),
        now: int | None = None,
        timezone: str = "UTC",
    ) -> Snapshot:
        if not items:
            raise ValueError("Interpreter requires Items")
        if previous is not None and previous.items != items:
            raise ValueError("clarification must preserve the previous Items")
        snapshot_id = previous.id if previous else str(uuid4())
        version = previous.version + 1 if previous else 1
        if self._model is None:
            if now is None:
                raise ValueError("deterministic Interpreter requires current time")
            return _deterministic(
                items, previous, selection, objects, now, timezone, self.version
            )
        response = self._model.generate(
            _system(),
            _prompt(items, previous, selection, objects, now, timezone),
        )
        try:
            meanings = _list(_json(response).get("meanings"), "meanings")
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            meanings = []

        events: list[Event] = []
        directives: list[Directive] = []
        covered: set[str] = set()
        by_id = {item.id: item for item in items}
        for index, raw in enumerate(meanings):
            value = _dict(raw, f"meanings[{index}]")
            item_ids = tuple(
                str(item) for item in _list(value.get("item_ids", []), "item_ids")
                if str(item) in by_id
            )
            if not item_ids:
                item_ids = (items[min(index, len(items) - 1)].id,)
            if str(value.get("type")) == "directive" and len(item_ids) == 1:
                try:
                    directives.append(
                        _directive(snapshot_id, index, value, by_id[item_ids[0]], self.version)
                    )
                except (ValueError, KeyError, TypeError):
                    events.append(_clarification_event(
                        snapshot_id, index, tuple(by_id[item_id] for item_id in item_ids),
                        self.version, "我没有可靠理解这条请求。你希望创建任务、创建提醒，还是修改已有安排？",
                    ))
            else:
                events.append(_normalize_event(
                    snapshot_id, index, value, item_ids, by_id, self.version,
                    now=now, timezone=timezone,
                    answers=previous.answers if previous else (),
                ))
            covered.update(item_ids)

        for index, item in enumerate(items):
            if item.id in covered:
                continue
            events.append(_clarification_event(
                snapshot_id, len(meanings) + index, (item,), self.version,
                "我没有可靠理解这条请求。你希望创建任务、创建提醒，还是修改已有安排？",
            ))

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
    return """You are the language extraction stage inside the Chronos Interpreter.
Return JSON only: {"meanings":[...]}. Extract what the user said without trying to satisfy the
strict canonical Event invariants. Fields may be omitted when absent or uncertain. Preserve raw
content, time, duration, recurrence, and target text. Use supplied Item ids and source spans when
reliable. One Item may produce multiple event blocks. Do not invent values or timestamps. Directives
are only genuine query, explain, replan, or inform requests; uncertainty is not a directive."""


def _prompt(
    items: tuple[Item, ...],
    previous: Snapshot | None,
    selection: Reference | Time | None,
    objects: tuple[Object, ...],
    now: int | None = None,
    timezone: str = "UTC",
) -> str:
    return json.dumps(
        {
            "schema": {
                "meaning": "event | directive",
                "event": {
                    "type": "event",
                    "item_ids": ["item id"],
                    "content": [{"item_id": "item id", "start": 0, "end": 1}],
                    "title": "concise task or reminder name, without command or time wording",
                    "kind": "task | reminder | state | schedule | unknown",
                    "request": {"type": "add | edit | delete", "target_text": "optional", "fields": ["optional"]},
                    "time": {"text": "exact source wording", "type": "optional hint"},
                    "duration": {"text": "exact source wording", "minutes": "optional number"},
                    "recurrence": {"text": "exact source wording"},
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
                    "response": "concise user-facing Chronos reply",
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
            "selection": _selection_dict(selection),
            "current_time": now,
            "timezone": timezone,
            "objects": [
                {"type": item.type, "id": item.id, "title": item.title}
                for item in objects
            ],
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
        recurrence=(
            _recurrence(_dict(value["recurrence"], "recurrence"))
            if value.get("recurrence") is not None
            else None
        ),
        references=references,
        relations=relations,
        gaps=gaps,
        residue=residue,
        provenance=(
            Provenance(Origin.PROMPT, item_ids, tuple(item.span for item in contents)),
        ),
        title=(str(value["title"]).strip() if value.get("title") else None),
    )


def _normalize_event(
    snapshot_id: str,
    index: int,
    value: dict[str, object],
    item_ids: tuple[str, ...],
    items: dict[str, Item],
    interpreter_version: str,
    *,
    now: int | None,
    timezone: str,
    answers: tuple[Answer, ...] = (),
) -> Event:
    """Normalize permissive language extraction into one strict canonical Event."""

    event_id = str(uuid5(NAMESPACE_URL, f"{snapshot_id}:event:{index}:{','.join(item_ids)}"))
    source_items = tuple(items[item_id] for item_id in item_ids)
    source_text = " ".join(item.text for item in source_items)
    contents = _normalized_content(value.get("content"), source_items)
    gaps: list[Gap] = []
    answered = _answered_fields(answers, item_ids)
    resolved_answers: set[str] = set()

    kind_value = str(value.get("kind") or "").lower()
    if kind_value in {Kind.TASK.value, Kind.REMINDER.value}:
        kind = Kind(kind_value)
    else:
        inferred_kind = _kind_from_source(source_text)
        kind = inferred_kind or Kind.UNKNOWN
        if inferred_kind is None:
            gaps.append(Gap(
                item_ids[0], "kind",
                "这是要安排一项占用时间的任务，还是只创建一个提醒？",
                GapReason.AMBIGUOUS, event_id, ("任务", "提醒"),
            ))
    answered_kind = _answered_kind(answered.get("kind"))
    if answered_kind is not None:
        kind = answered_kind
        resolved_answers.add("kind")

    request_value = value.get("request")
    request_raw = request_value if isinstance(request_value, Mapping) else {}
    action = str(request_raw.get("type") or value.get("action") or "").lower()
    if action not in {item.value for item in RequestKind}:
        inferred_request = _request_from_source(source_text)
        action = (inferred_request or RequestKind.ADD).value
        if inferred_request is None:
            gaps.append(Gap(
                item_ids[0], "request", "你希望创建、修改还是删除这项安排？",
                GapReason.AMBIGUOUS, event_id, ("创建", "修改", "删除"),
            ))
    request_kind = RequestKind(action)
    answered_request = _answered_request(answered.get("request"))
    if answered_request is not None:
        request_kind = answered_request
        resolved_answers.add("request")
    target = _normalized_target(request_raw, value, kind, source_text)
    fields = _normalized_fields(request_raw.get("fields", value.get("fields", [])))
    if request_kind == RequestKind.ADD:
        request = Request(RequestKind.ADD)
    elif target is None:
        target_type = kind.value if kind in {Kind.TASK, Kind.REMINDER} else "task"
        target = Reference(target_type, "unresolved")
        gaps.append(Gap(
            item_ids[0], "target", "请选择要操作的时间轴对象。",
            GapReason.MISSING, event_id,
        ))
        request = (
            Request(RequestKind.DELETE, target)
            if request_kind == RequestKind.DELETE
            else Request(RequestKind.EDIT, target, fields or (Field.CONTENT,))
        )
    elif request_kind == RequestKind.DELETE:
        request = Request(RequestKind.DELETE, target)
    else:
        if not fields:
            gaps.append(Gap(
                item_ids[0], "fields", "你希望修改这项安排的什么内容？",
                GapReason.MISSING, event_id,
            ))
            fields = (Field.CONTENT,)
        request = Request(RequestKind.EDIT, target, fields)

    time, time_gap = _normalized_time(value.get("time"), source_text, now, timezone)
    answered_time = _answered_time(answered.get("time"), now, timezone)
    if answered_time is not None:
        time, time_gap = answered_time, None
        resolved_answers.add("time")
    if time_gap is not None:
        gaps.append(Gap(item_ids[0], "time", time_gap, GapReason.AMBIGUOUS, event_id))
    duration, duration_invalid = _normalized_duration(value.get("duration"), source_text)
    if answered.get("duration"):
        answered_duration = _deterministic_duration(answered["duration"])
        if answered_duration is not None:
            duration, duration_invalid = answered_duration, False
            resolved_answers.add("duration")
    recurrence = _normalized_recurrence(value.get("recurrence"), source_text, now, timezone)
    title = _normalized_title(value.get("title"), contents, source_text, kind)
    if kind == Kind.REMINDER:
        duration = None
        duration_invalid = False

    for raw_gap in value.get("gaps", []) if isinstance(value.get("gaps"), list) else []:
        try:
            candidate = _dict(raw_gap, "gap")
            field = str(candidate.get("field") or "meaning")
            if kind == Kind.REMINDER and field == "duration":
                continue
            if field == "duration" and duration is not None:
                continue
            if field == "time" and time.type not in {TimeKind.NONE, TimeKind.UNRESOLVED}:
                continue
            if field in resolved_answers or any(item.field == field for item in gaps):
                continue
            gaps.append(Gap(
                str(candidate.get("item_id") or item_ids[0]), field,
                str(candidate.get("question") or f"请明确 {field}。"),
                GapReason(str(candidate.get("reason") or "ambiguous")), event_id,
                tuple(str(item) for item in candidate.get("candidates", [])
                      if str(item).strip()) if isinstance(candidate.get("candidates"), list) else (),
            ))
        except (ValueError, TypeError):
            continue

    if request_kind == RequestKind.ADD:
        if "time" not in resolved_answers and time.type in {TimeKind.NONE, TimeKind.UNRESOLVED} and not any(
            item.field == "time" for item in gaps
        ):
            question = "希望安排在什么时间？" if kind != Kind.REMINDER else "希望在什么时间提醒？"
            gaps.append(Gap(item_ids[0], "time", question, GapReason.MISSING, event_id))
        if "duration" not in resolved_answers and kind in {Kind.TASK, Kind.UNKNOWN} and duration is None:
            gaps.append(Gap(
                item_ids[0], "duration",
                "这个任务需要多长时间？" if not duration_invalid else "请明确这个任务需要多长时间。",
                GapReason.MISSING, event_id,
            ))

    references = tuple(
        reference for raw in value.get("references", [])
        if isinstance(value.get("references"), list)
        for reference in _safe_reference(raw)
    )
    return Event(
        id=event_id,
        item_ids=item_ids,
        content=contents,
        kind=kind,
        request=request,
        time=time,
        duration=duration,
        recurrence=recurrence,
        references=references,
        gaps=tuple(_unique_gaps([
            gap for gap in gaps
            if gap.field not in resolved_answers
            and not (kind == Kind.REMINDER and gap.field == "duration")
        ])),
        provenance=(Provenance(Origin.PROMPT, item_ids, tuple(item.span for item in contents)),),
        title=title,
    )


def _clarification_event(
    snapshot_id: str,
    index: int,
    items: tuple[Item, ...],
    version: str,
    question: str,
) -> Event:
    event_id = str(uuid5(NAMESPACE_URL, f"{snapshot_id}:event:{index}:{','.join(item.id for item in items)}"))
    contents = tuple(Content(item.id, item.span, item.text) for item in items)
    return Event(
        event_id,
        tuple(item.id for item in items),
        contents,
        Kind.UNKNOWN,
        Request(RequestKind.ADD),
        Time(TimeKind.NONE),
        gaps=(Gap(items[0].id, "kind", question, GapReason.AMBIGUOUS, event_id,
                  ("创建任务", "创建提醒", "修改安排")),),
        residue=tuple(Residue(
            item.id, item.span, item.text, ResidueReason.LOW_CONFIDENCE, version,
            "language extraction did not produce a usable meaning",
        ) for item in items),
        provenance=(Provenance(Origin.PROMPT, tuple(item.id for item in items),
                               tuple(item.span for item in contents)),),
    )


def _normalized_content(raw: object, items: tuple[Item, ...]) -> tuple[Content, ...]:
    by_id = {item.id: item for item in items}
    parsed: dict[str, Content] = {}
    if isinstance(raw, list):
        for value in raw:
            try:
                content = _content(_dict(value, "content"), by_id)
                parsed[content.item_id] = content
            except (ValueError, KeyError, TypeError):
                continue
    result = []
    for item in items:
        content = parsed.get(item.id)
        if content is None or (content.text == item.text and re.search(r"[：:]", item.text)):
            content = _source_content(item)
        result.append(content)
    return tuple(result)


def _source_content(item: Item) -> Content:
    source = item.text
    match = re.search(r"[：:]\s*([^，。,.]+)$", source)
    candidate = match.group(1).strip() if match else source.strip()
    start = source.find(candidate)
    return Content(item.id, Span(item.span.start + start, item.span.start + start + len(candidate)), candidate)


def _normalized_title(
    raw: object,
    contents: tuple[Content, ...],
    source: str,
    kind: Kind,
) -> str:
    title = str(raw or "").strip(" ，,。.")
    if title and not _looks_like_instruction(title):
        return title
    evidence = " · ".join(item.text.strip() for item in contents if item.text.strip())
    if evidence and not _looks_like_instruction(evidence):
        return evidence
    candidate = source.strip(" ，,。.")
    patterns = (
        r"(?:提醒我|别忘(?:了)?)(?:在)?(?:今天|明天)?(?:凌晨|早上|上午|中午|下午|晚上|今晚)?\s*"
        r"(?:\d{1,2}|[零一二两三四五六七八九十]{1,3})(?:[:：]\d{2}|点(?:半|\d{1,2}分)?)?\s*(?:去|要)?(.+)$",
        r"(?:帮我|请)?(?:添加|创建|新建|安排)(?:一个|一项)?(?:任务|提醒)?[：:\s]*(.+)$",
    )
    for pattern in patterns:
        match = re.search(pattern, candidate, re.I)
        if match and match.group(1).strip():
            return match.group(1).strip(" ，,。.")
    return candidate


def _looks_like_instruction(value: str) -> bool:
    return bool(re.search(
        r"提醒我|帮我|请|添加任务|创建任务|新建任务|添加提醒|创建提醒|"
        r"\b(?:remind me|create task|add task|create reminder)\b",
        value,
        re.I,
    ))


def _normalized_target(
    request: Mapping[object, object], value: dict[str, object], kind: Kind, source: str
) -> Reference | None:
    raw = request.get("target") or value.get("target")
    if isinstance(raw, Mapping):
        target_id = str(raw.get("id") or "").strip()
        target_type = str(raw.get("type") or kind.value).strip()
        if target_id and target_id != "None" and target_type in {"task", "reminder"}:
            return Reference(target_type, target_id)
    return None


def _normalized_fields(raw: object) -> tuple[Field, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(dict.fromkeys(
        Field(str(item)) for item in raw if str(item) in {field.value for field in Field}
    ))


def _answered_fields(
    answers: tuple[Answer, ...], item_ids: tuple[str, ...]
) -> dict[str, str]:
    values: dict[str, str] = {}
    for answer in answers:
        if answer.item_id not in item_ids:
            continue
        field = answer.question_id.split(":", 1)[-1]
        values[field] = answer.text.strip()
    return values


def _answered_kind(value: str | None) -> Kind | None:
    if not value:
        return None
    if re.search(r"提醒|reminder", value, re.I):
        return Kind.REMINDER
    if re.search(r"任务|task", value, re.I):
        return Kind.TASK
    return None


def _kind_from_source(value: str) -> Kind | None:
    if re.search(r"提醒(?:我)?|别忘(?:了)?|remind\s+me|reminder", value, re.I):
        return Kind.REMINDER
    if re.search(r"任务|安排|日程|task", value, re.I):
        return Kind.TASK
    return None


def _request_from_source(value: str) -> RequestKind | None:
    if re.search(r"删除|移除|取消(?:任务|安排|提醒)|delete|remove|cancel", value, re.I):
        return RequestKind.DELETE
    if re.search(r"移动|挪到|改到|改成|改为|调整|延长|缩短|编辑|move|edit|update|resize|rename", value, re.I):
        return RequestKind.EDIT
    if re.search(r"提醒(?:我)?|添加|创建|新建|安排|add|create|remind\s+me", value, re.I):
        return RequestKind.ADD
    return None


def _answered_request(value: str | None) -> RequestKind | None:
    if not value:
        return None
    if re.search(r"创建|添加|新建|add|create", value, re.I):
        return RequestKind.ADD
    if re.search(r"修改|编辑|调整|edit|update", value, re.I):
        return RequestKind.EDIT
    if re.search(r"删除|取消|delete|remove", value, re.I):
        return RequestKind.DELETE
    return None


def _answered_time(value: str | None, now: int | None, timezone: str) -> Time | None:
    if not value or now is None:
        return None
    parsed = _deterministic_time(value, now, timezone)
    if parsed.type != TimeKind.NONE:
        return parsed
    zone = ZoneInfo(timezone)
    current = datetime.fromtimestamp(now / 1000, zone)
    chinese_hours = {
        "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
        "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
        "十一": 11, "十二": 12,
    }
    clock = re.search(
        r"(?:(今天|明天)\s*)?(凌晨|早上|上午|中午|下午|晚上)?\s*"
        r"(十二|十一|十|[零一二两三四五六七八九]|\d{1,2})\s*点(?:\s*(半|\d{1,2}\s*分))?",
        value,
    )
    if clock:
        day = current.date() + (timedelta(days=1) if clock.group(1) == "明天" else timedelta())
        raw_hour = clock.group(3)
        hour = chinese_hours.get(raw_hour, int(raw_hour) if raw_hour.isdigit() else -1)
        period = clock.group(2) or ""
        if period in {"下午", "晚上"} and 1 <= hour <= 11:
            hour += 12
        if period == "中午" and hour < 11:
            hour += 12
        minute_text = clock.group(4) or ""
        minute = 30 if minute_text == "半" else int(re.sub(r"\D", "", minute_text) or 0)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            timestamp = datetime.combine(day, datetime.min.time(), zone).replace(
                hour=hour, minute=minute
            )
            return Time(TimeKind.POINT, start=int(timestamp.timestamp() * 1000))
    english = re.search(r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)?\s*(\d{1,2}):([0-5]\d)\b", value, re.I)
    if english:
        timestamp = current.replace(
            hour=int(english.group(1)), minute=int(english.group(2)), second=0, microsecond=0
        )
        if timestamp <= current:
            timestamp += timedelta(days=1)
        return Time(TimeKind.POINT, start=int(timestamp.timestamp() * 1000))
    return None


def _normalized_time(
    raw: object, source: str, now: int | None, timezone: str
) -> tuple[Time, str | None]:
    if isinstance(raw, Mapping):
        try:
            return _time({str(key): value for key, value in raw.items()}), None
        except (ValueError, KeyError, TypeError):
            pass
    text = str(raw.get("text") or "").strip() if isinstance(raw, Mapping) else ""
    text = text or _time_source(source)
    if not text:
        return Time(TimeKind.NONE), None
    if re.search(r"(?:晚上|今晚)\s*[01]?\d\s*点", text):
        return Time(TimeKind.UNRESOLVED, text=text), "“晚上1点”是指明天凌晨 1:00，还是下午 1:00？"
    if now is not None:
        parsed = _deterministic_time(text, now, timezone)
        if parsed.type != TimeKind.NONE:
            return parsed, None
    return Time(TimeKind.UNRESOLVED, text=text), f"请明确“{text}”对应的具体时间。"


def _time_source(source: str) -> str:
    patterns = (
        r"(?:今天|明天)?(?:凌晨|早上|上午|中午|下午|晚上|今晚)\s*\d{1,2}(?:[:：]\d{2}|点(?:半|\d{1,2}分)?)?",
        r"(?:今天|明天)?\s*\d{1,2}[:：]\d{2}",
        r"明天|今天|上午|下午|晚上|今晚|早上",
    )
    for pattern in patterns:
        match = re.search(pattern, source)
        if match:
            return match.group(0).strip()
    return ""


def _normalized_duration(raw: object, source: str) -> tuple[Duration | None, bool]:
    if isinstance(raw, Mapping):
        try:
            return _duration({str(key): value for key, value in raw.items()}), False
        except (ValueError, KeyError, TypeError):
            text = str(raw.get("text") or "")
            parsed = _deterministic_duration(text)
            if parsed is not None:
                return parsed, False
            source_duration = _deterministic_duration(source)
            if source_duration is not None:
                return source_duration, False
            if text or str(raw.get("type") or "") not in {"", "none"}:
                return None, True
    return _deterministic_duration(source), False


def _normalized_recurrence(
    raw: object, source: str, now: int | None, timezone: str
) -> Recurrence | None:
    if isinstance(raw, Mapping):
        frequency = raw.get("frequency")
        if frequency in {"daily", "weekly"}:
            try:
                return _recurrence({str(key): value for key, value in raw.items()})
            except (ValueError, KeyError, TypeError):
                pass
        text = str(raw.get("text") or "")
        if text and now is not None:
            return _deterministic_recurrence(text, now, timezone)
    return _deterministic_recurrence(source, now, timezone) if now is not None else None


def _safe_reference(raw: object) -> tuple[Reference, ...]:
    try:
        return (_reference(_dict(raw, "reference")),)
    except (ValueError, KeyError, TypeError):
        return ()


def _unique_gaps(values: list[Gap]) -> list[Gap]:
    result: list[Gap] = []
    seen: set[str] = set()
    for value in values:
        if value.field not in seen:
            seen.add(value.field)
            result.append(value)
    return result


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
        response=str(value["response"]) if value.get("response") else None,
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
        response=None,
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


def _recurrence(value: dict[str, object]) -> Recurrence:
    return Recurrence(
        str(value.get("frequency")),
        tuple(int(item) for item in _list(value.get("weekdays", []), "weekdays")),
        str(value["until"]) if value.get("until") else None,
    )


def _reference(value: dict[str, object]) -> Reference:
    return Reference(str(value.get("type")), str(value.get("id")))


def _reference_dict(value: Reference) -> dict[str, str]:
    return {"type": value.type, "id": value.id}


def _selection_dict(value: Reference | Time | None) -> dict[str, object] | None:
    if value is None:
        return None
    if isinstance(value, Reference):
        return _reference_dict(value)
    return {
        "type": value.type.value,
        "start": value.start,
        "end": value.end,
        "precision": value.precision.value,
    }


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
    # Clarification recompiles the full semantic working state.  Answers are
    # evidence attached to that state, not a transcript from which the model
    # must guess all previously established meaning again.
    return asdict(previous)


def _deterministic(
    items: tuple[Item, ...],
    previous: Snapshot | None,
    selection: Reference | Time | None,
    objects: tuple[Object, ...],
    now: int,
    timezone: str,
    version: str,
) -> Snapshot:
    snapshot_id = previous.id if previous else str(uuid4())
    snapshot_version = previous.version + 1 if previous else 1
    answers = previous.answers if previous else ()
    events: list[Event] = []
    directives: list[Directive] = []
    for index, item in enumerate(items):
        answer = " ".join(value.text for value in answers if value.item_id == item.id)
        text = f"{item.text} {answer}".strip()
        if re.search(r"查询|查找|看看|有哪些|什么时候|列出|find|show|list|when", text, re.I):
            directives.append(Directive(
                str(uuid5(NAMESPACE_URL, f"{snapshot_id}:directive:{index}:{item.id}")),
                item.id,
                DirectiveKind.QUERY,
                (Content(item.id, item.span, item.text),),
                response="请查看时间轴中的匹配对象。",
            ))
            continue
        events.append(_deterministic_event(
            snapshot_id, index, item, text, selection, objects, now, timezone, version
        ))
    return Snapshot(
        snapshot_id,
        items[0].prompt_id,
        snapshot_version,
        items,
        tuple(events),
        tuple(directives),
        answers,
    )


def _deterministic_event(
    snapshot_id: str,
    index: int,
    item: Item,
    text: str,
    selection: Reference | Time | None,
    objects: tuple[Object, ...],
    now: int,
    timezone: str,
    version: str,
) -> Event:
    event_id = str(uuid5(NAMESPACE_URL, f"{snapshot_id}:event:{index}:{item.id}"))
    reminder = bool(re.search(r"提醒(?:我)?|别忘(?:了)?|remind\s+me|reminder", text, re.I))
    kind = Kind.REMINDER if reminder else Kind.TASK
    delete = bool(re.search(r"删除|移除|取消(?:任务|安排|提醒)|delete|remove|cancel", text, re.I))
    edit = bool(re.search(
        r"移动|挪到|改到|改成|改为|调整到|延长|缩短|标题|move|reschedule|resize|rename",
        text,
        re.I,
    )) and not delete
    target, target_gap = (
        _deterministic_target(
            text, kind,
            selection if isinstance(selection, Reference) else None,
            objects, item, event_id,
        )
        if edit or delete else (None, ())
    )
    fields: list[Field] = []
    if edit:
        if _deterministic_time(text, now, timezone).type != TimeKind.NONE:
            fields.append(Field.TIME)
        if _deterministic_duration(text) is not None:
            fields.append(Field.DURATION)
        if _deterministic_recurrence(text, now, timezone) is not None:
            fields.append(Field.RECURRENCE)
        if re.search(r"标题|改成|rename", text, re.I) and not fields:
            fields.append(Field.CONTENT)
        if not fields:
            fields.append(Field.CONTENT)
    request = (
        Request(RequestKind.DELETE, target)
        if delete and target is not None else
        Request(RequestKind.EDIT, target, tuple(fields))
        if edit and target is not None else
        Request(RequestKind.ADD)
    )
    content = _deterministic_content(item, text, kind, request, target, objects)
    time = _deterministic_time(text, now, timezone)
    if time.type == TimeKind.NONE and isinstance(selection, Time):
        time = selection
    duration = _deterministic_duration(text)
    recurrence = _deterministic_recurrence(text, now, timezone)
    gaps: list[Gap] = list(target_gap)
    if not edit and not delete and kind == Kind.TASK:
        if duration is None:
            gaps.append(Gap(item.id, "duration", "这个任务需要多长时间？", GapReason.MISSING, event_id))
        if time.type == TimeKind.NONE:
            gaps.append(Gap(item.id, "time", "希望安排在什么时间？", GapReason.MISSING, event_id))
    if not edit and not delete and kind == Kind.REMINDER and time.type == TimeKind.NONE:
        gaps.append(Gap(item.id, "time", "希望在什么时间提醒？", GapReason.MISSING, event_id))
    if (edit or delete) and target is None:
        placeholder = Reference(kind.value, "unresolved")
        request = (
            Request(RequestKind.DELETE, placeholder)
            if delete else Request(RequestKind.EDIT, placeholder, tuple(fields))
        )
    return Event(
        event_id,
        (item.id,),
        (content,),
        kind,
        request,
        time,
        duration,
        recurrence,
        gaps=tuple(gaps),
        provenance=(Provenance(Origin.PROMPT, (item.id,), (content.span,)),),
        title=content.text,
    )


def _deterministic_target(
    text: str,
    kind: Kind,
    selection: Reference | None,
    objects: tuple[Object, ...],
    item: Item,
    event_id: str,
) -> tuple[Reference | None, tuple[Gap, ...]]:
    if selection is not None and selection.type == kind.value and selection.id:
        return selection, ()
    matches = [value for value in objects if value.type == kind.value and value.title in text]
    if len(matches) == 1:
        return matches[0].reference, ()
    candidates = tuple(value.title for value in matches or objects if value.type == kind.value)
    question = "请选择要编辑的对象。" if matches else "没有找到要操作的对象，请选择时间轴对象。"
    return None, (Gap(
        item.id, "target", question, GapReason.AMBIGUOUS if matches else GapReason.MISSING,
        event_id, candidates[:4],
    ),)


def _deterministic_content(
    item: Item,
    text: str,
    kind: Kind,
    request: Request,
    target: Reference | None,
    objects: tuple[Object, ...],
) -> Content:
    source = item.text
    candidate = source
    if request.type == RequestKind.EDIT and Field.CONTENT in request.fields:
        match = re.search(r"(?:标题)?(?:改成|改为|rename(?:\s+to)?)\s*([^，。,.]+)", source, re.I)
        if match:
            candidate = match.group(1).strip()
    elif request.type != RequestKind.ADD and target is not None:
        known = next((value.title for value in objects if value.reference == target), None)
        if known and known in source:
            candidate = known
    else:
        candidate = re.sub(
            r"帮我|请|给我|安排|创建|新建|提醒我?|别忘(?:了)?|今天|明天|上午|下午|晚上|早上|中午|每天|每日|每周[一二三四五六日天、,，和及\s]*|工作日|周末|到\s*\d{1,2}[月./-]\d{1,2}日?|\d+(?:\.\d+)?\s*(?:分钟|min|minutes?|小时|hours?|h)|(?:[01]?\d|2[0-3])[:：][0-5]\d",
            " ", source, flags=re.I,
        ).strip(" ，,。.")
    candidate = re.sub(r"\s+", " ", candidate).strip() or source
    start = source.find(candidate)
    if start < 0:
        candidate = source
        start = 0
    span = Span(item.span.start + start, item.span.start + start + len(candidate))
    return Content(item.id, span, candidate)


def _deterministic_duration(text: str) -> Duration | None:
    if re.search(r"半\s*(?:个)?\s*小时", text):
        return Duration(DurationKind.EXACT, minutes=30)
    minutes = re.search(r"(\d+)\s*(?:分钟|min|minutes?)", text, re.I)
    hours = re.search(r"(\d+(?:\.\d+)?)\s*(?:个)?\s*(?:小时|hours?|h)(?:\s*半)?", text, re.I)
    if minutes:
        return Duration(DurationKind.EXACT, minutes=int(minutes.group(1)))
    if hours:
        value = round(float(hours.group(1)) * 60)
        if re.search(r"半\s*$", hours.group(0)):
            value += 30
        return Duration(DurationKind.EXACT, minutes=value)
    return None


def _deterministic_time(text: str, now: int, timezone: str) -> Time:
    zone = ZoneInfo(timezone)
    current = datetime.fromtimestamp(now / 1000, zone)
    day = current.date() + (timedelta(days=1) if re.search(r"明天|tomorrow", text, re.I) else timedelta())
    clock = re.search(r"(?:^|\D)([01]?\d|2[0-3])[:：]([0-5]\d)", text)
    if clock:
        value = datetime.combine(day, datetime.min.time(), zone).replace(
            hour=int(clock.group(1)), minute=int(clock.group(2))
        )
        return Time(TimeKind.POINT, start=int(value.timestamp() * 1000))
    for source, period in (
        (r"上午|早上|morning", Period.MORNING),
        (r"下午|afternoon", Period.AFTERNOON),
        (r"晚上|今晚|evening", Period.EVENING),
    ):
        if re.search(source, text, re.I):
            if day != current.date():
                hours = {
                    Period.MORNING: (6, 12),
                    Period.AFTERNOON: (13, 18),
                    Period.EVENING: (18, 23),
                }[period]
                start = datetime.combine(day, datetime.min.time(), zone).replace(hour=hours[0])
                end = datetime.combine(day, datetime.min.time(), zone).replace(hour=hours[1])
                return Time(
                    TimeKind.RANGE,
                    start=int(start.timestamp() * 1000),
                    end=int(end.timestamp() * 1000),
                )
            return Time(TimeKind.PERIOD, period=period)
    return Time(TimeKind.NONE)


def _deterministic_recurrence(text: str, now: int, timezone: str) -> Recurrence | None:
    until_match = re.search(
        r"(?:到|至|截止(?:到)?|直到)\s*(?:(\d{4})[年./-])?(\d{1,2})[月./-](\d{1,2})日?",
        text,
    )
    current = datetime.fromtimestamp(now / 1000, ZoneInfo(timezone))
    until = None
    if until_match:
        until = f"{int(until_match.group(1) or current.year):04d}-{int(until_match.group(2)):02d}-{int(until_match.group(3)):02d}"
    if re.search(r"每天|每日|daily|every\s+day", text, re.I):
        return Recurrence("daily", until=until)
    if not re.search(r"每周|工作日|周末|weekly|every\s+week", text, re.I):
        return None
    if "工作日" in text:
        weekdays = (1, 2, 3, 4, 5)
    elif "周末" in text:
        weekdays = (0, 6)
    else:
        mapping = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 0, "天": 0}
        match = re.search(r"每周([一二三四五六日天、,，和及\s]+)", text)
        weekdays = tuple(sorted({mapping[value] for value in re.findall(
            r"[一二三四五六日天]", match.group(1) if match else ""
        )}))
        if not weekdays:
            weekdays = ((current.weekday() + 1) % 7,)
    return Recurrence("weekly", weekdays, until)


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
