#!/usr/bin/env python3
"""Basic MCP server validation tests for the Kubernetes validator."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

TESTS_DIR = Path(__file__).resolve().parent
AUTOMATION_ROOT = TESTS_DIR.parent
PACKAGE_ROOT = AUTOMATION_ROOT.parent
VENV_PYTHON = PACKAGE_ROOT / ".venv-automation" / "bin" / "python"
MCP_SERVER_SCRIPT = AUTOMATION_ROOT / "validator_mcp_server.py"

SAMPLE_MANIFEST = """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: mcp-basic-test
  namespace: default
data:
  message: hello
"""


def _check_prerequisites() -> bool:
    if not MCP_SERVER_SCRIPT.exists():
        print(f"[FAIL] MCP server script not found: {MCP_SERVER_SCRIPT}")
        return False
    if not VENV_PYTHON.exists():
        print(f"[FAIL] Automation virtualenv interpreter not found: {VENV_PYTHON}")
        return False
    kubconform_bin = shutil.which("kubconform") or os.environ.get("KUBECONFORM_BIN")
    kube_linter_bin = shutil.which("kube-linter") or os.environ.get("KUBE_LINTER_BIN")
    if not kubconform_bin:
        print("[FAIL] kubconform binary not found on PATH. Set KUBECONFORM_BIN if installed elsewhere.")
        return False
    if not kube_linter_bin:
        print("[FAIL] kube-linter binary not found on PATH. Set KUBE_LINTER_BIN if installed elsewhere.")
        return False
    return True


async def _run_server_interaction(callback) -> Dict[str, any]:
    params = StdioServerParameters(
        command=str(VENV_PYTHON),
        args=[str(MCP_SERVER_SCRIPT)],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await callback(session)


async def test_server_initialization() -> bool:
    print("== Test 1: Server initialization ==")

    async def body(session: ClientSession) -> bool:
        info = session.connection_info
        print(f"[OK] Connected to {info.clientInfo.name if info and info.clientInfo else 'client'}")
        return True

    try:
        await _run_server_interaction(body)
        return True
    except Exception as exc:  # pragma: no cover - best effort diagnostics
        print(f"[FAIL] Initialization failed: {exc}")
        return False


async def test_list_tools() -> bool:
    print("\n== Test 2: List tools ==")

    async def body(session: ClientSession) -> bool:
        tools = await session.list_tools()
        names = [tool.name for tool in tools.tools]
        print(f"[OK] Tools discovered: {names}")
        return "validate_manifest" in names

    try:
        result = await _run_server_interaction(body)
        if result:
            print("[OK] validate_manifest tool present")
        else:
            print("[FAIL] validate_manifest tool missing")
        return result
    except Exception as exc:
        print(f"[FAIL] list_tools failed: {exc}")
        return False


async def test_validate_manifest_with_inline() -> bool:
    print("\n== Test 3: validate_manifest with inline content ==")

    async def body(session: ClientSession) -> bool:
        response = await session.call_tool(
            "validate_manifest",
            arguments={"manifest_content": SAMPLE_MANIFEST},
        )
        if not response.content:
            print("[FAIL] No content returned from tool call.")
            return False
        payload = json.loads(response.content[0].text)
        print("[OK] Received payload keys:", list(payload.keys()))
        return "objective_validations" in payload

    try:
        return await _run_server_interaction(body)
    except Exception as exc:
        print(f"[FAIL] validate_manifest inline call failed: {exc}")
        return False


async def test_validate_manifest_with_path() -> bool:
    print("\n== Test 4: validate_manifest with manifest path ==")
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as handle:
        handle.write(SAMPLE_MANIFEST)
        manifest_path = Path(handle.name)

    async def body(session: ClientSession) -> bool:
        response = await session.call_tool(
            "validate_manifest",
            arguments={"manifest_path": str(manifest_path)},
        )
        payload = json.loads(response.content[0].text)
        print("[OK] validate_manifest returned payload with keys:", list(payload.keys()))
        return "objective_validations" in payload

    try:
        result = await _run_server_interaction(body)
    finally:
        try:
            manifest_path.unlink()
        except FileNotFoundError:
            pass
    return result


async def main() -> int:
    if not _check_prerequisites():
        return 1

    results = {
        "initialization": await test_server_initialization(),
        "list_tools": await test_list_tools(),
        "inline": await test_validate_manifest_with_inline(),
        "path": await test_validate_manifest_with_path(),
    }

    print("\n== Summary ==")
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"{status} - {name}")

    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
    except KeyboardInterrupt:  # pragma: no cover
        exit_code = 1
    sys.exit(exit_code)
