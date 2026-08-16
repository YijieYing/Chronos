import ast
from pathlib import Path
from unittest import TestCase

from chronos.agent.meaning import Interpreter, Item, Snapshot


ROOT = Path(__file__).parents[1]


class BoundaryTest(TestCase):
    def test_interpreter_contract_accepts_items_and_returns_a_complete_snapshot(self) -> None:
        annotation = Interpreter.interpret.__annotations__
        self.assertEqual(annotation["items"], "tuple[Item, ...]")
        self.assertEqual(annotation["previous"], "Snapshot | None")
        self.assertEqual(annotation["return"], "Snapshot")
        self.assertIsNotNone(Item)
        self.assertIsNotNone(Snapshot)

    def test_parser_cannot_depend_on_schedule_or_executable_operations(self) -> None:
        tree = ast.parse((ROOT / "src/chronos/agent/parser.py").read_text())
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        self.assertFalse(any(module.startswith("chronos.schedule") for module in imports))
        self.assertNotIn("chronos.agent.models", imports)

    def test_meaning_cannot_depend_on_planner_or_executable_operations(self) -> None:
        tree = ast.parse((ROOT / "src/chronos/agent/meaning.py").read_text())
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        self.assertFalse(any(module.startswith("chronos.schedule") for module in imports))
        self.assertNotIn("chronos.agent.models", imports)
