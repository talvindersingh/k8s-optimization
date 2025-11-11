"""Live integration tests for the Kubernetes manifest optimizer node."""

from __future__ import annotations

import importlib.util
import json
import os
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NODES_DIR = PROJECT_ROOT / "ansible_nodes"
DATASET_ROOT = PROJECT_ROOT / "dataset"
DEFAULT_DATASET_INDEX = "1"
DATASET_FILE = "optimization_flow.json"
ENV_PATH = PROJECT_ROOT / ".env"


def _ensure_environment() -> None:
    try:
        import agents  # noqa: F401
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise RuntimeError(
            "The OpenAI Agents SDK ('agents' package) must be installed in the Python 3.13 virtualenv."
        ) from exc

    if ENV_PATH.exists():
        load_dotenv(ENV_PATH, override=False)
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError(
            f"OPENAI_API_KEY is required. Populate {ENV_PATH} or export it before running the tests."
        )


def _load_module():
    module_path = NODES_DIR / "kubernetes_manifest_optimizer_agent.py"
    if not module_path.exists():
        raise FileNotFoundError(f"Kubernetes manifest optimizer module not found at {module_path}")
    spec = importlib.util.spec_from_file_location("kubernetes_manifest_optimizer_agent", module_path)
    if spec is None or spec.loader is None:  # pragma: no cover
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


class KubernetesManifestOptimizerExecutableTests(unittest.IsolatedAsyncioTestCase):
    """Exercises the manifest optimizer via the ExecutableNode interface."""

    @classmethod
    def setUpClass(cls) -> None:
        _ensure_environment()
        cls.module = _load_module()
        cls.ExecutableNode = getattr(__import__("interfaces", fromlist=["ExecutableNode"]), "ExecutableNode")

    def _build_context(self, dataset: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "instruction": dataset["instruction"],
            "original_code": dataset["original_code"],
            "original_manifest": dataset.get("original_manifest", dataset["original_code"]),
            "optimization_flow": deepcopy(dataset.get("optimization_flow", {})),
        }

    def _assert_schema(self, payload: Dict[str, Any]) -> None:
        self.assertIn("result", payload, "Node response must include 'result'.")
        result = payload["result"]
        self.assertIsInstance(result, dict, "'result' must be a dict.")
        self.assertIn("code", result, "Result missing 'code'.")
        self.assertIn("rationale", result, "Result missing 'rationale'.")
        self.assertIn("transformed_at", result, "Result missing 'transformed_at'.")
        self.assertIsInstance(result["code"], str, "code must be a string.")
        self.assertTrue(result["code"].strip(), "code must be non-empty.")
        self.assertIsInstance(result["rationale"], str, "rationale must be a string.")
        self.assertTrue(result["rationale"].strip(), "rationale must be non-empty.")
        self.assertTrue(result["code"].endswith("\n"), "code must end with a newline.")

    async def _run_optimizer(
        self,
        dataset_file: str,
        *,
        feedback: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        dataset = _load_dataset(dataset_file)
        context = self._build_context(dataset)
        node_cls = getattr(self.module, "KubernetesManifestOptimizerNode")
        node = node_cls()
        self.assertTrue(isinstance(node, self.ExecutableNode))

        kwargs: Dict[str, Any] = {
            "instruction": context["instruction"],
            "before_manifest": context["original_manifest"],
        }

        if feedback:
            kwargs["feedback"] = feedback

        response = await node.evaluate(context, **kwargs)

        # Ensure original context is unchanged
        self.assertEqual(
            dataset.get("optimization_flow", {}),
            context["optimization_flow"],
            "Optimizer should not mutate optimization_flow in-place.",
        )

        return response

    async def test_optimizer_generates_manifest(self) -> None:
        payload = await self._run_optimizer(DATASET_FILE)
        print(json.dumps({"dataset": DATASET_FILE, "payload": payload}, indent=2))
        self._assert_schema(payload)

    async def test_optimizer_with_manual_feedback(self) -> None:
        feedback = {
            "schema_compliance": {"score": 1, "reason": "Deployment missing probes and pod security."},
            "resource_configuration": {"score": 1, "reason": "No resource requests/limits are defined."},
            "security_posture": {"score": 1, "reason": "Containers run as root and lack security context."},
            "operational_resilience": {"score": 2, "reason": "Basic deployment works but lacks readiness probe."},
            "best_practice_alignment": {"score": 1, "reason": "Missing metadata annotations and strategy details."},
        }
        payload = await self._run_optimizer(DATASET_FILE, feedback=feedback)
        print(json.dumps({"dataset": DATASET_FILE, "payload": payload}, indent=2))
        self._assert_schema(payload)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
