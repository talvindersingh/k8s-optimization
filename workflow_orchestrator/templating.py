"""Templating utilities for workflow execution."""

from __future__ import annotations

import re
from typing import Any, Dict, Mapping, MutableMapping

from workflow_orchestrator.store import PathResolutionError, resolve_path

PLACEHOLDER_PATTERN = re.compile(r"\{\{([^{}]+)\}\}")


class TemplateError(ValueError):
    """Raised when template rendering fails."""


def render_value(value: Any, vars: MutableMapping[str, Any], store: Mapping[str, Any]) -> Any:
    """Render placeholders in a value (supports nested dicts/lists)."""
    if isinstance(value, str):
        return render_string(value, vars, store)
    if isinstance(value, list):
        return [render_value(item, vars, store) for item in value]
    if isinstance(value, dict):
        return {key: render_value(val, vars, store) for key, val in value.items()}
    return value


def render_string(template: str, vars: MutableMapping[str, Any], store: Mapping[str, Any]) -> Any:
    """Render placeholders inside a single string."""
    matches = list(PLACEHOLDER_PATTERN.finditer(template))
    if not matches:
        return template

    rendered_parts: list[str] = []
    cursor = 0
    last_value: Any = None
    for match in matches:
        start, end = match.span()
        rendered_parts.append(template[cursor:start])
        expr = match.group(1).strip()
        value = _evaluate_expression(expr, vars, store)
        rendered_parts.append(_stringify(value))
        last_value = value
        cursor = end
    rendered_parts.append(template[cursor:])

    combined = "".join(rendered_parts)

    # If the template is exactly one placeholder (ignoring whitespace), return
    # the actual value so types (int/float/bool) are preserved.
    if len(matches) == 1:
        prefix = template[: matches[0].start()].strip()
        suffix = template[matches[0].end() :].strip()
        if not prefix and not suffix:
            return last_value

    return combined


def evaluate_python_expression(
    expression: str,
    *,
    value: Any,
    vars: MutableMapping[str, Any],
    store: Mapping[str, Any],
) -> bool:
    """Render and evaluate a python expression in a restricted environment."""
    rendered = render_string(expression, vars, store)
    if not isinstance(rendered, str):
        rendered = str(rendered)

    safe_locals = {
        "value": _coerce_numeric(value),
        "vars": {k: _coerce_numeric(v) for k, v in vars.items()},
        "store": store,
    }

    try:
        result = eval(  # noqa: S307 - controlled environment
            rendered,
            {"__builtins__": {}},
            safe_locals,
        )
    except Exception as exc:  # pragma: no cover - bubble precise error
        raise TemplateError(f"Failed to evaluate python condition: {rendered}") from exc

    if not isinstance(result, bool):
        raise TemplateError(f"Python condition must return a boolean value, got {type(result).__name__}.")
    return result


def _evaluate_expression(expr: str, vars: MutableMapping[str, Any], store: Mapping[str, Any]) -> Any:
    if not expr:
        raise TemplateError("Empty placeholder expression.")

    # Pre-increment
    if expr.startswith("++"):
        var_name = expr[2:].strip()
        return _increment_var(vars, var_name, post=False)

    # Post-increment
    if expr.endswith("++"):
        var_name = expr[:-2].strip()
        return _increment_var(vars, var_name, post=True)

    if expr.startswith("vars."):
        return _resolve_mapping_path(vars, expr[5:])

    if expr.startswith("store."):
        path = expr[6:]
        try:
            return resolve_path(store, path, raise_on_missing=True)
        except PathResolutionError as exc:
            raise TemplateError(str(exc)) from exc

    if expr in vars:
        return vars[expr]

    # Literal coercion
    literal = _coerce_literal(expr)
    if literal is not None:
        return literal

    raise TemplateError(f"Unknown placeholder '{expr}'.")


def _increment_var(vars: MutableMapping[str, Any], name: str, *, post: bool) -> Any:
    container, key = _resolve_var_container(vars, name)
    if key not in container:
        raise TemplateError(f"Variable '{name}' not found for increment.")
    current = container[key]
    if not isinstance(current, (int, float)):
        raise TemplateError(f"Variable '{name}' must be numeric to increment.")
    if post:
        container[key] = current + 1
        return current
    container[key] = current + 1
    return container[key]


def _coerce_numeric(value: Any) -> Any:
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        lowered = value.lower()
        if lowered in {"true", "false", "none"}:
            return value
        try:
            if "." in value:
                return float(value)
            return int(value)
        except ValueError:
            return value
    return value


def _resolve_mapping_path(mapping: MutableMapping[str, Any], path: str) -> Any:
    parts = [part.strip() for part in path.split(".") if part.strip()]
    current: Any = mapping
    for part in parts:
        if isinstance(current, MutableMapping) and part in current:
            current = current[part]
        else:
            raise TemplateError(f"Variable '{path}' not found.")
    return current


def _resolve_var_container(mapping: MutableMapping[str, Any], path: str) -> tuple[MutableMapping[str, Any], str]:
    parts = [part.strip() for part in path.split(".") if part.strip()]
    if not parts:
        raise TemplateError("Invalid variable name for increment.")
    current: Any = mapping
    for part in parts[:-1]:
        if isinstance(current, MutableMapping) and part in current:
            current = current[part]
        else:
            raise TemplateError(f"Variable '{path}' not found.")
    if not isinstance(current, MutableMapping):
        raise TemplateError(f"Variable '{path}' must resolve to a mapping.")
    return current, parts[-1]


def _coerce_literal(expr: str) -> Any | None:
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


def _stringify(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return str(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return str(value)
