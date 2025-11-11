#!/usr/bin/env python3
"""End-to-end validation of the Kubernetes MCP validator tool output schema."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

TESTS_DIR = Path(__file__).resolve().parent
AUTOMATION_ROOT = TESTS_DIR.parent
PACKAGE_ROOT = AUTOMATION_ROOT.parent
VENV_PYTHON = PACKAGE_ROOT / ".venv-automation" / "bin" / "python"
MCP_SERVER_SCRIPT = AUTOMATION_ROOT / "validator_mcp_server.py"

VALID_MANIFEST = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: validator-sample
  namespace: default
spec:
  replicas: 1
  selector:
    matchLabels:
      app: validator-sample
  template:
    metadata:
      labels:
        app: validator-sample
    spec:
      containers:
        - name: web
          image: nginx:1.25
          ports:
            - containerPort: 80
      restartPolicy: Always
"""


def validate_objective_payload(payload: Dict[str, Any]) -> List[str]:
    errors: List[str] = []

    if "objective_validations" not in payload:
        errors.append("Missing objective_validations key.")
        return errors

    objective = payload["objective_validations"]
    required_top = {
        "file_under_test",
        "overall_result",
        "overall_messages",
        "kubconform",
        "kube-linter",
    }
    missing = required_top - objective.keys()
    if missing:
        errors.append(f"Missing objective fields: {sorted(missing)}")

    for step in ("kubconform", "kube-linter"):
        block = objective.get(step, {})
        if not isinstance(block, dict):
            errors.append(f"{step} block must be a dict.")
            continue
        for key in ("result", "messages"):
            if key not in block:
                errors.append(f"{step} missing '{key}'.")
        if "messages" in block and not isinstance(block["messages"], list):
            errors.append(f"{step}.messages must be a list.")

    return errors


async def invoke_validate_manifest(manifest: str) -> Dict[str, Any]:
    params = StdioServerParameters(
        command=str(VENV_PYTHON),
        args=[str(MCP_SERVER_SCRIPT)],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            response = await session.call_tool(
                "validate_manifest",
                arguments={"manifest_content": manifest},
            )
    if not response.content:
        raise RuntimeError("validate_manifest returned no content.")
    return json.loads(response.content[0].text)


async def run() -> int:
    for requirement, message in (
        (MCP_SERVER_SCRIPT.exists(), f"MCP server script missing: {MCP_SERVER_SCRIPT}"),
        (VENV_PYTHON.exists(), f"Automation interpreter missing: {VENV_PYTHON}"),
        (
            shutil.which(os.environ.get("KUBECONFORM_BIN", "kubconform")) is not None,
            "kubconform binary not found on PATH.",
        ),
        (
            shutil.which(os.environ.get("KUBE_LINTER_BIN", "kube-linter")) is not None,
            "kube-linter binary not found on PATH.",
        ),
    ):
        if not requirement:
            print(f"[SKIP] {message}")
            return 0

    print("Running validate_manifest end-to-end test...")
    payload = await invoke_validate_manifest(VALID_MANIFEST)
    errors = validate_objective_payload(payload)
    if errors:
        print("[FAIL] Validation payload did not match expected schema:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("[OK] Payload structure validated.")
    return 0


def main() -> None:
    try:
        code = asyncio.run(run())
    except KeyboardInterrupt:
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
