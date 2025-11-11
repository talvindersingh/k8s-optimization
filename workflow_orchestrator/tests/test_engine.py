"""Integration tests for workflow engine execution."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from workflow_orchestrator.engine import (
    WorkflowExecutionError,
    execute_workflow,
    run_workflow_from_files,
)
from workflow_orchestrator.models import WorkflowConfig

TEST_ROOT = Path(__file__).resolve().parent
DATA_DIR = TEST_ROOT / "data"


class EngineExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_execute_stub_workflow(self) -> None:
        config = WorkflowConfig.model_validate(json.loads((DATA_DIR / "workflow_stubs.json").read_text()))
        context: dict[str, object] = json.loads((DATA_DIR / "optimization_flow_stub.json").read_text())

        await execute_workflow(config, context)

        self.assertEqual(context["vars"]["iteration"], 2)
        runs = context["optimization_flow"]["runs"]
        self.assertIn("0", runs)
        self.assertIn("1", runs)

    async def test_run_workflow_from_files(self) -> None:
        config_path = DATA_DIR / "workflow_stubs.json"
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "flow.json"
            store_path.write_text((DATA_DIR / "optimization_flow_stub.json").read_text(), encoding="utf-8")

            await run_workflow_from_files(config_path, store_path)

            updated = json.loads(store_path.read_text())
            self.assertEqual(updated["vars"]["iteration"], 2)

    async def test_unknown_goto_raises(self) -> None:
        config = WorkflowConfig.model_validate(
            {
                "name": "bad",
                "code_type": "test",
                "vars": {},
                "flow": [
                    {
                        "id": "check",
                        "type": "conditional",
                        "branches": [
                            {
                                "condition": {"python": "True"},
                                "goto": "missing",
                            }
                        ],
                        "else": "END",
                    }
                ],
            }
        )
        with self.assertRaises(WorkflowExecutionError):
            await execute_workflow(config, {"optimization_flow": {}})

    async def test_skip_if_output_present_short_circuits(self) -> None:
        config = WorkflowConfig.model_validate(
            {
                "name": "skip-flow",
                "code_type": "test",
                "vars": {},
                "flow": [
                    {
                        "id": "already-done",
                        "type": "execute",
                        "node": "workflow_orchestrator.tests.stub_nodes.SimpleEvaluator",
                        "skipIfOutputPresent": True,
                        "inputs": {"value": "completed"},
                        "outputs": {"result": "optimization_flow.subjective"},
                    },
                    {
                        "id": "increment",
                        "type": "execute",
                        "node": "workflow_orchestrator.tests.stub_nodes.increment_iteration",
                        "inputs": {},
                        "outputs": {"result": "vars.iteration"},
                    },
                ],
            }
        )
        context = {
            "instruction": "x",
            "original_code": "y",
            "original_manifest": "apiVersion: v1",
            "vars": {"iteration": 0},
            "optimization_flow": {
                "subjective": {
                    "scores": {},
                    "created_at": "2025-01-01T00:00:00Z",
                }
            },
        }
        await execute_workflow(config, context)
        self.assertEqual(context["vars"]["iteration"], 1)

    async def test_vars_preserved_across_skipped_runs(self) -> None:
        config = WorkflowConfig.model_validate(
            {
                "name": "preserve-vars",
                "code_type": "test",
                "vars": {"latest_manifest_key": "original_manifest"},
                "flow": [
                    {
                        "id": "produce",
                        "type": "execute",
                        "node": "workflow_orchestrator.tests.stub_nodes.CodeProducer",
                        "skipIfOutputPresent": True,
                        "inputs": {"code": "new-code"},
                        "outputs": {
                            "result": "optimization_flow.improved.code",
                            "vars.latest_manifest_key": "optimization_flow.improved.code",
                        },
                    }
                ],
            }
        )
        context = {
            "instruction": "x",
            "original_code": "y",
            "original_manifest": "apiVersion: v1",
            "vars": {},
            "optimization_flow": {},
        }
        await execute_workflow(config, context)
        self.assertEqual(context["vars"]["latest_manifest_key"], "optimization_flow.improved.code")

        # Re-run with populated context to trigger skip
        await execute_workflow(config, context)
        self.assertEqual(context["vars"]["latest_manifest_key"], "optimization_flow.improved.code")
