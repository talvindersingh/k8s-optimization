"""Execution utilities for workflow nodes."""

from __future__ import annotations

import importlib
import inspect
from copy import deepcopy
from datetime import datetime, timezone
from types import ModuleType
from typing import Any, Callable, Dict, Mapping, MutableMapping

from interfaces import ExecutableNode, JsonValue

from workflow_orchestrator.models import ExecuteNode
from workflow_orchestrator.store import PathResolutionError, resolve_path, write_with_metadata, write_path
from workflow_orchestrator.templating import render_string


class NodeExecutionError(RuntimeError):
    """Raised when a node fails to execute."""


def _import_callable(dotted_path: str) -> Callable[..., Any]:
    module_path, _, attr = dotted_path.rpartition(".")
    if not module_path:
        raise NodeExecutionError(f"Invalid node path '{dotted_path}'. Expected module.callable format.")

    module: ModuleType = importlib.import_module(module_path)
    try:
        callable_obj = getattr(module, attr)
    except AttributeError as exc:
        raise NodeExecutionError(f"Callable '{attr}' not found in module '{module_path}'.") from exc
    return callable_obj


async def execute_node(
    node: ExecuteNode,
    *,
    workflow_vars: MutableMapping[str, JsonValue],
    context: MutableMapping[str, JsonValue],
) -> Dict[str, Any]:
    """Execute an `execute` node and persist its outputs."""
    # Resolve output skip condition
    primary_output = next((k for k in node.outputs.keys() if not k.endswith("_key")), None)
    if node.skip_if_output_present and primary_output:
        try:
            rendered_path = render_string(node.outputs[primary_output], deepcopy(workflow_vars), context)
            existing = resolve_path(context, rendered_path, raise_on_missing=True)
            if existing:
                _sync_outputs_on_skip(node, workflow_vars, context)
                return {"status": "skipped", "reason": "output already present"}
        except (PathResolutionError, KeyError):
            pass  # No output yet; proceed.

    callable_obj = _import_callable(node.node)

    # Build async callable interface.
    async def executor_callable(context_dict: Dict[str, JsonValue], **params: JsonValue) -> Dict[str, JsonValue]:
        raise NotImplementedError

    if isinstance(callable_obj, type):
        instance = callable_obj()  # type: ignore[call-arg]
        if not isinstance(instance, ExecutableNode):
            raise NodeExecutionError(f"Node '{node.node}' does not implement ExecutableNode protocol.")

        async def executor_callable(context_dict: Dict[str, JsonValue], **params: JsonValue) -> Dict[str, JsonValue]:
            result = instance.evaluate(context_dict, **params)
            if inspect.isawaitable(result):
                return await result  # type: ignore[return-value]
            return result  # type: ignore[return-value]

    elif callable(callable_obj):

        async def executor_callable(context_dict: Dict[str, JsonValue], **params: JsonValue) -> Dict[str, JsonValue]:
            result = callable_obj(context_dict, **params)  # type: ignore[misc]
            if inspect.isawaitable(result):
                return await result  # type: ignore[return-value]
            return result  # type: ignore[return-value]

    else:
        raise NodeExecutionError(f"Node '{node.node}' is not callable.")

    rendered_inputs: Dict[str, JsonValue] = {}
    for key, template in node.inputs.items():
        rendered = render_string(template, workflow_vars, context)
        rendered_inputs[key] = _resolve_input_value(rendered, context)

    context["vars"] = workflow_vars
    try:
        result = await executor_callable(context, **rendered_inputs)
    finally:
        context.pop("vars", None)

    if not isinstance(result, Mapping):
        raise NodeExecutionError(f"Node '{node.id}' must return a mapping of outputs.")

    timestamp = datetime.now(timezone.utc)
    primary_key = next((key for key in node.outputs if key in result), None)
    if primary_key is None:
        raise NodeExecutionError(f"Node '{node.id}' returned no matching outputs.")

    primary_path_value = render_string(node.outputs[primary_key], workflow_vars, context)
    if not isinstance(primary_path_value, str):
        raise NodeExecutionError(f"Primary output path for node '{node.id}' must resolve to a string.")
    primary_path = primary_path_value
    provenance = {
        key: render_string(value, workflow_vars, context)
        for key, value in node.outputs.items()
        if key.endswith("_key")
    }

    if primary_path.startswith("vars."):
        write_path(context, primary_path, result[primary_key])
        var_name = primary_path.split(".", 1)[1]
        workflow_vars[var_name] = result[primary_key]
        stored_obj = resolve_path(context, primary_path, raise_on_missing=True)
        primary_is_mapping = isinstance(stored_obj, MutableMapping)
    else:
        write_with_metadata(
            context,
            primary_path,
            result[primary_key],
            created_at=timestamp,
            provenance=provenance if provenance else None,
        )
        stored_obj = resolve_path(context, primary_path, raise_on_missing=True)
        primary_is_mapping = isinstance(stored_obj, MutableMapping)

    for key, template in node.outputs.items():
        if key.startswith("vars."):
            var_name = key.split(".", 1)[1]
            rendered_var = render_string(template, workflow_vars, context)
            write_path(context, key, rendered_var)
            workflow_vars[var_name] = rendered_var
            # DEBUG
            # print('updated var', var_name, rendered_var)
            continue
        if key in (primary_key,) or key.endswith("_key"):
            continue
        if key in result:
            if primary_is_mapping:
                stored_obj[key] = result[key]  # type: ignore[index]
                if key in workflow_vars:
                    workflow_vars[key] = result[key]
            continue
        rendered_extra = render_string(template, workflow_vars, context)
        if primary_is_mapping:
            stored_obj[key] = rendered_extra  # type: ignore[index]
        if key in workflow_vars:
            workflow_vars[key] = rendered_extra

    if primary_is_mapping:
        for var_name in list(workflow_vars.keys()):
            if var_name in stored_obj:
                workflow_vars[var_name] = stored_obj[var_name]

    return {"status": "completed", "outputs": list(result.keys())}


def _resolve_input_value(value: JsonValue, context: MutableMapping[str, JsonValue]) -> JsonValue:
    if isinstance(value, str):
        try:
            return resolve_path(context, value, raise_on_missing=True)
        except PathResolutionError:
            return value
    return value


def _sync_outputs_on_skip(
    node: ExecuteNode,
    workflow_vars: MutableMapping[str, JsonValue],
    context: MutableMapping[str, JsonValue],
) -> None:
    for key, template in node.outputs.items():
        rendered = render_string(template, workflow_vars, context)
        if key.startswith("vars."):
            var_name = key.split(".", 1)[1]
            workflow_vars[var_name] = rendered
        elif key in workflow_vars and isinstance(rendered, str):
            try:
                workflow_vars[key] = resolve_path(context, rendered, raise_on_missing=True)
            except PathResolutionError:
                workflow_vars[key] = rendered
