import ast
import inspect
from dataclasses import fields
from pathlib import Path
from unittest import TestCase

from chronos.agent.interpreter import Interpreter
from chronos.agent.meaning import Item, Snapshot
from chronos.agent.runtime import ChronosRuntime
from chronos.agent.models import ProposalSnapshot


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

    def test_interpreter_cannot_depend_on_schedule_or_executable_operations(self) -> None:
        tree = ast.parse((ROOT / "src/chronos/agent/interpreter.py").read_text())
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        self.assertFalse(any(module.startswith("chronos.schedule") for module in imports))
        self.assertNotIn("chronos.agent.models", imports)

    def test_schedule_releases_plan_as_the_agent_pipeline_name(self) -> None:
        tree = ast.parse((ROOT / "src/chronos/schedule/models.py").read_text())
        classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
        self.assertIn("Agenda", classes)
        self.assertIn("AgendaStatus", classes)
        self.assertNotIn("Plan", classes)
        self.assertNotIn("PlanStatus", classes)

    def test_planner_does_not_import_executable_operations(self) -> None:
        tree = ast.parse((ROOT / "src/chronos/agent/planner.py").read_text())
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        self.assertNotIn("chronos.agent.models", imports)
        self.assertFalse(any(module.startswith("chronos.schedule") for module in imports))

    def test_lowerer_is_the_only_new_layer_that_imports_executable_operations(self) -> None:
        tree = ast.parse((ROOT / "src/chronos/agent/lowerer.py").read_text())
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        self.assertIn("chronos.agent.models", imports)
        self.assertFalse(any(module.startswith("chronos.schedule") for module in imports))

    def test_canonical_runtime_does_not_execute_proposal_payload(self) -> None:
        source = inspect.getsource(ChronosRuntime.execute)
        self.assertNotIn("_proposals", source)
        self.assertNotIn("Planner", source)
        self.assertIn("self._apply", source)

    def test_runtime_and_routes_have_no_legacy_agent_write_path(self) -> None:
        runtime = inspect.getsource(ChronosRuntime)
        routes = (ROOT / "src/chronos/api/routes/v1.py").read_text()
        self.assertNotIn("ProposalService", runtime)
        self.assertNotIn("execute_legacy", runtime)
        self.assertNotIn("revert_legacy", runtime)
        self.assertNotIn("execute_legacy", routes)
        self.assertNotIn("revert_legacy", routes)
        self.assertNotIn("create_from_compiler", routes)

    def test_proposal_is_a_view_reference_not_an_execution_payload(self) -> None:
        names = {item.name for item in fields(ProposalSnapshot)}
        self.assertNotIn("operations", names)
        self.assertIn("plan_id", names)

    def test_flow_does_not_depend_on_legacy_compiler_or_proposal_service(self) -> None:
        tree = ast.parse((ROOT / "src/chronos/agent/flow.py").read_text())
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        self.assertNotIn("chronos.agent.compiler", imports)
        self.assertNotIn("chronos.schedule.proposals", imports)
