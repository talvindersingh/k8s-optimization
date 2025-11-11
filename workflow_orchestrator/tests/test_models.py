"""Unit tests for workflow configuration models."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from workflow_orchestrator.models import (
    ConditionalNode,
    ExecuteNode,
    WorkflowConfig,
    load_workflow_config,
)

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "flow-designs"


class WorkflowModelTests(unittest.TestCase):
    """Verify that workflow configs validate correctly."""

    def test_load_loop_with_counters(self) -> None:
        config_path = FIXTURES_DIR / "loop-with-counters.json"
        config = load_workflow_config(config_path)
        self.assertIsInstance(config, WorkflowConfig)
        self.assertEqual(config.name, "eval-transform-loop")
        self.assertEqual(len(config.flow), 4)
        self.assertIsInstance(config.flow[0], ExecuteNode)
        self.assertIsInstance(config.flow[1], ConditionalNode)

    def test_duplicate_node_ids_raise(self) -> None:
        payload = {
            "name": "duplicate-ids",
            "code_type": "ansible",
            "vars": {},
            "flow": [
                {
                    "id": "node-1",
                    "type": "execute",
                    "node": "ansible_nodes.subjective_evaluator_agent",
                    "inputs": {},
                    "outputs": {},
                },
                {
                    "id": "node-1",
                    "type": "execute",
                    "node": "ansible_nodes.ansible_code_optimizer_agent",
                    "inputs": {},
                    "outputs": {},
                },
            ],
        }

        with tempfile.NamedTemporaryFile(mode="w+", suffix=".json", delete=False) as tmp:
            json.dump(payload, tmp)
            tmp.flush()
            temp_path = Path(tmp.name)

        try:
            with self.assertRaises(ValidationError):
                load_workflow_config(temp_path)
        finally:
            temp_path.unlink(missing_ok=True)

    def test_conditional_branch_requires_goto(self) -> None:
        payload = {
            "name": "invalid-conditional",
            "code_type": "ansible",
            "vars": {},
            "flow": [
                {
                    "id": "check",
                    "type": "conditional",
                    "branches": [
                        {
                            "value": "vars.iteration",
                            "condition": {
                                "op": ">=",
                                "compare_to": "{{max_iterations}}",
                            },
                        }
                    ],
                    "else": "other",
                }
            ],
        }

        with tempfile.NamedTemporaryFile(mode="w+", suffix=".json", delete=False) as tmp:
            json.dump(payload, tmp)
            tmp.flush()
            temp_path = Path(tmp.name)

        try:
            with self.assertRaises(ValidationError):
                load_workflow_config(temp_path)
        finally:
            temp_path.unlink(missing_ok=True)

    def test_condition_must_declare_operands(self) -> None:
        payload = {
            "name": "invalid-condition",
            "code_type": "ansible",
            "vars": {},
            "flow": [
                {
                    "id": "check",
                    "type": "conditional",
                    "branches": [
                        {
                            "value": "vars.iteration",
                            "condition": {
                                "op": ">=",
                            },
                            "goto": "END",
                        }
                    ],
                    "else": "loop",
                }
            ],
        }

        with tempfile.NamedTemporaryFile(mode="w+", suffix=".json", delete=False) as tmp:
            json.dump(payload, tmp)
            tmp.flush()
            temp_path = Path(tmp.name)

        try:
            with self.assertRaises(ValidationError):
                load_workflow_config(temp_path)
        finally:
            temp_path.unlink(missing_ok=True)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
