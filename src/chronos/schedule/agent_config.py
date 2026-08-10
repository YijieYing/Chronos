"""Load the single local configuration entry point for Schedule Agent providers."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


DEFAULT_AGENT_CONFIG = Path("config/agent.local.toml")


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    name: str
    adapter: str
    base_url: str
    endpoint: str
    api_key: str
    model: str
    max_tokens: int = 800
    temperature: float = 0.0
    json_mode: bool = True
    api_version: str = "2023-06-01"


@dataclass(frozen=True, slots=True)
class AgentConfig:
    provider: str = "deterministic"
    fallback_provider: str = "deterministic"
    fallback_on_error: bool = True
    timeout_seconds: float = 30
    profile_path: Path | None = None
    profile_max_chars: int = 16_000
    providers: tuple[ProviderConfig, ...] = ()

    def selected_provider(self) -> ProviderConfig | None:
        return next((item for item in self.providers if item.name == self.provider), None)


def load_agent_config(path: str | Path | None = None) -> AgentConfig:
    selected_path = Path(
        path or os.environ.get("CHRONOS_AGENT_CONFIG", DEFAULT_AGENT_CONFIG)
    )
    if not selected_path.is_file():
        return AgentConfig()
    with selected_path.open("rb") as handle:
        payload = tomllib.load(handle)
    agent = payload.get("agent", {})
    provider_tables = payload.get("providers", {})
    if not isinstance(agent, dict) or not isinstance(provider_tables, dict):
        raise ValueError("agent configuration must contain [agent] and [providers] tables")
    providers = tuple(
        _provider_config(name, values)
        for name, values in provider_tables.items()
        if isinstance(values, dict)
    )
    provider = str(agent.get("provider", "deterministic")).strip()
    if provider != "deterministic" and not any(item.name == provider for item in providers):
        raise ValueError(f"unknown Agent provider: {provider}")
    return AgentConfig(
        provider=provider,
        fallback_provider=str(agent.get("fallback_provider", "deterministic")),
        fallback_on_error=bool(agent.get("fallback_on_error", True)),
        timeout_seconds=float(agent.get("timeout_seconds", 30)),
        profile_path=(
            Path(str(agent["profile_path"])) if agent.get("profile_path") else None
        ),
        profile_max_chars=int(agent.get("profile_max_chars", 16_000)),
        providers=providers,
    )


def _provider_config(name: str, values: dict[str, object]) -> ProviderConfig:
    adapter = str(values.get("adapter", "openai_compatible")).strip()
    if adapter not in {"openai_compatible", "anthropic", "gemini"}:
        raise ValueError(f"unsupported Agent adapter: {adapter}")
    default_endpoint = {
        "openai_compatible": "/chat/completions",
        "anthropic": "/v1/messages",
        "gemini": "",
    }[adapter]
    return ProviderConfig(
        name=name,
        adapter=adapter,
        base_url=str(values.get("base_url", "")).rstrip("/"),
        endpoint=str(values.get("endpoint", default_endpoint)),
        api_key=str(values.get("api_key", "")).strip(),
        model=str(values.get("model", "")).strip(),
        max_tokens=int(values.get("max_tokens", 800)),
        temperature=float(values.get("temperature", 0)),
        json_mode=bool(values.get("json_mode", True)),
        api_version=str(values.get("api_version", "2023-06-01")),
    )
