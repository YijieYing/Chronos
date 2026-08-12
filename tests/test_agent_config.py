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


class _SequenceProvider:
    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)
        self.prompts: list[str] = []

    def generate(self, system: str, prompt: str) -> str:
        self.prompts.append(prompt)
        return next(self.responses)


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

    def test_semantic_parser_returns_the_exact_retrieved_context(self) -> None:
        provider = _FakeProvider(
            '{"type":"create_task","title":"写方案",'
            '"preferred_start":"2026-08-04T14:00:00+08:00",'
            '"estimated_minutes":60,"task_type":"creative"}'
        )
        selected = [
            {
                "context_id": "memory-1",
                "source": "chatgpt",
                "category": "scheduling",
                "content": "用户偏好上午进行深度工作",
                "source_ref": "profile.md",
                "score": 0.72,
            }
        ]
        parser = SemanticScheduleCommandParser(provider, memory_retriever=lambda query: selected)

        result = parser.parse_with_context(
            "安排写方案",
            datetime(2026, 8, 3, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            [],
        )

        self.assertEqual(result.context_used[0]["context_id"], "memory-1")
        self.assertIn("用户偏好上午进行深度工作", provider.system_prompts[0])

    def test_semantic_parser_preserves_recurrence(self) -> None:
        provider = _FakeProvider(
            '{"type":"create_task","title":"日语学习",'
            '"preferred_start":"2026-08-03T09:00:00+08:00",'
            '"estimated_minutes":30,"task_type":"research",'
            '"recurrence":{"frequency":"weekly","weekdays":[1,3,5]}}'
        )
        parser = SemanticScheduleCommandParser(provider)

        command = parser.parse(
            "每周一、三、五上午九点学习日语",
            datetime(2026, 8, 3, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            [],
        )

        self.assertEqual(command.recurrence, {"frequency": "weekly", "weekdays": [1, 3, 5]})

    def test_interpretation_repairs_unverified_sources_once(self) -> None:
        invalid = (
            '{"intent":"create_schedule","tasks":[{"title":"晨间计划",'
            '"title_source":"paraphrased","duration_minutes":30,'
            '"duration_source":"30分钟","preferred_start":"2026-08-12T07:30:00+08:00",'
            '"temporal_source":"7:30","task_type":"execution",'
            '"recurrence":{"frequency":"daily"},"recurrence_source":"每天",'
            '"fixed":true}],"unresolved":[],"assumptions":[]}'
        )
        repaired = invalid.replace('"paraphrased"', '"晨间计划"')
        provider = _SequenceProvider([invalid, repaired])
        parser = SemanticScheduleCommandParser(provider)

        result = parser.interpret(
            "每天7:30晨间计划30分钟",
            datetime(2026, 8, 12, 6, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            [],
        )

        self.assertEqual(result.tasks[0].title, "晨间计划")
        self.assertFalse(result.unresolved)
        self.assertEqual(len(provider.prompts), 2)
        self.assertIn("validation_errors", provider.prompts[1])

    def test_interpretation_cannot_drop_an_explicit_recurrence(self) -> None:
        response = (
            '{"intent":"create_schedule","tasks":[{"title":"晨间计划",'
            '"title_source":"晨间计划","duration_minutes":30,'
            '"duration_source":"30分钟","preferred_start":"2026-08-12T07:30:00+08:00",'
            '"temporal_source":"7:30","task_type":"execution",'
            '"recurrence":null,"recurrence_source":null,"fixed":true}],'
            '"unresolved":[],"assumptions":[]}'
        )
        parser = SemanticScheduleCommandParser(_SequenceProvider([response, response]))

        result = parser.interpret(
            "每天7:30晨间计划30分钟",
            datetime(2026, 8, 12, 6, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            [],
        )

        self.assertEqual(result.unresolved[0].field, "tasks[0].recurrence")

    def test_interpretation_accepts_shared_field_level_recurrence_evidence(self) -> None:
        task = (
            '"title":"托福口语练习","title_source":"托福口语练习",'
            '"duration_minutes":90,"duration_source":"七点半到九点",'
            '"preferred_start":"2026-08-12T07:30:00+08:00",'
            '"temporal_source":"七点半到九点","task_type":"research",'
            '"recurrence":{"frequency":"daily","until":"2026-08-18"},'
            '"recurrence_sources":{"frequency":["每天"],'
            '"until":["到8.18为止"]},"fixed":true'
        )
        response = (
            '{"intent":"create_schedule","tasks":[{'
            + task
            + "},{"
            + task.replace("托福口语练习", "作文练习")
            + '}],"unresolved":[],"assumptions":[]}'
        )
        parser = SemanticScheduleCommandParser(_FakeProvider(response))

        result = parser.interpret(
            "到8.18为止每天七点半到九点托福口语练习和作文练习",
            datetime(2026, 8, 12, 6, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            [],
        )

        self.assertFalse(result.unresolved)
        self.assertEqual(result.tasks[1].recurrence["until"], "2026-08-18")
        self.assertEqual(
            result.tasks[1].recurrence_sources["frequency"], ("每天",)
        )

    def test_interpretation_accepts_clock_only_provider_output(self) -> None:
        response = (
            '{"intent":"create_schedule","tasks":[{"title":"晨间计划",'
            '"title_source":"晨间计划","duration_minutes":30,'
            '"duration_source":"30分钟","preferred_start":"07:30:00",'
            '"temporal_source":"七点半","task_type":"execution",'
            '"recurrence":{"frequency":"daily"},'
            '"recurrence_sources":{"frequency":["每天"]},"fixed":true}],'
            '"unresolved":[],"assumptions":[]}'
        )
        parser = SemanticScheduleCommandParser(_FakeProvider(response))

        result = parser.interpret(
            "每天七点半晨间计划30分钟",
            datetime(2026, 8, 12, 6, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            [],
        )

        self.assertEqual(result.tasks[0].preferred_start.isoformat(), "2026-08-12T07:30:00+08:00")

    def test_context_aware_language_overrides_exact_reminder_delivery(self) -> None:
        response = (
            '{"intent":"create_reminder","tasks":[],"reminders":[{'
            '"title":"给老师发邮件","title_source":"给老师发邮件",'
            '"trigger":{"type":"window","start":"2026-08-13T12:00:00+08:00",'
            '"end":"2026-08-13T18:00:00+08:00"},'
            '"temporal_sources":["下午"],"delivery":"exact",'
            '"delivery_sources":[],"priority":3}],"unresolved":[],"assumptions":[]}'
        )
        parser = SemanticScheduleCommandParser(_FakeProvider(response))

        result = parser.interpret(
            "下午我空下来以后提醒我给老师发邮件",
            datetime(2026, 8, 13, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            [],
        )

        self.assertEqual(result.reminders[0].delivery, "context-aware")
        self.assertEqual(result.reminders[0].delivery_sources, ("空下来以后",))

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
