from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from zoneinfo import ZoneInfo

from chronos.schedule.agent_config import AgentConfig, ProviderConfig, load_agent_config
from chronos.schedule.agent_profile import AgentProfileCache
from chronos.schedule.commands import DeterministicScheduleCommandParser
from chronos.schedule.semantic_parser import (
    SemanticScheduleCommandParser,
    build_command_parser,
)


class _FakeProvider:
    def __init__(self, response: str) -> None:
        self.response = response
        self.system_prompts: list[str] = []

    def generate(self, system: str, prompt: str) -> str:
        self.system_prompts.append(system)
        return self.response


class AgentConfigTest(TestCase):
    def test_loads_selected_provider_from_toml(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "agent.toml"
            path.write_text(
                """
[agent]
provider = "deepseek"
timeout_seconds = 12
profile_path = "config/agent.local.md"
profile_max_chars = 9000

[providers.deepseek]
adapter = "openai_compatible"
base_url = "https://api.deepseek.com"
api_key = "test-key"
model = "deepseek-v4-flash"
""",
                encoding="utf-8",
            )
            config = load_agent_config(path)

        self.assertEqual(config.provider, "deepseek")
        self.assertEqual(config.timeout_seconds, 12)
        self.assertEqual(config.selected_provider().adapter, "openai_compatible")
        self.assertEqual(config.selected_provider().model, "deepseek-v4-flash")
        self.assertEqual(config.profile_path, Path("config/agent.local.md"))
        self.assertEqual(config.profile_max_chars, 9000)

    def test_blank_key_uses_deterministic_fallback(self) -> None:
        config = AgentConfig(
            provider="deepseek",
            providers=(
                ProviderConfig(
                    name="deepseek",
                    adapter="openai_compatible",
                    base_url="https://api.deepseek.com",
                    endpoint="/chat/completions",
                    api_key="",
                    model="deepseek-v4-flash",
                ),
            ),
        )

        self.assertIsInstance(build_command_parser(config), DeterministicScheduleCommandParser)

    def test_semantic_json_becomes_a_typed_schedule_command(self) -> None:
        provider = _FakeProvider(
                '{"type":"create_task","title":"写方案",'
                '"preferred_start":"2026-08-04T14:00:00+08:00",'
                '"estimated_minutes":60,"task_type":"creative"}'
        )
        with TemporaryDirectory() as temporary:
            profile = Path(temporary) / "agent.md"
            profile.write_text("## Working style\n- Prefer deep work", encoding="utf-8")
            parser = SemanticScheduleCommandParser(
                provider, profile_cache=AgentProfileCache(profile)
            )
            command = parser.parse(
                "明天下午写方案",
                datetime(2026, 8, 3, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
                [],
            )

        self.assertEqual(command.type, "create_task")
        self.assertEqual(command.title, "写方案")
        self.assertEqual(command.preferred_start.hour, 14)
        self.assertEqual(command.estimated_minutes, 60)
        self.assertIn("Prefer deep work", provider.system_prompts[0])

    def test_profile_is_read_only_when_fingerprint_changes(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "agent.md"
            path.write_text("first", encoding="utf-8")
            cache = AgentProfileCache(path)
            original = Path.read_text
            with patch.object(Path, "read_text", autospec=True, side_effect=original) as read:
                first = cache.get()
                second = cache.get()
                self.assertEqual(read.call_count, 1)
                self.assertIs(first, second)
                path.write_text("second version", encoding="utf-8")
                third = cache.get()
                self.assertEqual(read.call_count, 2)
                self.assertNotEqual(first.content_hash, third.content_hash)
