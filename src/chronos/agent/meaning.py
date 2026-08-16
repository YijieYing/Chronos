"""Source-grounded meaning passed from Interpreter to Planner."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class Span:
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 0 or self.end <= self.start:
            raise ValueError("span must have positive length and a non-negative start")

    def extract(self, source: str) -> str:
        if self.end > len(source):
            raise ValueError("span exceeds its source")
        return source[self.start : self.end]


@dataclass(frozen=True, slots=True)
class Item:
    id: str
    prompt_id: str
    span: Span
    text: str

    def __post_init__(self) -> None:
        if not self.id or not self.prompt_id or not self.text:
            raise ValueError("item id, prompt id, and source text are required")


@dataclass(frozen=True, slots=True)
class Content:
    item_id: str
    span: Span
    text: str

    def __post_init__(self) -> None:
        if not self.item_id or not self.text:
            raise ValueError("content requires an item and source text")


class Kind(StrEnum):
    TASK = "task"
    REMINDER = "reminder"
    STATE = "state"
    SCHEDULE = "schedule"
    UNKNOWN = "unknown"


class RequestKind(StrEnum):
    ADD = "add"
    EDIT = "edit"
    DELETE = "delete"


class Field(StrEnum):
    CONTENT = "content"
    TIME = "time"
    DURATION = "duration"
    KIND = "kind"
    RELATIONS = "relations"
    POLICY = "policy"
    RECURRENCE = "recurrence"
    PRIORITY = "priority"


@dataclass(frozen=True, slots=True)
class Reference:
    type: str
    id: str

    def __post_init__(self) -> None:
        if not self.type or not self.id:
            raise ValueError("reference type and id are required")


@dataclass(frozen=True, slots=True)
class Request:
    type: RequestKind
    target: Reference | None = None
    fields: tuple[Field, ...] = ()

    def __post_init__(self) -> None:
        if len(set(self.fields)) != len(self.fields):
            raise ValueError("request fields must be unique")
        if self.type == RequestKind.ADD:
            if self.target is not None or self.fields:
                raise ValueError("add request cannot have a target or edit fields")
        elif self.type == RequestKind.EDIT:
            if self.target is None or not self.fields:
                raise ValueError("edit request requires a target and changed fields")
        elif self.target is None or self.fields:
            raise ValueError("delete request requires only a target")


class Period(StrEnum):
    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"


class Precision(StrEnum):
    EXACT = "exact"
    APPROXIMATE = "approximate"


class TimeKind(StrEnum):
    NONE = "none"
    PERIOD = "period"
    POINT = "point"
    RANGE = "range"
    FLEXIBLE = "flexible"
    RELATIVE = "relative"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class Time:
    type: TimeKind
    period: Period | None = None
    start: int | None = None
    end: int | None = None
    relation_id: str | None = None
    text: str | None = None
    precision: Precision = Precision.EXACT

    def __post_init__(self) -> None:
        supplied = {
            "period": self.period is not None,
            "start": self.start is not None,
            "end": self.end is not None,
            "relation": self.relation_id is not None,
            "text": self.text is not None,
        }
        if self.type == TimeKind.NONE and any(supplied.values()):
            raise ValueError("time none cannot carry a value")
        if self.type == TimeKind.PERIOD and (
            self.period is None
            or any(supplied[key] for key in ("start", "end", "relation", "text"))
        ):
            raise ValueError("period time requires only a symbolic period")
        if self.type == TimeKind.POINT and (
            self.start is None
            or any(supplied[key] for key in ("period", "end", "relation", "text"))
        ):
            raise ValueError("point time requires only a timestamp")
        if self.type == TimeKind.RANGE and (
            self.start is None
            or self.end is None
            or self.end <= self.start
            or any(supplied[key] for key in ("period", "relation", "text"))
        ):
            raise ValueError("range time requires an ordered start and end")
        if self.type == TimeKind.FLEXIBLE and any(
            supplied[key] for key in ("start", "end", "relation", "text")
        ):
            raise ValueError("flexible time may carry only an optional period")
        if self.type == TimeKind.RELATIVE and (
            self.relation_id is None
            or any(supplied[key] for key in ("period", "start", "end", "text"))
        ):
            raise ValueError("relative time requires only a relation")
        if self.type == TimeKind.UNRESOLVED and (
            not self.text
            or any(supplied[key] for key in ("period", "start", "end", "relation"))
        ):
            raise ValueError("unresolved time requires only source text")


class DurationKind(StrEnum):
    EXACT = "exact"
    ESTIMATE = "estimate"
    RANGE = "range"


@dataclass(frozen=True, slots=True)
class Duration:
    type: DurationKind
    minutes: int | None = None
    minimum: int | None = None
    maximum: int | None = None

    def __post_init__(self) -> None:
        if self.type in {DurationKind.EXACT, DurationKind.ESTIMATE}:
            if (
                self.minutes is None
                or self.minutes <= 0
                or self.minimum is not None
                or self.maximum is not None
            ):
                raise ValueError("exact or estimated duration requires positive minutes")
            return
        if (
            self.minutes is not None
            or self.minimum is None
            or self.maximum is None
            or self.minimum <= 0
            or self.maximum < self.minimum
        ):
            raise ValueError("duration range requires ordered positive bounds")


class RelationKind(StrEnum):
    INCLUDES = "includes"
    PART_OF = "part_of"
    SAME_BLOCK = "same_block"
    BEFORE = "before"
    AFTER = "after"
    DEPENDS_ON = "depends_on"


@dataclass(frozen=True, slots=True)
class Relation:
    id: str
    type: RelationKind
    target: Reference

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("relation id is required")


class GapReason(StrEnum):
    AMBIGUOUS = "ambiguous"
    MISSING = "missing"
    CONFLICTING = "conflicting"


@dataclass(frozen=True, slots=True)
class Gap:
    item_id: str
    field: str
    question: str
    reason: GapReason
    event_id: str | None = None
    candidates: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.item_id or not self.field or not self.question.strip():
            raise ValueError("gap requires an item, field, and question")
        if any(not item.strip() for item in self.candidates):
            raise ValueError("gap candidates cannot be blank")
        if len(set(self.candidates)) != len(self.candidates):
            raise ValueError("gap candidates must be unique")


class ResidueReason(StrEnum):
    UNSUPPORTED = "unsupported"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED_REFERENCE = "unresolved_reference"
    LOW_CONFIDENCE = "low_confidence"


class ResidueStatus(StrEnum):
    OPEN = "open"
    ASSUMED = "assumed"
    CLARIFIED = "clarified"
    FAILED = "failed"
    RESOLVED = "resolved"


@dataclass(frozen=True, slots=True)
class Residue:
    item_id: str
    span: Span
    text: str
    reason: ResidueReason
    interpreter_version: str
    hint: str | None = None
    status: ResidueStatus = ResidueStatus.OPEN

    def __post_init__(self) -> None:
        if not self.item_id or not self.text or not self.interpreter_version:
            raise ValueError("residue requires an item, source text, and Interpreter version")


class Origin(StrEnum):
    PROMPT = "prompt"
    CLARIFICATION = "clarification"
    SELECTION = "selection"
    PROFILE = "profile"
    TIMELINE = "timeline"
    MONITOR = "monitor"
    PLANNING = "planning"


@dataclass(frozen=True, slots=True)
class Provenance:
    source: Origin
    item_ids: tuple[str, ...] = ()
    evidence: tuple[Span, ...] = ()

    def __post_init__(self) -> None:
        if len(set(self.item_ids)) != len(self.item_ids):
            raise ValueError("provenance item ids must be unique")
        if self.source in {Origin.PROMPT, Origin.CLARIFICATION} and not self.item_ids:
            raise ValueError("source-grounded provenance requires an item")


@dataclass(frozen=True, slots=True)
class Event:
    id: str
    item_ids: tuple[str, ...]
    content: tuple[Content, ...]
    kind: Kind
    request: Request
    time: Time
    duration: Duration | None = None
    references: tuple[Reference, ...] = ()
    relations: tuple[Relation, ...] = ()
    gaps: tuple[Gap, ...] = ()
    residue: tuple[Residue, ...] = ()
    provenance: tuple[Provenance, ...] = ()

    def __post_init__(self) -> None:
        if not self.id or not self.item_ids or not self.content:
            raise ValueError("event id, source items, and content are required")
        if len(set(self.item_ids)) != len(self.item_ids):
            raise ValueError("event item ids must be unique")
        source_ids = set(self.item_ids)
        if any(item.item_id not in source_ids for item in self.content):
            raise ValueError("event content must come from its source items")
        if {item.item_id for item in self.content} != source_ids:
            raise ValueError("event content must represent every source item")
        if any(item.item_id not in source_ids for item in (*self.gaps, *self.residue)):
            raise ValueError("event gaps and residue must anchor a source item")
        relation_ids = {item.id for item in self.relations}
        if len(relation_ids) != len(self.relations):
            raise ValueError("event relation ids must be unique")
        if self.time.type == TimeKind.RELATIVE and self.time.relation_id not in relation_ids:
            raise ValueError("relative time must reference an event relation")


class DirectiveKind(StrEnum):
    QUERY = "query"
    EXPLAIN = "explain"
    REPLAN = "replan"
    INFORM = "inform"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Directive:
    id: str
    item_id: str
    type: DirectiveKind
    content: tuple[Content, ...]
    references: tuple[Reference, ...] = ()
    residue: tuple[Residue, ...] = ()
    response: str | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.item_id or not self.content:
            raise ValueError("directive id, item, and content are required")
        if any(item.item_id != self.item_id for item in self.content):
            raise ValueError("directive content must come from its item")
        if self.type != DirectiveKind.UNKNOWN and not (self.response or "").strip():
            raise ValueError("understood directive requires a user-facing response")


@dataclass(frozen=True, slots=True)
class Answer:
    id: str
    item_id: str
    question_id: str
    text: str

    def __post_init__(self) -> None:
        if not self.id or not self.item_id or not self.question_id or not self.text.strip():
            raise ValueError("answer id, item, question, and text are required")


@dataclass(frozen=True, slots=True)
class Snapshot:
    id: str
    prompt_id: str
    version: int
    items: tuple[Item, ...]
    events: tuple[Event, ...]
    directives: tuple[Directive, ...] = ()
    answers: tuple[Answer, ...] = ()

    def __post_init__(self) -> None:
        if not self.id or not self.prompt_id or self.version <= 0 or not self.items:
            raise ValueError("snapshot id, prompt id, positive version, and items are required")
        item_ids = {item.id for item in self.items}
        if len(item_ids) != len(self.items):
            raise ValueError("snapshot item ids must be unique")
        if any(item.prompt_id != self.prompt_id for item in self.items):
            raise ValueError("snapshot items must belong to its prompt")
        covered = {
            item_id
            for event in self.events
            for item_id in event.item_ids
        } | {directive.item_id for directive in self.directives}
        if covered != item_ids:
            raise ValueError("snapshot must represent every item exactly within its prompt")
        if any(item.item_id not in item_ids for item in self.answers):
            raise ValueError("snapshot answers must anchor one of its items")
        event_ids = {event.id for event in self.events}
        if len(event_ids) != len(self.events):
            raise ValueError("snapshot event ids must be unique")
        directive_ids = {directive.id for directive in self.directives}
        if len(directive_ids) != len(self.directives):
            raise ValueError("snapshot directive ids must be unique")
