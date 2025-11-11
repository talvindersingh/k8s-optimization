"""Utilities for reading and mutating optimization flow JSON structures."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, Tuple


class PathResolutionError(KeyError):
    """Raised when a JSON path cannot be resolved."""


def _split_path(path: str) -> Tuple[str, ...]:
    if not path or not path.strip():
        raise ValueError("Path must be a non-empty string.")
    return tuple(part.strip() for part in path.split(".") if part.strip())


def resolve_path(data: Dict[str, Any], path: str, *, default: Any = None, raise_on_missing: bool = True) -> Any:
    """Resolve a dotted path inside a nested dictionary."""
    current: Any = data
    parts = _split_path(path)
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            if raise_on_missing:
                raise PathResolutionError(f"Path '{path}' not found at segment '{part}'.")
            return default
        current = current[part]
    return current


def ensure_container(data: Dict[str, Any], path: str) -> Tuple[Dict[str, Any], str]:
    """Ensure all parent containers for a path exist and return the parent dict and final key."""
    parts = _split_path(path)
    if len(parts) == 1:
        return data, parts[0]

    current: Any = data
    for part in parts[:-1]:
        if not isinstance(current, dict):
            raise TypeError(f"Cannot traverse through non-dict object at '{part}'.")
        if part not in current or current[part] is None:
            current[part] = {}
        elif not isinstance(current[part], dict):
            raise TypeError(f"Expected dict at '{part}', found {type(current[part]).__name__}.")
        current = current[part]

    return current, parts[-1]


def write_path(data: Dict[str, Any], path: str, value: Any) -> None:
    """Write a value to the specified path, creating intermediate containers as needed."""
    container, key = ensure_container(data, path)
    if not isinstance(container, dict):
        raise TypeError(f"Parent container for '{path}' must be a dict.")
    container[key] = deepcopy(value)


def write_with_metadata(
    data: Dict[str, Any],
    path: str,
    value: Any,
    *,
    created_at: datetime | None = None,
    provenance: Dict[str, str] | None = None,
) -> None:
    """Write a value and annotate it with metadata (created_at/provenance)."""
    created_at = created_at or datetime.now(timezone.utc)
    write_path(data, path, value)

    target = resolve_path(data, path, raise_on_missing=True)
    if isinstance(target, dict):
        target.setdefault("created_at", created_at.isoformat())
        if provenance:
            for key, ref in provenance.items():
                target.setdefault(key, ref)
    else:
        # For primitive targets, store metadata alongside the value.
        container, key = ensure_container(data, path)
        metadata_key = f"{key}_metadata"
        container[metadata_key] = {
            "created_at": created_at.isoformat(),
        }
        if provenance:
            container[metadata_key].update(provenance)
