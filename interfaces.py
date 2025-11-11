"""Shared protocol definitions for workflow nodes."""

from __future__ import annotations

from typing import Any, Dict, Protocol, TypeAlias, runtime_checkable

JsonPrimitive = str | int | float | bool | None
JsonValue: TypeAlias = JsonPrimitive | Dict[str, "JsonValue"] | list["JsonValue"]
JsonObject: TypeAlias = Dict[str, JsonValue]


@runtime_checkable
class ExecutableNode(Protocol):
    """Protocol implemented by all executable workflow nodes."""

    async def evaluate(self, context: Dict[str, JsonValue], **params: JsonValue) -> Dict[str, JsonValue]:
        """Execute the node with the provided context and inputs."""
