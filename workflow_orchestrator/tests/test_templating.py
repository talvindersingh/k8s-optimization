"""Tests for templating utilities."""

from __future__ import annotations

import unittest

from workflow_orchestrator.templating import (
    TemplateError,
    evaluate_python_expression,
    render_string,
    render_value,
)


class TemplatingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vars: dict[str, object] = {
            "latest_manifest_key": "original_manifest",
            "latest_code_key": "original_manifest",
            "subjective_iteration_count": 0,
            "code_iteration_count": 1,
            "max_code_iterations": 5,
            "threshold": 0.8,
        }
        self.store = {
            "optimization_flow": {
                "subjective_evaluation_1": {
                    "scores": {"weighted_overall_score": 0.7},
                }
            }
        }

    def test_render_simple_variable(self) -> None:
        result = render_string("{{latest_manifest_key}}", self.vars, self.store)
        self.assertEqual(result, "original_manifest")

    def test_render_numeric_placeholder_returns_int(self) -> None:
        result = render_string("{{code_iteration_count}}", self.vars, self.store)
        self.assertEqual(result, 1)
        self.assertIsInstance(result, int)

    def test_render_with_contextual_text(self) -> None:
        path = render_string("optimization_flow.improved_manifest_B{{code_iteration_count}}", self.vars, self.store)
        self.assertEqual(path, "optimization_flow.improved_manifest_B1")

    def test_pre_increment(self) -> None:
        rendered = render_string("optimization_flow.subjective_evaluation_{{++subjective_iteration_count}}", self.vars, self.store)
        self.assertEqual(rendered, "optimization_flow.subjective_evaluation_1")
        self.assertEqual(self.vars["subjective_iteration_count"], 1)

    def test_post_increment(self) -> None:
        rendered = render_string("{{code_iteration_count++}}", self.vars, self.store)
        self.assertEqual(rendered, 1)
        self.assertEqual(self.vars["code_iteration_count"], 2)

    def test_render_nested_structure(self) -> None:
        payload = {
            "instruction": "{{latest_manifest_key}}",
            "targets": [
                "optimization_flow.subjective_evaluation_{{++subjective_iteration_count}}",
                "{{code_iteration_count}}",
            ],
        }
        rendered = render_value(payload, self.vars, self.store)
        self.assertEqual(
            rendered,
            {
                "instruction": "original_manifest",
                "targets": [
                    "optimization_flow.subjective_evaluation_1",
                    1,
                ],
            },
        )

    def test_python_expression(self) -> None:
        self.vars["max_code_iterations"] = 2
        result = evaluate_python_expression(
            "value >= {{max_code_iterations}}",
            value=3,
            vars=self.vars,
            store=self.store,
        )
        self.assertTrue(result)

    def test_python_expression_false(self) -> None:
        self.vars["max_code_iterations"] = 5
        result = evaluate_python_expression(
            "value >= {{max_code_iterations}}",
            value=3,
            vars=self.vars,
            store=self.store,
        )
        self.assertFalse(result)

    def test_unknown_placeholder_raises(self) -> None:
        with self.assertRaises(TemplateError):
            render_string("{{missing}}", self.vars, self.store)

    def test_store_placeholder(self) -> None:
        result = render_string(
            "{{store.optimization_flow.subjective_evaluation_1.scores.weighted_overall_score}}",
            self.vars,
            self.store,
        )
        self.assertEqual(result, 0.7)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
