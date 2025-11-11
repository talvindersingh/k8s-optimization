"""Stub nodes for executor tests."""

from __future__ import annotations

from interfaces import JsonValue


class SimpleEvaluator:
    """Minimal evaluator returning a fixed result."""

    async def evaluate(self, context: dict[str, JsonValue], **params: JsonValue) -> dict[str, JsonValue]:
        value = params.get("value", 0)
        if isinstance(value, str) and value.isdigit():
            value = int(value)
        return {
            "result": {
                "scores": {"value": value},
            }
        }


def sync_evaluator(context: dict[str, JsonValue], **params: JsonValue) -> dict[str, JsonValue]:
    """Synchronous evaluator verifying adapter logic."""
    return {
        "result": {
            "payload": params,
        }
    }


class MissingResultEvaluator:
    async def evaluate(self, context: dict[str, JsonValue], **params: JsonValue) -> dict[str, JsonValue]:
        return {}


class IncrementIteration:
    async def evaluate(self, context: dict[str, JsonValue], **params: JsonValue) -> dict[str, JsonValue]:
        vars_map = context.get("vars")
        if not isinstance(vars_map, dict):
            raise RuntimeError("vars map missing from context.")
        current = vars_map.get("iteration", 0)
        vars_map["iteration"] = current + 1
        return {"result": vars_map["iteration"]}


async def increment_iteration(context: dict[str, JsonValue], **params: JsonValue) -> dict[str, JsonValue]:
    """Async function variant used by workflow stub."""
    vars_map = context.get("vars")
    if not isinstance(vars_map, dict):
        raise RuntimeError("vars map missing from context.")
    current = vars_map.get("iteration", 0)
    vars_map["iteration"] = current + 1
    return {"result": vars_map["iteration"]}


class CodeProducer:
    """Returns a canned code artifact."""

    async def evaluate(self, context: dict[str, JsonValue], **params: JsonValue) -> dict[str, JsonValue]:
        return {
            "result": {
                "code": params.get("code", "generated code"),
            }
        }
