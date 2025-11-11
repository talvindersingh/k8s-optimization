"""Live integration test for the validate-optimize workflow."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import SkipTest

from dotenv import load_dotenv

from workflow_orchestrator.engine import run


TEST_DIR = Path(__file__).resolve().parent
WORKFLOW_ROOT = TEST_DIR.parent
REPO_ROOT = WORKFLOW_ROOT.parent
CONFIG_PATH = WORKFLOW_ROOT / "flow-examples" / "validate-transform-loop.json"
DATASET_PATH = REPO_ROOT / "ansible_optimizer" / "dataset" / "1" / "optimization_flow.json"
AUTOMATION_VENV = REPO_ROOT / "ansible_optimizer" / ".venv-automation" / "bin" / "python"
MCP_SERVER = REPO_ROOT / "ansible_optimizer" / "automation" / "validator_mcp_server.py"
ENV_PATH = REPO_ROOT / "ansible_optimizer" / ".env"


def _check_prerequisites() -> None:
    """Validate that required tooling is available for the live workflow test."""
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Workflow definition not found: {CONFIG_PATH}")
    if not DATASET_PATH.exists():
        raise FileNotFoundError(f"Dataset not found: {DATASET_PATH}")
    if not AUTOMATION_VENV.exists():
        raise SkipTest(
            "Automation virtualenv missing. Create it at "
            f"{AUTOMATION_VENV.parent} using python3.13 -m venv .venv-automation "
            "and install requirements before running this test."
        )
    if not MCP_SERVER.exists():
        raise FileNotFoundError(f"Validator MCP server script not found: {MCP_SERVER}")
    if not os.getenv("OPENAI_API_KEY") and ENV_PATH.exists():
        load_dotenv(ENV_PATH, override=False)
    if not os.getenv("OPENAI_API_KEY"):
        raise SkipTest("OPENAI_API_KEY must be set for the analyzer and optimizer agents.")
    if not shutil.which(os.environ.get("KUBECONFORM_BIN", "kubconform")):
        raise SkipTest("kubconform binary not found on PATH.")
    if not shutil.which(os.environ.get("KUBE_LINTER_BIN", "kube-linter")):
        raise SkipTest("kube-linter binary not found on PATH.")


def _lookup_path(context: dict, dotted: str):
    parts = dotted.split(".")
    current = context
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise KeyError(dotted)
    return current


class ValidateOptimizeFlowIntegrationTests(unittest.TestCase):
    """Runs the full validate-optimize workflow against the sample dataset."""

    @classmethod
    def setUpClass(cls) -> None:
        _check_prerequisites()

    def test_validate_optimize_workflow_executes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "optimization_flow.json"
            store_path.write_text(DATASET_PATH.read_text(encoding="utf-8"), encoding="utf-8")

            run(CONFIG_PATH, store_path)

            updated = json.loads(store_path.read_text(encoding="utf-8"))
            optimization_flow = updated.get("optimization_flow") or {}

            validation_keys = [
                key for key in optimization_flow.keys() if key.startswith("objective_validation_")
            ]
            self.assertTrue(validation_keys, "Expected at least one objective validation entry.")
            validation_keys.sort()

            first_entry = optimization_flow[validation_keys[0]]
            self._assert_analyzer_outputs(first_entry)

            vars_block = updated.get("vars") or {}
            last_key = vars_block.get("last_validation_result_key")
            self.assertIsInstance(last_key, str)
            self.assertTrue(last_key)
            latest_entry = _lookup_path(updated, last_key)
            self._assert_analyzer_outputs(latest_entry)

            self.assertIn("validation_iteration_count", vars_block)
            self.assertGreaterEqual(vars_block["validation_iteration_count"], len(validation_keys) + 1)

            # If a fix was requested, ensure a new code revision exists.
            if latest_entry["manifest_fix_required"]:
                improved_keys = [
                    key for key in optimization_flow if key.startswith("improved_manifest_")
                ]
                self.assertTrue(
                    improved_keys,
                    "Expected an improved manifest artifact when manifest_fix_required is True.",
                )

    def _assert_analyzer_outputs(self, entry: dict) -> None:
        self.assertIn("objective_validations", entry)
        self.assertIsInstance(entry["objective_validations"], dict)
        self.assertIn("validations_result", entry)
        self.assertIn(entry["validations_result"], {"pass", "fail"})
        self.assertIn("result_analysis", entry)
        self.assertIsInstance(entry["result_analysis"], str)
        self.assertTrue(entry["result_analysis"].strip())
        self.assertIn("manifest_fix_required", entry)
        self.assertIsInstance(entry["manifest_fix_required"], bool)
        objective = entry["objective_validations"]
        overall = (objective.get("overall_result") or objective.get("overall") or "").lower()
        if overall in {"pass", "fail"}:
            self.assertEqual(entry["validations_result"], overall)

if __name__ == "__main__":  # pragma: no cover
    unittest.main()
