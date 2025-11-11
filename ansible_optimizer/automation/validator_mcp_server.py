#!/usr/bin/env python3
"""
MCP server exposing Kubernetes manifest validation as a tool.

This server wraps run_validations.py to offer kubconform and kube-linter checks
through a Model Context Protocol tool.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent

AUTOMATION_ROOT = Path(__file__).resolve().parent
RUN_VALIDATIONS = AUTOMATION_ROOT / "run_validations.py"
SERVER_NAME = "kubernetes-validator"
SERVER_VERSION = "0.1.0"

app = Server(SERVER_NAME)


def _normalise_extra_args(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(str(item) for item in value if str(item).strip())
    raise ValueError("Extra arguments must be a string or list of strings.")


def _extract_validation_payload(stdout: str) -> tuple[Dict[str, Any], Optional[str]]:
    marker = '"objective_validations"'
    marker_index = stdout.rfind(marker)
    if marker_index == -1:
        return {}, "objective_validations JSON not found in stdout."

    start_index = stdout.rfind("{", 0, marker_index)
    if start_index == -1:
        return {}, "Unable to locate start of JSON payload."

    text = stdout[start_index:]
    depth = 0
    for idx, char in enumerate(text):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                json_blob = text[: idx + 1]
                try:
                    data = json.loads(json_blob)
                    return data, None
                except json.JSONDecodeError as exc:
                    return {}, f"Failed to parse validation JSON: {exc}"
    return {}, "Incomplete JSON payload in stdout."


async def _run_validations(
    manifest_path: Path,
    *,
    friendly_name: Optional[str],
    kubconform_bin: Optional[str],
    kube_linter_bin: Optional[str],
    kubconform_args: str,
    kube_linter_args: str,
) -> Dict[str, Any]:
    command = [sys.executable, str(RUN_VALIDATIONS), str(manifest_path)]
    if friendly_name:
        command.extend(["--name", friendly_name])
    if kubconform_bin:
        command.extend(["--kubconform-bin", kubconform_bin])
    if kube_linter_bin:
        command.extend(["--kube-linter-bin", kube_linter_bin])
    if kubconform_args:
        command.extend(["--kubconform-args", kubconform_args])
    if kube_linter_args:
        command.extend(["--kube-linter-args", kube_linter_args])

    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(AUTOMATION_ROOT.parent),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await process.communicate()
    stdout = stdout_bytes.decode("utf-8", "replace")
    stderr = stderr_bytes.decode("utf-8", "replace")

    payload, error = _extract_validation_payload(stdout)
    if error:
        raise RuntimeError(
            json.dumps(
                {
                    "error": "validation_output_unavailable",
                    "message": error,
                    "stdout": stdout.strip(),
                    "stderr": stderr.strip(),
                    "returncode": process.returncode,
                },
                indent=2,
            )
        )
    return payload


def _write_temp_manifest(content: str) -> Path:
    cleaned = content.replace("\\n", "\n")
    if not cleaned.endswith("\n"):
        cleaned += "\n"
    temp_file = tempfile.NamedTemporaryFile("w", delete=False, suffix=".yaml", encoding="utf-8")
    with temp_file as handle:
        handle.write(cleaned)
    return Path(temp_file.name)


@app.tool(
    name="validate_manifest",
    description="Run kubconform and kube-linter validations on a Kubernetes manifest.",
)
async def validate_manifest(arguments: Dict[str, Any]) -> list[TextContent]:
    manifest_path_arg = arguments.get("manifest_path")
    manifest_content = arguments.get("manifest_content")
    if not manifest_path_arg and not manifest_content:
        raise ValueError("Provide either manifest_path or manifest_content.")

    friendly_name = arguments.get("name") or arguments.get("manifest_name")
    kubconform_bin = arguments.get("kubconform_bin")
    kube_linter_bin = arguments.get("kube_linter_bin")
    kubconform_args = _normalise_extra_args(arguments.get("kubconform_args"))
    kube_linter_args = _normalise_extra_args(arguments.get("kube_linter_args"))

    temp_manifest: Optional[Path] = None
    try:
        if manifest_content:
            temp_manifest = _write_temp_manifest(str(manifest_content))
            manifest_path = temp_manifest
        else:
            manifest_path = Path(str(manifest_path_arg)).expanduser().resolve()
            if not manifest_path.exists():
                raise FileNotFoundError(f"Manifest not found: {manifest_path}")
        payload = await _run_validations(
            manifest_path,
            friendly_name=friendly_name,
            kubconform_bin=str(kubconform_bin) if kubconform_bin else None,
            kube_linter_bin=str(kube_linter_bin) if kube_linter_bin else None,
            kubconform_args=kubconform_args,
            kube_linter_args=kube_linter_args,
        )
    except Exception as exc:
        error_result = {
            "error": "validation_failed",
            "message": str(exc),
            "manifest_path": str(manifest_path_arg) if manifest_path_arg else "(inline content)",
        }
        return [
            TextContent(
                type="text",
                text=json.dumps(error_result, indent=2),
            )
        ]
    finally:
        if temp_manifest and temp_manifest.exists():
            try:
                temp_manifest.unlink()
            except OSError:
                pass

    return [
        TextContent(
            type="text",
            text=json.dumps(payload, indent=2),
        )
    ]


async def main() -> None:
    if not RUN_VALIDATIONS.exists():
        print(
            f"ERROR: run_validations.py not found at {RUN_VALIDATIONS}",
            file=sys.stderr,
        )
        sys.exit(1)

    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(version=SERVER_VERSION),
        )


if __name__ == "__main__":
    asyncio.run(main())
