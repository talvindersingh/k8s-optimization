"""Conditional node evaluation logic."""

from __future__ import annotations

from typing import Mapping, MutableMapping

from interfaces import JsonValue

from workflow_orchestrator.models import ConditionalBranch, ConditionalNode
from workflow_orchestrator.store import PathResolutionError, resolve_path
from workflow_orchestrator.templating import evaluate_python_expression, render_string
from workflow_orchestrator.templating import _coerce_literal  # type: ignore


class ConditionalEvaluationError(RuntimeError):
    """Raised when evaluating a conditional node fails."""


_COMPARATORS = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
}


def evaluate_conditional(
    node: ConditionalNode,
    *,
    workflow_vars: MutableMapping[str, JsonValue],
    context: Mapping[str, JsonValue],
) -> str:
    """Evaluate a conditional node and return the next node id (or 'END')."""
    for branch in node.branches:
        if _branch_matches(branch, workflow_vars=workflow_vars, context=context):
            return branch.goto
    return node.else_goto


def _branch_matches(
    branch: ConditionalBranch,
    *,
    workflow_vars: MutableMapping[str, JsonValue],
    context: Mapping[str, JsonValue],
) -> bool:
    if branch.condition is None:
        return True

    if branch.condition.python:
        value = None
        if branch.value:
            rendered = render_string(branch.value, workflow_vars, context)
            value = _resolve_operand(rendered, workflow_vars, context)
        return evaluate_python_expression(
            branch.condition.python,
            value=value,
            vars=workflow_vars,
            store=context,
        )

    comparator = branch.condition.op
    compare_to_template = branch.condition.compare_to
    if not comparator or comparator not in _COMPARATORS:
        raise ConditionalEvaluationError(f"Unsupported comparator '{comparator}'.")
    if compare_to_template is None:
        raise ConditionalEvaluationError("Comparator branch requires 'compare_to'.")

    left = _resolve_operand(render_string(branch.value or "", workflow_vars, context), workflow_vars, context)
    right = _resolve_operand(render_string(compare_to_template, workflow_vars, context), workflow_vars, context)
    try:
        return _COMPARATORS[comparator](left, right)
    except TypeError:
        return False


def _resolve_operand(value: object, vars: MutableMapping[str, JsonValue], context: Mapping[str, JsonValue]) -> object:
    if not isinstance(value, str):
        return value

    # Try workflow vars (supports dotted path)
    parts = [part.strip() for part in value.split(".") if part.strip()]
    current: object = vars
    try:
        for part in parts:
            if isinstance(current, MutableMapping) and part in current:
                current = current[part]
            else:
                raise KeyError
        return current
    except KeyError:
        pass

    # Try store path
    try:
        return resolve_path(context, value, raise_on_missing=True)
    except (PathResolutionError, KeyError):
        literal = _attempt_literal(value)
        if literal is not None:
            return literal
        return value


def _attempt_literal(expr: str) -> object | None:
    lowered = expr.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "none":
        return None
    try:
        if "." in expr:
            return float(expr)
        return int(expr)
    except ValueError:
        return None
