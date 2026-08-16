from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from chronos.agent.interpreter import Interpreter
from chronos.agent.meaning import ResidueStatus
from chronos.agent.parser import Parser
from chronos.agent.residue import Registry
from chronos.infrastructure.sqlite_residue import SQLiteResidueRepository


class BrokenModel:
    def generate(self, _system: str, _prompt: str) -> str:
        return "broken"


class ResidueRegistryTest(TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        path = Path(self.temporary.name) / "chronos.sqlite3"
        self.registry = Registry(SQLiteResidueRepository(path))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_capture_is_idempotent_sanitized_and_exportable(self) -> None:
        text = "联系 me@example.com，token sk-abcdefgh1234"
        item = Parser().parse("prompt-1", text).items[0]
        snapshot = Interpreter(BrokenModel(), version="events-test").interpret((item,))

        self.assertEqual(self.registry.capture("operation-1", snapshot), 1)
        self.assertEqual(self.registry.capture("operation-1", snapshot), 0)
        records = self.registry.list(ResidueStatus.OPEN)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].text, "联系 [email]，token [secret]")
        exported = self.registry.export()
        self.assertEqual(exported[0]["interpreter_version"], "events-test")
        self.assertEqual(exported[0]["status"], "open")

    def test_registry_tracks_the_planners_handling_outcome(self) -> None:
        item = Parser().parse("prompt-2", "无法理解").items[0]
        snapshot = Interpreter(BrokenModel()).interpret((item,))
        self.registry.capture("operation-2", snapshot)
        record = self.registry.list()[0]

        resolved = self.registry.resolve(record.id, ResidueStatus.FAILED)

        self.assertEqual(resolved.status, ResidueStatus.FAILED)
        self.assertEqual(self.registry.list(ResidueStatus.OPEN), [])
