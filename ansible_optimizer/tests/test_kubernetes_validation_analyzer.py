"""Live integration tests for the Kubernetes validation analyzer node."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NODES_DIR = PROJECT_ROOT / "ansible_nodes"
AUTOMATION_DIR = PROJECT_ROOT / "automation"
DATASET_ROOT = PROJECT_ROOT / "dataset"
DEFAULT_DATASET_INDEX = "1"
DATASET_FILE = "optimization_flow.json"
AUTOMATION_VENV = PROJECT_ROOT / ".venv-automation" / "bin" / "python"
SERVER_SCRIPT = AUTOMATION_DIR / "validator_mcp_server.py"
ENV_PATH = PROJECT_ROOT / ".env"


def _which(executable: str) -> bool:
    return shutil.which(executable) is not None


def _load_module():
    """Dynamically import the Kubernetes validation analyzer module."""
    module_path = NODES_DIR / "kubernetes_validation_analyzer.py"
    if not module_path.exists():
        raise FileNotFoundError(f"Validation analyzer module not found: {module_path}")
    spec = importlib.util.spec_from_file_location("kubernetes_validation_analyzer", module_path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"Unable to load module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_dataset(dataset_file: str) -> Dict[str, Any]:
    dataset_index = os.getenv("DATASET_INDEX", DEFAULT_DATASET_INDEX)
    dataset_path = DATASET_ROOT / dataset_index / dataset_file
    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset not found: {dataset_path}")
    return json.loads(dataset_path.read_text(encoding="utf-8"))


AUTOMATION_DEPENDENCIES_READY = AUTOMATION_VENV.exists() and SERVER_SCRIPT.exists()


class KubernetesValidationAnalyzerTests(unittest.IsolatedAsyncioTestCase):
    """Exercises the validation analyzer node via the ExecutableNode protocol."""

    @classmethod
    def setUpClass(cls) -> None:
        if ENV_PATH.exists():
            load_dotenv(ENV_PATH, override=False)
        if not os.getenv("OPENAI_API_KEY"):
            raise unittest.SkipTest("OPENAI_API_KEY must be set to run live analyzer tests.")
        if not AUTOMATION_DEPENDENCIES_READY:
            raise unittest.SkipTest(
                "Automation virtualenv or validator MCP server script missing. "
                "Run automation/README setup steps before executing this test."
            )
        kubconform_bin = os.environ.get("KUBECONFORM_BIN", "kubconform")
        kube_linter_bin = os.environ.get("KUBE_LINTER_BIN", "kube-linter")
        if not _which(kubconform_bin):
            raise unittest.SkipTest(f"kubconform binary '{kubconform_bin}' not found on PATH.")
        if not _which(kube_linter_bin):
            raise unittest.SkipTest(f"kube-linter binary '{kube_linter_bin}' not found on PATH.")
        cls.module = _load_module()
        cls.ExecutableNode = getattr(__import__("interfaces", fromlist=["ExecutableNode"]), "ExecutableNode")

    def _build_context(self, dataset: Dict[str, Any]) -> Dict[str, Any]:
        context: Dict[str, Any] = {
            "instruction": dataset.get("instruction", ""),
            "original_code": dataset.get("original_code", ""),
            "original_manifest": dataset.get("original_manifest", dataset.get("original_code", "")),
            "optimization_flow": deepcopy(dataset.get("optimization_flow", {})),
        }
        context.setdefault("vars", {"latest_manifest_key": "original_manifest"})
        return context

    def _assert_schema(self, payload: Dict[str, Any]) -> None:
        self.assertIn("result", payload, "Node response must include 'result'.")
        result = payload["result"]
        self.assertIsInstance(result, dict, "'result' must be a dictionary.")
        self.assertIn("objective_validations", result, "'result' missing objective_validations.")
        self.assertIn("validations_result", result, "'result' missing validations_result.")
        self.assertIn("result_analysis", result, "'result' missing result_analysis.")
        self.assertIn("manifest_fix_required", result, "'result' missing manifest_fix_required.")

        objective = result["objective_validations"]
        self.assertIsInstance(objective, dict, "objective_validations must be a dictionary.")
        self.assertIn("overall_result", objective, "objective_validations missing overall_result.")
        self.assertIn("kubconform", objective, "objective_validations missing kubconform block.")
        self.assertIn("kube-linter", objective, "objective_validations missing kube-linter block.")

        for name in ("kubconform", "kube-linter"):
            block = objective[name]
            self.assertIsInstance(block, dict, f"{name} block must be dict.")
            self.assertIn("result", block, f"{name} block missing result.")
            self.assertIn("messages", block, f"{name} block missing messages.")
            self.assertIsInstance(block["messages"], list, f"{name} messages must be list.")

        validations_result = result["validations_result"]
        self.assertIn(validations_result, {"pass", "fail"}, "validations_result must be 'pass' or 'fail'.")
        self.assertIsInstance(result["result_analysis"], str, "result_analysis must be string.")
        self.assertTrue(result["result_analysis"].strip(), "result_analysis must be non-empty.")

    async def _run_analyzer(self, dataset_file: str) -> Dict[str, Any]:
        dataset = _load_dataset(dataset_file)
        context = self._build_context(dataset)
        node_cls = getattr(self.module, "KubernetesValidationAnalyzerNode")
        node = node_cls()
        self.assertTrue(isinstance(node, self.ExecutableNode))
        response = await node.evaluate(
            context,
            instruction=context["instruction"],
            manifest=context["original_manifest"],
        )
        self.assertEqual(
            dataset.get("optimization_flow", {}),
            context["optimization_flow"],
            "Analyzer should not mutate optimization_flow in-place.",
        )
        return response

    async def test_validation_analyzer_on_dataset(self) -> None:
        payload = await self._run_analyzer(DATASET_FILE)
        print(json.dumps({"dataset": DATASET_FILE, "payload": payload}, indent=2)[:2000])
        self._assert_schema(payload)

    async def test_validation_analyzer_detects_invalid_manifest(self) -> None:
        prior_index = os.environ.get("DATASET_INDEX")
        os.environ["DATASET_INDEX"] = "2"
        try:
            payload = await self._run_analyzer(DATASET_FILE)
        finally:
            if prior_index is None:
                os.environ.pop("DATASET_INDEX", None)
            else:
                os.environ["DATASET_INDEX"] = prior_index

        result = payload["result"]
        kubconform = result["objective_validations"]["kubconform"]
        self.assertNotEqual(
            kubconform.get("result"),
            "pass",
            "kubconform should report failure for the incomplete CronJob manifest.",
        )

    async def test_mcp_tool_invocation(self) -> None:
        params = StdioServerParameters(
            command=str(AUTOMATION_VENV),
            args=[str(SERVER_SCRIPT)],
        )

        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                tool_names = [tool.name for tool in tools.tools]
                self.assertIn("validate_manifest", tool_names)

                dataset = _load_dataset(DATASET_FILE)
                manifest = dataset.get("original_manifest", dataset["original_code"])
                result = await session.call_tool(
                    "validate_manifest",
                    arguments={"manifest_content": manifest},
                )

        self.assertTrue(result.content)
        payload = json.loads(result.content[0].text)
        self.assertIn("objective_validations", payload)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
