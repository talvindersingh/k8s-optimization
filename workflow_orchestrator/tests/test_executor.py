"""Tests for the execute node runner."""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone

from workflow_orchestrator.executor import NodeExecutionError, execute_node
from workflow_orchestrator.models import ExecuteNode
from workflow_orchestrator.store import resolve_path


class ExecuteNodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vars = {
            "latest_manifest_key": "original_manifest",
            "latest_code_key": "original_manifest",
            "subjective_iteration_count": 0,
        }
        self.context: dict[str, object] = {
            "instruction": "Do something",
            "original_code": "print('hello')",
            "original_manifest": "apiVersion: v1",
            "optimization_flow": {},
        }

    def run_async(self, coro):
        return asyncio.run(coro)

    def test_execute_node_writes_output(self) -> None:
        node = ExecuteNode.model_validate(
            {
                "id": "test",
                "type": "execute",
                "node": "workflow_orchestrator.tests.stub_nodes.SimpleEvaluator",
                "inputs": {"value": "1"},
                "outputs": {
                    "result": "optimization_flow.subjective_evaluation_1",
                    "instruction_key": "instruction",
                    "iterations": "{{subjective_iteration_count}}",
                },
            }
        )

        result = self.run_async(
            execute_node(
                node,
                workflow_vars=self.vars,
                context=self.context,
            )
        )
        self.assertEqual(result["status"], "completed")
        stored = resolve_path(self.context, "optimization_flow.subjective_evaluation_1")
        self.assertEqual(stored["scores"]["value"], 1)
        self.assertIn("created_at", stored)
        self.assertEqual(stored["instruction_key"], "instruction")
        self.assertEqual(stored["iterations"], 0)

    def test_skip_if_output_present(self) -> None:
        ts = datetime(2024, 4, 15, 12, 0, tzinfo=timezone.utc).isoformat()
        self.context["optimization_flow"]["subjective_evaluation_1"] = {"scores": {}, "created_at": ts}
        node = ExecuteNode.model_validate(
            {
                "id": "test",
                "type": "execute",
                "node": "workflow_orchestrator.tests.stub_nodes.SimpleEvaluator",
                "skipIfOutputPresent": True,
                "inputs": {},
                "outputs": {"result": "optimization_flow.subjective_evaluation_1"},
            }
        )

        result = self.run_async(
            execute_node(
                node,
                workflow_vars=self.vars,
                context=self.context,
            )
        )
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(
            self.context["optimization_flow"]["subjective_evaluation_1"]["created_at"],
            ts,
        )

    def test_sync_callable_executor(self) -> None:
        node = ExecuteNode.model_validate(
            {
                "id": "sync",
                "type": "execute",
                "node": "workflow_orchestrator.tests.stub_nodes.sync_evaluator",
                "inputs": {"value": "42"},
                "outputs": {"result": "optimization_flow.sync"},
            }
        )
        result = self.run_async(
            execute_node(node, workflow_vars=self.vars, context=self.context)
        )
        self.assertEqual(result["status"], "completed")
        stored = resolve_path(self.context, "optimization_flow.sync")
        self.assertEqual(stored["payload"]["value"], "42")

    def test_missing_result_raises(self) -> None:
        node = ExecuteNode.model_validate(
            {
                "id": "missing",
                "type": "execute",
                "node": "workflow_orchestrator.tests.stub_nodes.MissingResultEvaluator",
                "inputs": {},
                "outputs": {"result": "optimization_flow.missing"},
            }
        )

        with self.assertRaises(NodeExecutionError):
            self.run_async(execute_node(node, workflow_vars=self.vars, context=self.context))

    def test_output_updates_workflow_vars(self) -> None:
        node = ExecuteNode.model_validate(
            {
                "id": "producer",
                "type": "execute",
                "node": "workflow_orchestrator.tests.stub_nodes.CodeProducer",
                "inputs": {"code": "updated"},
                "outputs": {
                    "result": "optimization_flow.improved.code",
                    "latest_manifest_key": "optimization_flow.improved.code",
                    "vars.latest_manifest_key": "optimization_flow.improved.code",
                },
            }
        )

        self.vars["latest_manifest_key"] = "original_manifest"
        result = self.run_async(execute_node(node, workflow_vars=self.vars, context=self.context))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.vars["latest_manifest_key"], "optimization_flow.improved.code")
        stored = resolve_path(self.context, "optimization_flow.improved.code")
        self.assertEqual(stored["created_at"].split("T")[0], datetime.now(timezone.utc).isoformat().split("T")[0])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
