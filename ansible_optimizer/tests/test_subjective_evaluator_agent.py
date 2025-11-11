"""Live integration tests for the Kubernetes subjective evaluator executable node."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
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
    """Ensure OpenAI credentials and dependencies are available."""
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
    """Dynamically import the subjective evaluator agent module."""
    module_path = NODES_DIR / "subjective_evaluator_agent.py"
    if not module_path.exists():
        raise FileNotFoundError(f"Subjective evaluator module not found at {module_path}")
    spec = importlib.util.spec_from_file_location("subjective_evaluator_agent", module_path)
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


class SubjectiveEvaluatorExecutableTests(unittest.IsolatedAsyncioTestCase):
    """Exercises the subjective evaluator node via the ExecutableNode interface."""

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
        self.assertIsInstance(result, dict, "'result' must be a dictionary.")
        self.assertIn("evaluated_at", result, "Result missing evaluated_at timestamp.")
        self.assertTrue(str(result["evaluated_at"]).strip(), "evaluated_at must be non-empty.")

        self.assertIn("scores", result, "Result missing scores dictionary.")
        scores = result["scores"]
        self.assertIsInstance(scores, dict, "scores must be a dictionary.")

        required_metrics = [
            "schema_compliance",
            "resource_configuration",
            "security_posture",
            "operational_resilience",
            "best_practice_alignment",
        ]
        for metric in required_metrics:
            self.assertIn(metric, scores, f"Missing metric {metric}")
            metric_payload = scores[metric]
            self.assertIsInstance(metric_payload, dict, f"{metric} must be an object.")
            self.assertIn("score", metric_payload, f"{metric} missing score.")
            self.assertIn("reason", metric_payload, f"{metric} missing reason.")
            self.assertIsInstance(metric_payload["score"], int, f"{metric} score must be int.")
            self.assertGreaterEqual(metric_payload["score"], 0, f"{metric} score below 0.")
            self.assertLessEqual(metric_payload["score"], 3, f"{metric} score above 3.")
            self.assertIsInstance(metric_payload["reason"], str, f"{metric} reason must be string.")
            self.assertTrue(metric_payload["reason"].strip(), f"{metric} reason must be non-empty.")

        self.assertIn("weighted_overall_score", scores, "scores missing weighted_overall_score.")
        weighted_score = scores["weighted_overall_score"]
        self.assertIsInstance(weighted_score, (int, float), "weighted_overall_score must be numeric.")
        self.assertGreaterEqual(weighted_score, 0.0, "weighted_overall_score below 0.")
        self.assertLessEqual(weighted_score, 1.0, "weighted_overall_score above 1.")

    async def _run_evaluator(self, dataset_file: str) -> Dict[str, Any]:
        dataset = _load_dataset(dataset_file)
        context = self._build_context(dataset)
        node_cls = getattr(self.module, "SubjectiveEvaluatorNode")
        node = node_cls()
        self.assertTrue(isinstance(node, self.ExecutableNode))
        response = await node.evaluate(
            context,
            instruction=context["instruction"],
            manifest=context["original_manifest"],
        )
        # Ensure context is not mutated.
        self.assertEqual(
            dataset.get("optimization_flow", {}),
            context["optimization_flow"],
            "Evaluator should not mutate optimization_flow in-place.",
        )
        return response

    def _calculate_expected_weighted_score(self, scores: Dict[str, Any]) -> float:
        """Calculate expected weighted_overall_score from individual metric scores.
        
        Formula: weighted_overall_score = sum of ((score/3) * weight) for each metric
        Weights: syntax(20%), structure(25%), parameters(25%), completeness(20%), best_practices(10%)
        """
        METRIC_WEIGHTS = {
            "schema_compliance": 0.25,
            "resource_configuration": 0.25,
            "security_posture": 0.20,
            "operational_resilience": 0.20,
            "best_practice_alignment": 0.10,
        }
        
        total = 0.0
        for metric, weight in METRIC_WEIGHTS.items():
            metric_score = scores[metric]["score"]
            total += (metric_score / 3.0) * weight
        return round(total, 4)

    async def test_evaluator_on_dataset(self) -> None:
        payload = await self._run_evaluator(DATASET_FILE)
        print(json.dumps({"dataset": DATASET_FILE, "payload": payload}, indent=2))
        self._assert_schema(payload)

    async def test_weighted_overall_score_calculation(self) -> None:
        """Validate that weighted_overall_score is calculated correctly based on individual metric scores."""
        payload = await self._run_evaluator(DATASET_FILE)
        
        # Extract scores from the result
        result = payload["result"]
        scores = result["scores"]
        actual_weighted_score = scores["weighted_overall_score"]
        
        # Calculate expected weighted score
        expected_weighted_score = self._calculate_expected_weighted_score(scores)
        
        # Assert they match 
        self.assertAlmostEqual(
            actual_weighted_score,
            expected_weighted_score,
            places=4,
            msg=(
                f"weighted_overall_score calculation is incorrect. "
                f"Expected {expected_weighted_score}, got {actual_weighted_score}. "
                f"Individual scores: "
                f"schema={scores['schema_compliance']['score']}, "
                f"resources={scores['resource_configuration']['score']}, "
                f"security={scores['security_posture']['score']}, "
                f"resilience={scores['operational_resilience']['score']}, "
                f"best_practices={scores['best_practice_alignment']['score']}"
            ),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
