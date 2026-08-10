"""Provider-switchable semantic parser for natural-language Schedule commands."""

from __future__ import annotations

import json
import re
import warnings
from datetime import date, datetime
from collections.abc import Callable
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from chronos.schedule.agent_config import AgentConfig, ProviderConfig
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


class SemanticScheduleCommandParser:
    def __init__(
        self,
        provider: TextGenerationProvider,
        *,
        fallback: ScheduleCommandParser | None = None,
        fallback_on_error: bool = True,
        profile_cache: AgentProfileCache | None = None,
        memory_context: Callable[[], str] | None = None,
    ) -> None:
        self._provider = provider
        self._fallback = fallback or DeterministicScheduleCommandParser()
        self._fallback_on_error = fallback_on_error
        self._profile_cache = profile_cache or AgentProfileCache(None)
        self._memory_context = memory_context or (lambda: "")

    def parse(self, text: str, now: datetime, tasks: list[Task]) -> ScheduleCommand:
        try:
            profile = self._profile_cache.get().content
            memory = self._memory_context()
            response = self._provider.generate(
                _system_prompt(profile, memory), _prompt(text, now, tasks)
            )
            return _command_from_response(response, now, tasks)
        except Exception as error:
            if not self._fallback_on_error:
                raise
            warnings.warn(
                f"Agent semantic provider failed; using deterministic parser: {error}",
                RuntimeWarning,
                stacklevel=2,
            )
            return self._fallback.parse(text, now, tasks)


def build_command_parser(
    config: AgentConfig, memory_context: Callable[[], str] | None = None
) -> ScheduleCommandParser:
    if config.provider == "deterministic":
        return DeterministicScheduleCommandParser()
    selected = config.selected_provider()
    if selected is None:
        raise ValueError(f"unknown Agent provider: {config.provider}")
    if not selected.api_key or not selected.model or not selected.base_url:
        if config.fallback_on_error and config.fallback_provider == "deterministic":
            return DeterministicScheduleCommandParser()
        raise ValueError(
            f"Agent provider {selected.name} requires base_url, api_key, and model"
        )
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
        memory_context=memory_context,
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
        response = _post_json(
            _join_url(self._config.base_url, self._config.endpoint),
            payload,
            {"Authorization": f"Bearer {self._config.api_key}"},
            self._timeout,
        )
        return str(response["choices"][0]["message"]["content"])


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
        return "".join(str(block.get("text", "")) for block in blocks if block.get("type") == "text")


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


_SYSTEM_PROMPT = """You convert natural-language requests into one Chronos Schedule command.
Return only one JSON object, with no markdown. Never invent a task_id: use an id from the supplied
task list. Allowed types: create_task, update_task, delete_task, query_schedule. Dates and times must
be ISO 8601 with the supplied timezone. For create_task return title, preferred_start,
estimated_minutes, task_type. For update_task return task_id and only the new preferred_start and/or
estimated_minutes. For delete_task return task_id. For query_schedule return optional task_id and
optional query_date (YYYY-MM-DD). Allowed task_type values: creative, coding, research,
communication, execution, meeting, recovery."""


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


def _prompt(text: str, now: datetime, tasks: list[Task]) -> str:
    task_context = [
        {
            "task_id": task.task_id,
            "title": task.title,
            "preferred_start": task.preferred_start.isoformat() if task.preferred_start else None,
            "estimated_minutes": task.estimated_minutes,
            "task_type": task.task_type,
        }
        for task in tasks
    ]
    return json.dumps(
        {"now": now.isoformat(), "request": text, "tasks": task_context},
        ensure_ascii=False,
    )


def _command_from_response(response: str, now: datetime, tasks: list[Task]) -> ScheduleCommand:
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
        title = str(payload.get("title", "")).strip()
        if not title or preferred_start is None or estimated is None:
            raise ValueError("create_task requires title, preferred_start, and estimated_minutes")
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
        )
    query_date = date.fromisoformat(str(payload["query_date"])) if payload.get("query_date") else None
    return ScheduleCommand(
        type=command_type,  # type: ignore[arg-type]
        task_id=task_id,
        preferred_start=preferred_start,
        estimated_minutes=estimated,
        query_date=query_date,
    )


def _optional_datetime(value: object, now: datetime) -> datetime | None:
    if value in {None, ""}:
        return None
    parsed = datetime.fromisoformat(str(value))
    return parsed.replace(tzinfo=now.tzinfo) if parsed.tzinfo is None else parsed


def _optional_positive_int(value: object) -> int | None:
    if value in {None, ""}:
        return None
    parsed = int(value)
    if parsed <= 0:
        raise ValueError("estimated_minutes must be positive")
    return parsed
