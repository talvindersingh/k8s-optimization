"""Tests for conditional node evaluation."""

from __future__ import annotations

import unittest

from workflow_orchestrator.conditional import ConditionalEvaluationError, evaluate_conditional
from workflow_orchestrator.models import ConditionalNode


class ConditionalEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vars = {
            "subjective_pass_score": 0.8,
            "code_iteration_count": 2,
            "max_code_iterations": 5,
        }
        self.context = {
            "optimization_flow": {
                "subjective_evaluation_1": {
                    "weighted_overall_score": 0.9,
                }
            }
        }

    def test_comparator_branch_true(self) -> None:
        node = ConditionalNode.model_validate(
            {
                "id": "check",
                "type": "conditional",
                "branches": [
                    {
                        "value": "{{code_iteration_count}}",
                        "condition": {"op": ">=", "compare_to": "{{max_code_iterations}}"},
                        "goto": "stop",
                    },
                    {
                        "value": "optimization_flow.subjective_evaluation_1.weighted_overall_score",
                        "condition": {"op": ">=", "compare_to": "{{subjective_pass_score}}"},
                        "goto": "END",
                    },
                ],
                "else": "continue",
            }
        )
        next_node = evaluate_conditional(node, workflow_vars=self.vars, context=self.context)
        self.assertEqual(next_node, "END")

    def test_python_branch(self) -> None:
        node = ConditionalNode.model_validate(
            {
                "id": "python-check",
                "type": "conditional",
                "branches": [
                    {
                        "value": "{{code_iteration_count}}",
                        "condition": {"python": "value >= {{max_code_iterations}}"},
                        "goto": "END",
                    }
                ],
                "else": "continue",
            }
        )
        next_node = evaluate_conditional(node, workflow_vars=self.vars, context=self.context)
        self.assertEqual(next_node, "continue")

        self.vars["code_iteration_count"] = 6
        next_node = evaluate_conditional(node, workflow_vars=self.vars, context=self.context)
        self.assertEqual(next_node, "END")

    def test_else_path(self) -> None:
        node = ConditionalNode.model_validate(
            {
                "id": "else-test",
                "type": "conditional",
                "branches": [
                    {
                        "value": "{{code_iteration_count}}",
                        "condition": {"op": "<", "compare_to": "{{subjective_pass_score}}"},
                        "goto": "never",
                    }
                ],
                "else": "fallback",
            }
        )
        next_node = evaluate_conditional(node, workflow_vars=self.vars, context=self.context)
        self.assertEqual(next_node, "fallback")

    def test_unsupported_operator(self) -> None:
        node = ConditionalNode.model_validate(
            {
                "id": "bad",
                "type": "conditional",
                "branches": [
                    {
                        "value": "{{code_iteration_count}}",
                        "condition": {"op": "contains", "compare_to": "5"},
                        "goto": "END",
                    }
                ],
                "else": "fallback",
            }
        )
        with self.assertRaises(ConditionalEvaluationError):
            evaluate_conditional(node, workflow_vars=self.vars, context=self.context)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
