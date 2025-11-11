"""Stateless workflow engine."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from copy import deepcopy
from pathlib import Path
from typing import Dict, MutableMapping

from dotenv import load_dotenv

from interfaces import JsonValue

from workflow_orchestrator.conditional import (
    ConditionalEvaluationError,
    evaluate_conditional,
)
from workflow_orchestrator.executor import execute_node
from workflow_orchestrator.models import ConditionalNode, ExecuteNode, WorkflowConfig, load_workflow_config


class WorkflowExecutionError(RuntimeError):
    """Raised when workflow orchestration fails."""


async def execute_workflow(config: WorkflowConfig, context: MutableMapping[str, JsonValue]) -> MutableMapping[str, JsonValue]:
    """Execute a workflow definition against an in-memory context."""
    workflow_vars: Dict[str, JsonValue] = deepcopy(config.vars)
    stored_vars = context.get("vars")
    if isinstance(stored_vars, dict):
        workflow_vars.update(stored_vars)
    _rehydrate_counters(context, workflow_vars)
    node_index = {node.id: idx for idx, node in enumerate(config.flow)}

    current = 0
    while current < len(config.flow):
        node = config.flow[current]

        if isinstance(node, ExecuteNode):
            await execute_node(node, workflow_vars=workflow_vars, context=context)
            current += 1
            continue

        if isinstance(node, ConditionalNode):
            try:
                next_id = evaluate_conditional(node, workflow_vars=workflow_vars, context=context)
            except ConditionalEvaluationError as exc:  # pragma: no cover - defensive
                raise WorkflowExecutionError(f"Conditional node '{node.id}' failed: {exc}") from exc

            if next_id == "END":
                break

            if next_id not in node_index:
                raise WorkflowExecutionError(f"Conditional node '{node.id}' routed to unknown target '{next_id}'.")
            current = node_index[next_id]
            continue

        raise WorkflowExecutionError(f"Unsupported node type encountered: {node}")

    context.setdefault("vars", {})
    context["vars"].update(workflow_vars)
    return context


def _load_env_for_store(store_path: Path) -> None:
    """Load environment variables relative to the optimization_flow.json location."""
    candidates = [
        store_path.parent / ".env",
        store_path.parent.parent / ".env",
        store_path.parent.parent.parent / ".env",
    ]
    for env_path in candidates:
        if env_path.exists():
            load_dotenv(env_path, override=False)
            break


async def run_workflow_from_files(config_path: Path, store_path: Path) -> MutableMapping[str, JsonValue]:
    """Load workflow and context from disk, execute, and persist results."""
    _load_env_for_store(store_path)
    config = load_workflow_config(config_path)
    context: MutableMapping[str, JsonValue] = json.loads(store_path.read_text(encoding="utf-8"))
    await execute_workflow(config, context)
    store_path.write_text(json.dumps(context, indent=2), encoding="utf-8")
    return context


def run(config_path: Path | str, store_path: Path | str) -> MutableMapping[str, JsonValue]:
    """Synchronous entry point for CLI usage."""
    return asyncio.run(run_workflow_from_files(Path(config_path), Path(store_path)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Execute a workflow against an optimization_flow.json store.")
    parser.add_argument("config", type=Path, help="Path to workflow JSON definition.")
    parser.add_argument("store", type=Path, help="Path to optimization_flow.json.")
    args = parser.parse_args(argv)

    try:
        run(args.config, args.store)
    except Exception as exc:  # pragma: no cover - CLI convenience
        print(f"[engine] Error: {exc}", file=sys.stderr)
        return 1

    print("[engine] Workflow completed successfully.")
    return 0


def _rehydrate_counters(context: MutableMapping[str, JsonValue], workflow_vars: Dict[str, JsonValue]) -> None:
    optimization_flow = context.get("optimization_flow")
    if not isinstance(optimization_flow, dict):
        return

    subjective_indices = [
        int(key.rsplit("_", 1)[-1])
        for key in optimization_flow
        if key.startswith("subjective_evaluation_") and key.rsplit("_", 1)[-1].isdigit()
    ]
    if subjective_indices:
        last_subjective = max(subjective_indices)
        workflow_vars["last_subjective_iteration"] = last_subjective
        if workflow_vars.get("subjective_iteration_count", 0) <= last_subjective:
            workflow_vars["subjective_iteration_count"] = last_subjective + 1
    else:
        workflow_vars.setdefault("last_subjective_iteration", 0)

    improved_manifest_indices = [
        int(key.rsplit("B", 1)[-1])
        for key in optimization_flow
        if key.startswith("improved_manifest_B") and key.rsplit("B", 1)[-1].isdigit()
    ]
    improved_code_indices = [
        int(key.rsplit("B", 1)[-1])
        for key in optimization_flow
        if key.startswith("improved_code_B") and key.rsplit("B", 1)[-1].isdigit()
    ]

    if improved_manifest_indices:
        last_manifest = max(improved_manifest_indices)
        manifest_key = f"optimization_flow.improved_manifest_B{last_manifest}.code"
        workflow_vars["latest_manifest_key"] = manifest_key
        workflow_vars["latest_code_key"] = manifest_key
        if workflow_vars.get("code_iteration_count", 0) <= last_manifest:
            workflow_vars["code_iteration_count"] = last_manifest + 1
    elif improved_code_indices:
        last_code = max(improved_code_indices)
        code_key = f"optimization_flow.improved_code_B{last_code}.code"
        workflow_vars["latest_code_key"] = code_key
        workflow_vars.setdefault("latest_manifest_key", code_key)
        if workflow_vars.get("code_iteration_count", 0) <= last_code:
            workflow_vars["code_iteration_count"] = last_code + 1
    else:
        workflow_vars.setdefault("latest_manifest_key", workflow_vars.get("latest_code_key", "original_manifest"))
        workflow_vars.setdefault("latest_code_key", workflow_vars.get("latest_manifest_key", "original_code"))


__all__ = ["execute_workflow", "run_workflow_from_files", "run", "WorkflowExecutionError"]


if __name__ == "__main__":  # pragma: no cover - CLI
    sys.exit(main())
