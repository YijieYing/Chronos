"""Provider-switchable semantic parser for natural-language Schedule commands."""

from __future__ import annotations

import json
import re
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from chronos.agent.models import TimelineReference
from chronos.schedule.agent_config import AgentConfig, ProviderConfig
from chronos.schedule.agent_interpretation import (
    AgentInterpretation,
    InterpretedReminder,
    InterpretedTask,
    UnresolvedField,
)
from chronos.schedule.agent_profile import AgentProfileCache
from chronos.schedule.commands import (
    DeterministicScheduleCommandParser,
    ScheduleCommand,
    ScheduleCommandParser,
)
from chronos.schedule.models import Task

TASK_TYPES = {
    "creative",
    "coding",
    "research",
    "communication",
    "execution",
    "meeting",
    "recovery",
}


class TextGenerationProvider(Protocol):
    def generate(self, system: str, prompt: str) -> str: ...


@dataclass(frozen=True, slots=True)
class SemanticParseResult:
    command: ScheduleCommand
    context_used: tuple[dict[str, object], ...]
    parser_mode: str = "semantic"
    warnings: tuple[str, ...] = ()


class SemanticScheduleCommandParser:
    def __init__(
        self,
        provider: TextGenerationProvider,
        *,
        fallback: ScheduleCommandParser | None = None,
        fallback_on_error: bool = True,
        profile_cache: AgentProfileCache | None = None,
        memory_retriever: Callable[[str], list[dict[str, object]]] | None = None,
    ) -> None:
        self._provider = provider
        self._fallback = fallback or DeterministicScheduleCommandParser()
        self._fallback_on_error = fallback_on_error
        self._profile_cache = profile_cache or AgentProfileCache(None)
        self._memory_retriever = memory_retriever or (lambda _query: [])

    def parse(self, text: str, now: datetime, tasks: list[Task]) -> ScheduleCommand:
        return self.parse_with_context(text, now, tasks).command

    def parse_with_context(
        self, text: str, now: datetime, tasks: list[Task]
    ) -> SemanticParseResult:
        context_used: tuple[dict[str, object], ...] = ()
        try:
            profile = self._profile_cache.get().content
            context_used = tuple(self._memory_retriever(text))
            memory = "\n".join(f"- [{item['category']}] {item['content']}" for item in context_used)
            response = self._provider.generate(
                _system_prompt(profile, memory), _prompt(text, now, tasks)
            )
            return SemanticParseResult(
                _command_from_response(response, now, tasks, text), context_used
            )
        except Exception as error:
            if not self._fallback_on_error:
                raise
            warnings.warn(
                f"Agent semantic provider failed; using deterministic parser: {error}",
                RuntimeWarning,
                stacklevel=2,
            )
            return SemanticParseResult(
                self._fallback.parse(text, now, tasks),
                context_used,
                parser_mode="deterministic_fallback",
                warnings=("语义模型解析失败，本提案由本地规则生成；请重点核对标题、时间和周期。",),
            )

    def interpret(self, text: str, now: datetime, tasks: list[Task]) -> AgentInterpretation:
        return self.interpret_context(text, now, tasks, None)

    def interpret_context(
        self,
        text: str,
        now: datetime,
        tasks: list[Task],
        selection: TimelineReference | None,
    ) -> AgentInterpretation:
        profile = self._profile_cache.get().content
        context_used = tuple(self._memory_retriever(text))
        memory = "\n".join(f"- [{item['category']}] {item['content']}" for item in context_used)
        system = _interpretation_system_prompt(profile, memory)
        response = self._provider.generate(system, _prompt(text, now, tasks, selection))
        first = _interpretation_from_response(
            response, now, tasks, text, context_used, selection
        )
        if not first.unresolved:
            return first
        repair_prompt = json.dumps(
            {
                "now": now.isoformat(),
                "request": text,
                "selection": _selection_context(selection),
                "tasks": _task_context(tasks),
                "validation_errors": [
                    {"field": item.field, "error": item.question} for item in first.unresolved
                ],
                "instruction": (
                    "Regenerate the complete interpretation JSON. Fix validation errors by "
                    "copying every *_source character-for-character from request. Do not invent."
                ),
            },
            ensure_ascii=False,
        )
        repaired = self._provider.generate(system, repair_prompt)
        return _interpretation_from_response(
            repaired, now, tasks, text, context_used, selection
        )


def build_command_parser(
    config: AgentConfig,
    memory_retriever: Callable[[str], list[dict[str, object]]] | None = None,
) -> ScheduleCommandParser:
    if config.provider == "deterministic":
        return DeterministicScheduleCommandParser()
    selected = config.selected_provider()
    if selected is None:
        raise ValueError(f"unknown Agent provider: {config.provider}")
    if not selected.api_key or not selected.model or not selected.base_url:
        if config.fallback_on_error and config.fallback_provider == "deterministic":
            return DeterministicScheduleCommandParser()
        raise ValueError(f"Agent provider {selected.name} requires base_url, api_key, and model")
    provider = _provider(selected, config.timeout_seconds)
    fallback = (
        DeterministicScheduleCommandParser()
        if config.fallback_provider == "deterministic"
        else None
    )
    if fallback is None:
        raise ValueError(f"unsupported fallback provider: {config.fallback_provider}")
    return SemanticScheduleCommandParser(
        provider,
        fallback=fallback,
        fallback_on_error=config.fallback_on_error,
        profile_cache=AgentProfileCache(config.profile_path, config.profile_max_chars),
        memory_retriever=memory_retriever,
    )


class _OpenAICompatibleProvider:
    def __init__(self, config: ProviderConfig, timeout: float) -> None:
        self._config = config
        self._timeout = timeout

    def generate(self, system: str, prompt: str) -> str:
        payload: dict[str, object] = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": self._config.max_tokens,
            "temperature": self._config.temperature,
        }
        if self._config.json_mode:
            payload["response_format"] = {"type": "json_object"}
        if self._config.name == "deepseek":
            # Structured extraction does not benefit from spending the output budget on CoT.
            payload["thinking"] = {"type": "disabled"}
        response = _post_json(
            _join_url(self._config.base_url, self._config.endpoint),
            payload,
            {"Authorization": f"Bearer {self._config.api_key}"},
            self._timeout,
        )
        choice = response["choices"][0]
        content = str(choice["message"].get("content") or "")
        if not content.strip():
            reason = choice.get("finish_reason")
            raise RuntimeError(f"provider returned empty content (finish_reason={reason})")
        return content


class _AnthropicProvider:
    def __init__(self, config: ProviderConfig, timeout: float) -> None:
        self._config = config
        self._timeout = timeout

    def generate(self, system: str, prompt: str) -> str:
        response = _post_json(
            _join_url(self._config.base_url, self._config.endpoint),
            {
                "model": self._config.model,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": self._config.max_tokens,
                "temperature": self._config.temperature,
            },
            {
                "x-api-key": self._config.api_key,
                "anthropic-version": self._config.api_version,
            },
            self._timeout,
        )
        blocks = response.get("content", [])
        return "".join(
            str(block.get("text", ""))
            for block in blocks
            if block.get("type") == "text"
        )


class _GeminiProvider:
    def __init__(self, config: ProviderConfig, timeout: float) -> None:
        self._config = config
        self._timeout = timeout

    def generate(self, system: str, prompt: str) -> str:
        endpoint = f"/v1beta/models/{quote(self._config.model, safe='')}:generateContent"
        response = _post_json(
            _join_url(self._config.base_url, endpoint),
            {
                "system_instruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "maxOutputTokens": self._config.max_tokens,
                    "temperature": self._config.temperature,
                    "responseMimeType": "application/json",
                },
            },
            {"x-goog-api-key": self._config.api_key},
            self._timeout,
        )
        parts = response["candidates"][0]["content"]["parts"]
        return "".join(str(part.get("text", "")) for part in parts)


def _provider(config: ProviderConfig, timeout: float) -> TextGenerationProvider:
    if config.adapter == "openai_compatible":
        return _OpenAICompatibleProvider(config, timeout)
    if config.adapter == "anthropic":
        return _AnthropicProvider(config, timeout)
    if config.adapter == "gemini":
        return _GeminiProvider(config, timeout)
    raise ValueError(f"unsupported Agent adapter: {config.adapter}")


def _post_json(
    url: str, payload: dict[str, object], headers: dict[str, str], timeout: float
) -> dict[str, object]:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"provider returned HTTP {error.code}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"provider connection failed: {error.reason}") from error
    if not isinstance(result, dict):
        raise ValueError("provider response must be a JSON object")
    return result


def _join_url(base_url: str, endpoint: str) -> str:
    return f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"


_SYSTEM_PROMPT = """You convert natural-language requests into exactly one Chronos Schedule command.
Return only one JSON object, with no markdown. Never invent a task_id: use an id from the supplied
task list. Never copy the full request into title; title must be a concise task name. Allowed types:
create_task, update_task, delete_task, query_schedule. Dates and times must be ISO 8601 with the
supplied timezone and must preserve times explicitly requested by the user. For create_task return
title, preferred_start, estimated_minutes, task_type, and optional recurrence. Recurrence is either
{"frequency":"daily","until":"YYYY-MM-DD"} or {"frequency":"weekly","weekdays":[0..6],
"until":"YYYY-MM-DD"}, where 0 is Sunday and until is optional and inclusive. For
an exact clock time explicitly supplied by the user, also return "fixed":true; never silently move
an exact-time task. A broad period such as morning or afternoon is not fixed. For
update_task return task_id and only the new preferred_start and/or estimated_minutes. For
delete_task return task_id. For query_schedule return optional task_id and optional query_date
(YYYY-MM-DD). Allowed task_type values: creative, coding, research, communication, execution,
meeting, recovery. If the request contains multiple distinct tasks, do not merge them into one
title."""


_INTERPRETATION_PROMPT = """You are the interpretation layer for Chronos Schedule.
Return only one JSON object. Do not schedule or optimize anything. Extract user intent and preserve
field provenance. Valid intents are create_schedule, create_reminder, replan_schedule, update_task,
delete_task, query_schedule. Reminder intent is for “don't forget / remind me” events that do not
reserve time. For replan_schedule, do not invent task edits: return tasks:[] plus an unresolved
question about the adjustment scope until the Replanner integration is available.

For create_schedule return:
{"intent":"create_schedule","tasks":[{"title":"concise name","title_source":"exact source
phrase","duration_minutes":30,"duration_source":"exact source phrase","preferred_start":"ISO
8601","temporal_source":"exact source phrase","task_type":"execution","recurrence":{"frequency":
"daily","until":"YYYY-MM-DD"},"recurrence_sources":{"frequency":["exact source phrase"],
"until":["exact source phrase"]},"fixed":true}],"unresolved":[],"assumptions":[]}

One user request may produce multiple tasks. Morning and evening occurrences described as separate
activities must be separate tasks. Never copy the entire request into a title. Never invent a time,
duration, weekday, recurrence, or task name. If a required field is absent or ambiguous, use null
and add {"field":"tasks[0].duration_minutes","question":"..."} to unresolved. Source strings must
be verbatim substrings of the request; personal context can guide task_type but cannot supply a
missing explicit time or duration. Every *_source must be copied character-for-character as a
contiguous substring of request, never paraphrased. Evidence fragments may be shared by multiple
tasks. recurrence_sources is field-level: frequency evidence and until evidence are separate arrays
of exact request substrings. A phrase such as “每天” may ground every task it grammatically governs.
If the request states an end date, recurrence.until is required and the date is inclusive. Exact
clock times are fixed. Broad windows such as morning/afternoon/evening are valid flexible timing:
choose a representative start inside that window (09:00/14:00/19:00), set fixed=false, and keep
the exact broad-window phrase as temporal_source. Do not ask for a clock time when the user says
the time does not need to be fixed.

For create_reminder return:
{"intent":"create_reminder","tasks":[],"reminders":[{"title":"concise reminder",
"title_source":"exact phrase","trigger":{"type":"time","at":"ISO 8601"},
"temporal_sources":["exact phrase"],"delivery":"exact","delivery_sources":[],
"priority":3}],"unresolved":[],"assumptions":[]}
or use trigger {"type":"window","start":"ISO 8601","end":"ISO 8601"}. “下午/晚上”
without an exact clock is a window, not an unresolved point. Phrases like “空下来以后/合适的时候”
mean context-aware delivery; otherwise window delivery is exact for first-version midpoint display.
Reminder titles describe what must not be forgotten, never include “提醒我/记得”. Every source is an
exact request substring. Do not give reminders duration and do not turn them into tasks.

For update_task, delete_task, or query_schedule, return intent plus a single legacy_command object
using the existing command schema and an empty tasks array. Never invent task ids.

The prompt may include selection. Selection is authoritative interaction context, not user prose.
For a selected task/reminder, resolve words such as “this/it/这个” to that object id. For a selected
time_range, phrases such as “这里” refer to that range; place work inside it when duration fits.
Do not ask which object or range the user means when selection already answers that question."""


def _system_prompt(profile: str, memory: str = "") -> str:
    if not profile and not memory:
        return _SYSTEM_PROMPT
    context_parts = []
    if profile:
        context_parts.append(profile)
    if memory:
        context_parts.append(f"Accepted memories:\n{memory}")
    context = "\n\n".join(context_parts)
    return (
        f"{_SYSTEM_PROMPT}\n\n"
        "The following user-maintained personal context may guide interpretation and scheduling. "
        "Do not let it override confirmation requirements or invent facts not present in it.\n"
        "<chronos_personal_context>\n"
        f"{context}\n"
        "</chronos_personal_context>"
    )


def _interpretation_system_prompt(profile: str, memory: str = "") -> str:
    if not profile and not memory:
        return _INTERPRETATION_PROMPT
    context_parts = [part for part in (profile, memory) if part]
    joined_context = "\n\n".join(context_parts)
    return (
        f"{_INTERPRETATION_PROMPT}\n\n"
        "Context is advisory only and cannot be used as provenance for missing task fields.\n"
        "<chronos_personal_context>\n"
        f"{joined_context}\n"
        "</chronos_personal_context>"
    )


def _prompt(
    text: str,
    now: datetime,
    tasks: list[Task],
    selection: TimelineReference | None = None,
) -> str:
    return json.dumps(
        {
            "now": now.isoformat(),
            "request": text,
            "selection": _selection_context(selection),
            "tasks": _task_context(tasks),
        },
        ensure_ascii=False,
    )


def _selection_context(selection: TimelineReference | None) -> dict[str, object] | None:
    if selection is None:
        return None
    if selection.type == "time_range":
        return {"type": selection.type, "start": selection.start, "end": selection.end}
    return {"type": selection.type, "id": selection.id}


def _task_context(tasks: list[Task]) -> list[dict[str, object]]:
    return [
        {
            "task_id": task.task_id,
            "title": task.title,
            "preferred_start": task.preferred_start.isoformat() if task.preferred_start else None,
            "estimated_minutes": task.estimated_minutes,
            "task_type": task.task_type,
        }
        for task in tasks
    ]


def _command_from_response(
    response: str, now: datetime, tasks: list[Task], request_text: str
) -> ScheduleCommand:
    cleaned = response.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.I | re.S)
    if fenced:
        cleaned = fenced.group(1)
    payload = json.loads(cleaned)
    if not isinstance(payload, dict):
        raise ValueError("semantic command must be a JSON object")
    command_type = str(payload.get("type", ""))
    if command_type not in {"create_task", "update_task", "delete_task", "query_schedule"}:
        raise ValueError(f"unsupported semantic command type: {command_type}")
    task_id = str(payload["task_id"]) if payload.get("task_id") else None
    known = {task.task_id: task for task in tasks}
    if task_id and task_id not in known:
        raise ValueError("semantic provider returned an unknown task_id")
    if command_type in {"update_task", "delete_task"} and not task_id:
        raise ValueError(f"{command_type} requires task_id")
    preferred_start = _optional_datetime(payload.get("preferred_start"), now)
    estimated = _optional_positive_int(payload.get("estimated_minutes"))
    if command_type == "create_task":
        if len(re.findall(r"(?m)^\s*(?:[-*+]|\d+[.)])\s+\S", request_text)) > 1:
            raise ValueError("Agent 当前一次只能创建一个任务；请逐条提交时间计划")
        title = str(payload.get("title", "")).strip()
        if not title or preferred_start is None or estimated is None:
            raise ValueError("create_task requires title, preferred_start, and estimated_minutes")
        if len(title) > 80 or _normalized_title(title) == _normalized_title(request_text):
            raise ValueError("semantic provider did not return a concise task title")
        task_type = str(payload.get("task_type", "execution"))
        if task_type not in TASK_TYPES:
            raise ValueError(f"unknown task_type: {task_type}")
        intensity = 0.72 if task_type in {"coding", "creative", "research"} else 0.45
        spectrum = {
            "creative": 0.08,
            "coding": 0.28,
            "research": 0.2,
            "communication": 0.65,
            "execution": 0.88,
            "meeting": 0.72,
            "recovery": 0.5,
        }[task_type]
        return ScheduleCommand(
            type="create_task",
            title=title,
            preferred_start=preferred_start,
            estimated_minutes=estimated,
            task_type=task_type,
            cognitive_intensity=intensity,
            spectrum=spectrum,
            recurrence=_optional_recurrence(payload.get("recurrence")),
            fixed=bool(payload.get("fixed", False)) or _has_exact_clock(request_text),
        )
    query_date = (
        date.fromisoformat(str(payload["query_date"])) if payload.get("query_date") else None
    )
    return ScheduleCommand(
        type=command_type,  # type: ignore[arg-type]
        task_id=task_id,
        preferred_start=preferred_start,
        estimated_minutes=estimated,
        query_date=query_date,
    )


def _interpretation_from_response(
    response: str,
    now: datetime,
    tasks: list[Task],
    request_text: str,
    context_used: tuple[dict[str, object], ...],
    selection: TimelineReference | None = None,
) -> AgentInterpretation:
    payload = _json_object(response)
    intent = str(payload.get("intent", ""))
    unresolved_payload = payload.get("unresolved", [])
    if not isinstance(unresolved_payload, list):
        raise ValueError("unresolved must be a list")
    unresolved = [
        UnresolvedField(str(item.get("field", "")), str(item.get("question", "")))
        for item in unresolved_payload
        if isinstance(item, dict) and item.get("field") and item.get("question")
    ]
    assumptions_payload = payload.get("assumptions", [])
    assumptions = (
        tuple(str(item) for item in assumptions_payload if str(item).strip())
        if isinstance(assumptions_payload, list)
        else ()
    )
    if intent != "create_schedule":
        if intent == "create_reminder":
            return _reminder_interpretation(
                payload, now, request_text, context_used, unresolved, assumptions
            )
        if intent == "replan_schedule":
            if not unresolved:
                unresolved.append(UnresolvedField(
                    "replan.scope", "你希望 Chronos 调整今天哪些可变任务？"
                ))
            return AgentInterpretation(
                intent="replan_schedule",
                tasks=(),
                unresolved=tuple(unresolved),
                assumptions=assumptions,
                context_used=context_used,
            )
        legacy = payload.get("legacy_command")
        if not isinstance(legacy, dict):
            raise ValueError("non-create interpretation requires legacy_command")
        legacy = dict(legacy)
        if (
            selection is not None
            and selection.type == "task"
            and intent in {"update_task", "delete_task", "query_schedule"}
        ):
            legacy.setdefault("task_id", selection.id)
        command = _command_from_response(
            json.dumps(legacy, ensure_ascii=False), now, tasks, request_text
        )
        return AgentInterpretation(
            intent="single_command",
            tasks=(),
            unresolved=tuple(unresolved),
            assumptions=assumptions,
            context_used=context_used,
            command=command,
        )
    raw_tasks = payload.get("tasks", [])
    if not isinstance(raw_tasks, list) or not raw_tasks:
        if not unresolved:
            unresolved.append(UnresolvedField("tasks", "请说明希望 Chronos 安排的任务。"))
        return AgentInterpretation(
            intent="create_schedule",
            tasks=(),
            unresolved=tuple(unresolved),
            assumptions=assumptions,
            context_used=context_used,
        )
    interpreted: list[InterpretedTask] = []
    for index, item in enumerate(raw_tasks):
        if not isinstance(item, dict):
            raise ValueError("each interpreted task must be an object")
        title = str(item.get("title") or "").strip()
        title_source = _verified_source(
            item.get("title_source"), request_text, f"tasks[{index}].title", unresolved
        )
        duration_source = _verified_source(
            item.get("duration_source"),
            request_text,
            f"tasks[{index}].duration_minutes",
            unresolved,
            required=False,
        )
        temporal_source = _verified_source(
            item.get("temporal_source"),
            request_text,
            f"tasks[{index}].preferred_start",
            unresolved,
            required=False,
        )
        recurrence_sources = _verified_recurrence_sources(
            item,
            request_text,
            index,
            unresolved,
        )
        duration = _optional_positive_int(item.get("duration_minutes"))
        preferred_start = _optional_datetime(item.get("preferred_start"), now)
        recurrence = _optional_recurrence(item.get("recurrence"))
        if not title or not title_source:
            _add_unresolved(unresolved, f"tasks[{index}].title", "这个任务叫什么？")
        if duration is None or not duration_source:
            _add_unresolved(
                unresolved,
                f"tasks[{index}].duration_minutes",
                f"「{title or '该任务'}」每次需要多久？",
            )
        if preferred_start is None or not temporal_source:
            _add_unresolved(
                unresolved,
                f"tasks[{index}].preferred_start",
                f"「{title or '该任务'}」应在几点开始？",
            )
        if recurrence is not None and not recurrence_sources.get("frequency"):
            _add_unresolved(
                unresolved,
                f"tasks[{index}].recurrence",
                f"「{title or '该任务'}」的重复规则是什么？",
            )
        if (
            recurrence is not None
            and recurrence.get("until") is not None
            and not recurrence_sources.get("until")
        ):
            _add_unresolved(
                unresolved,
                f"tasks[{index}].recurrence.until",
                f"「{title or '该任务'}」的截止日期依据是什么？",
            )
        if recurrence is not None and _requests_until(request_text):
            if recurrence.get("until") is None:
                _add_unresolved(
                    unresolved,
                    f"tasks[{index}].recurrence.until",
                    f"请确认「{title or '该任务'}」重复到哪一天。",
                )
        if recurrence is None and _requests_recurrence(request_text):
            _add_unresolved(
                unresolved,
                f"tasks[{index}].recurrence",
                f"请确认「{title or '该任务'}」的重复规则。",
            )
        task_type = str(item.get("task_type") or "execution")
        if task_type not in TASK_TYPES:
            task_type = "execution"
        interpreted.append(
            InterpretedTask(
                title=title,
                title_source=title_source or "",
                duration_minutes=duration,
                duration_source=duration_source,
                preferred_start=preferred_start,
                temporal_source=temporal_source,
                task_type=task_type,
                recurrence=recurrence,
                recurrence_sources=recurrence_sources,
                fixed=bool(item.get("fixed", False)) and bool(temporal_source),
            )
        )
    return AgentInterpretation(
        intent="create_schedule",
        tasks=tuple(interpreted),
        unresolved=tuple(unresolved),
        assumptions=assumptions,
        context_used=context_used,
    )


def _json_object(response: str) -> dict[str, object]:
    cleaned = response.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.I | re.S)
    if fenced:
        cleaned = fenced.group(1)
    payload = json.loads(cleaned)
    if not isinstance(payload, dict):
        raise ValueError("semantic response must be a JSON object")
    return payload


def _reminder_interpretation(
    payload: dict[str, object],
    now: datetime,
    request_text: str,
    context_used: tuple[dict[str, object], ...],
    unresolved: list[UnresolvedField],
    assumptions: tuple[str, ...],
) -> AgentInterpretation:
    raw = payload.get("reminders", [])
    if not isinstance(raw, list) or not raw:
        _add_unresolved(unresolved, "reminders", "请说明需要提醒什么。")
        return AgentInterpretation(
            intent="create_reminder",
            tasks=(),
            unresolved=tuple(unresolved),
            assumptions=assumptions,
            context_used=context_used,
        )
    reminders: list[InterpretedReminder] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError("each reminder must be an object")
        title = str(item.get("title") or "").strip()
        title_source = _verified_source(
            item.get("title_source"),
            request_text,
            f"reminders[{index}].title",
            unresolved,
        )
        trigger = item.get("trigger")
        if not isinstance(trigger, dict):
            _add_unresolved(
                unresolved, f"reminders[{index}].trigger", "请明确提醒时间。"
            )
            continue
        trigger_type = str(trigger.get("type", ""))
        temporal_sources = _verified_sources(
            item.get("temporal_sources"),
            request_text,
            f"reminders[{index}].trigger",
            unresolved,
        )
        trigger_at = _optional_datetime(trigger.get("at"), now)
        window_start = _optional_datetime(trigger.get("start"), now)
        window_end = _optional_datetime(trigger.get("end"), now)
        if trigger_type == "time" and trigger_at is None:
            _add_unresolved(
                unresolved, f"reminders[{index}].trigger.at", "请明确提醒时刻。"
            )
        if trigger_type == "window" and (window_start is None or window_end is None):
            _add_unresolved(
                unresolved, f"reminders[{index}].trigger.window", "请明确提醒时间范围。"
            )
        if trigger_type not in {"time", "window"}:
            _add_unresolved(
                unresolved, f"reminders[{index}].trigger.type", "提醒是时刻还是时间窗口？"
            )
            continue
        delivery = str(item.get("delivery", "exact"))
        if delivery not in {"exact", "context-aware"}:
            delivery = "exact"
        context_source = _context_aware_source(request_text)
        if context_source:
            delivery = "context-aware"
        delivery_sources = _verified_sources(
            item.get("delivery_sources"),
            request_text,
            f"reminders[{index}].delivery",
            unresolved,
            required=delivery == "context-aware",
        )
        if context_source and not delivery_sources:
            delivery_sources = (context_source,)
        reminders.append(
            InterpretedReminder(
                title=title,
                title_source=title_source or "",
                trigger_type=trigger_type,  # type: ignore[arg-type]
                trigger_at=trigger_at,
                window_start=window_start,
                window_end=window_end,
                temporal_sources=temporal_sources,
                delivery=delivery,  # type: ignore[arg-type]
                delivery_sources=delivery_sources,
                priority=max(1, min(5, int(item.get("priority", 3)))),
            )
        )
    return AgentInterpretation(
        intent="create_reminder",
        tasks=(),
        reminders=tuple(reminders),
        unresolved=tuple(unresolved),
        assumptions=assumptions,
        context_used=context_used,
    )


def _verified_source(
    value: object,
    request_text: str,
    field: str,
    unresolved: list[UnresolvedField],
    *,
    required: bool = True,
) -> str | None:
    source = str(value or "").strip()
    if not source:
        if required:
            _add_unresolved(unresolved, field, f"请明确 {field}。")
        return None
    if source not in request_text:
        _add_unresolved(unresolved, field, f"请明确 {field}；Agent 返回的依据不在原文中。")
        return None
    return source


def _verified_sources(
    value: object,
    request_text: str,
    field: str,
    unresolved: list[UnresolvedField],
    *,
    required: bool = True,
) -> tuple[str, ...]:
    values = value if isinstance(value, list) else ([] if value is None else [value])
    sources = tuple(str(item).strip() for item in values if str(item).strip())
    if required and not sources:
        _add_unresolved(unresolved, field, f"请明确 {field}。")
    if any(source not in request_text for source in sources):
        _add_unresolved(unresolved, field, f"{field} 的依据不在原文中。")
        return ()
    return sources


def _verified_recurrence_sources(
    item: dict[str, object],
    request_text: str,
    index: int,
    unresolved: list[UnresolvedField],
) -> dict[str, tuple[str, ...]]:
    raw = item.get("recurrence_sources")
    if raw is None and item.get("recurrence_source") is not None:
        raw = {"frequency": [item["recurrence_source"]]}
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        _add_unresolved(
            unresolved,
            f"tasks[{index}].recurrence",
            "重复规则的原文依据格式无效。",
        )
        return {}
    verified: dict[str, tuple[str, ...]] = {}
    for field in ("frequency", "weekdays", "until"):
        values = raw.get(field)
        if values is None:
            continue
        candidates = values if isinstance(values, list) else [values]
        sources: list[str] = []
        for value in candidates:
            source = str(value or "").strip()
            if source and source in request_text:
                sources.append(source)
            else:
                _add_unresolved(
                    unresolved,
                    f"tasks[{index}].recurrence.{field}",
                    f"请明确重复规则的 {field}；Agent 返回的依据不在原文中。",
                )
        if sources:
            verified[field] = tuple(sources)
    return verified


def _add_unresolved(unresolved: list[UnresolvedField], field: str, question: str) -> None:
    if not any(item.field == field for item in unresolved):
        unresolved.append(UnresolvedField(field, question))


def _optional_datetime(value: object, now: datetime) -> datetime | None:
    if value in {None, ""}:
        return None
    text = str(value).strip()
    clock = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?", text)
    if clock:
        return now.replace(
            hour=int(clock.group(1)),
            minute=int(clock.group(2)),
            second=int(clock.group(3) or 0),
            microsecond=0,
        )
    parsed = datetime.fromisoformat(text)
    return parsed.replace(tzinfo=now.tzinfo) if parsed.tzinfo is None else parsed


def _optional_positive_int(value: object) -> int | None:
    if value in {None, ""}:
        return None
    parsed = int(value)
    if parsed <= 0:
        raise ValueError("estimated_minutes must be positive")
    return parsed


def _optional_recurrence(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("recurrence must be an object")
    frequency = str(value.get("frequency", ""))
    result: dict[str, object] = {"frequency": frequency}
    if frequency not in {"daily", "weekly"}:
        raise ValueError("recurrence frequency must be daily or weekly")
    if frequency == "weekly":
        weekdays = sorted({int(day) for day in value.get("weekdays", [])})
        if not weekdays or any(day < 0 or day > 6 for day in weekdays):
            raise ValueError("weekly recurrence requires weekdays from 0 to 6")
        result["weekdays"] = weekdays
    if value.get("until") not in {None, ""}:
        result["until"] = date.fromisoformat(str(value["until"])).isoformat()
    return result


def _normalized_title(value: str) -> str:
    return re.sub(r"[^\w\u3400-\u9fff]", "", value.casefold())


def _has_exact_clock(value: str) -> bool:
    return bool(re.search(r"(?:^|\D)(?:[01]?\d|2[0-3])[:：][0-5]\d", value))


def _requests_recurrence(value: str) -> bool:
    return bool(
        re.search(
            r"每天|每日|每晚|每早|每周|daily|every\s+day|weekly|every\s+week",
            value,
            re.I,
        )
    )


def _requests_until(value: str) -> bool:
    return bool(
        re.search(
            r"(?:到|至|截止(?:到)?|直到).{0,16}(?:为止|之前|以前|前|\d)|"
            r"(?:until|through|ending|ends?)\b",
            value,
            re.I,
        )
    )


def _context_aware_source(value: str) -> str | None:
    match = re.search(
        r"空下来以后|有空(?:时|的时候)?|合适的时候|方便的时候|任务结束后|"
        r"(?:when|once)\s+(?:i(?:'m| am)\s+)?(?:free|available)",
        value,
        re.I,
    )
    return match.group(0) if match else None
