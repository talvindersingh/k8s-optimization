"""Validation analyzer node powered by an OpenAI Agent with the validator MCP."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from agents import Agent, Runner
from agents.mcp import MCPServerStdio
from openai import APIConnectionError, OpenAIError

from interfaces import ExecutableNode, JsonValue

PROMPT_PATH = Path(__file__).with_name("kubernetes_validation_analyzer_prompt.txt")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = "gpt-5"
DEFAULT_MAX_TURNS = 10
DEFAULT_VALIDATOR_PYTHON = Path(__file__).resolve().parents[1] / ".venv-automation" / "bin" / "python"
DEFAULT_VALIDATOR_SERVER = Path(__file__).resolve().parents[1] / "automation" / "validator_mcp_server.py"


class KubernetesValidationAnalyzerNode(ExecutableNode):
    """Executable node that invokes the validator MCP for Kubernetes manifest checks."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        max_turns: int = DEFAULT_MAX_TURNS,
        validator_python: Optional[str] = None,
        validator_server: Optional[str] = None,
    ) -> None:
        self.model = model
        self.max_turns = max_turns
        self.validator_python = Path(validator_python) if validator_python else DEFAULT_VALIDATOR_PYTHON
        self.validator_server = Path(validator_server) if validator_server else DEFAULT_VALIDATOR_SERVER

    async def evaluate(self, context: Dict[str, JsonValue], **params: JsonValue) -> Dict[str, JsonValue]:
        instruction = _coalesce_param("instruction", context, params, fallback_key="instruction")
        manifest = _resolve_manifest_content(context, params)
        if not manifest.strip():
            raise ValueError("manifest content must be provided via inputs or context.")

        raw_output = await _run_agent(
            instruction=instruction,
            manifest_yaml=manifest,
            model=str(params.get("model") or self.model),
            max_turns=int(params.get("max_turns") or self.max_turns),
            validator_python=self.validator_python,
            validator_server=self.validator_server,
        )

        parsed = _parse_agent_output(raw_output)
        return {"result": parsed}


def _coalesce_param(
    name: str,
    context: Mapping[str, JsonValue],
    params: Mapping[str, JsonValue],
    *,
    fallback_key: str | None = None,
) -> str:
    value = params.get(name)
    if isinstance(value, str) and value.strip():
        return value

    if fallback_key:
        fallback = context.get(fallback_key)
        if isinstance(fallback, str) and fallback.strip():
            return fallback

    return ""


def _resolve_manifest_content(context: Mapping[str, JsonValue], params: Mapping[str, JsonValue]) -> str:
    manifest = params.get("manifest")
    if isinstance(manifest, str) and manifest.strip():
        return manifest

    vars_section = context.get("vars")
    if isinstance(vars_section, Mapping):
        key = vars_section.get("latest_manifest_key") or vars_section.get("latest_code_key")
        if isinstance(key, str) and key.strip():
            value = _lookup_path(context, key.strip())
            if isinstance(value, str) and value.strip():
                return value

    original = context.get("original_manifest") or context.get("original_code")
    if isinstance(original, str):
        return original
    return ""


def _lookup_path(context: Mapping[str, JsonValue], path: str) -> Optional[JsonValue]:
    parts = path.split(".")
    current: JsonValue = context
    for part in parts:
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            return None
    return current


def _load_prompt() -> str:
    if not PROMPT_PATH.exists():
        raise FileNotFoundError(f"Kubernetes validation analyzer prompt not found: {PROMPT_PATH}")
    return PROMPT_PATH.read_text(encoding="utf-8")


async def _run_agent(
    *,
    instruction: str,
    manifest_yaml: str,
    model: str,
    max_turns: int,
    validator_python: Path,
    validator_server: Path,
) -> str:
    if not validator_python.exists():
        raise FileNotFoundError(f"Validator python interpreter not found: {validator_python}")
    if not validator_server.exists():
        raise FileNotFoundError(f"Validator MCP server script not found: {validator_server}")

    prompt = _load_prompt()
    agent = Agent(
        name="kubernetes_validation_analyzer",
        instructions=prompt,
        model=model,
    )

    message_lines = [
        "Run objective validations on the provided Kubernetes manifest YAML using the validator MCP tool.",
        "",
        "Manifest YAML string:",
        manifest_yaml if manifest_yaml.endswith("\n") else manifest_yaml + "\n",
    ]
    if instruction.strip():
        message_lines.extend(
            [
                "",
                "Instruction context:",
                instruction.strip(),
            ]
        )
    message_lines.extend(
        [
            "",
            "Respond ONLY with the JSON object schema defined in your system prompt.",
        ]
    )
    message = "\n".join(message_lines)

    validator_command = {
        "command": str(validator_python),
        "args": [str(validator_server)],
    }

    original_cwd = Path.cwd()
    os.chdir(PROJECT_ROOT)
    try:
        async with MCPServerStdio(
            name="validator",
            params=validator_command,
            client_session_timeout_seconds=6000,
        ) as validator_srv:
            agent.mcp_servers = [validator_srv]
            try:
                run_result = await Runner.run(agent, message, max_turns=max_turns)
            except (APIConnectionError, OpenAIError) as exc:
                raise RuntimeError(
                    f"Validation analyzer failed to contact OpenAI: {exc.__class__.__name__}: {exc}"
                ) from exc
    finally:
        os.chdir(original_cwd)

    output_text = run_result.final_output.strip()
    if not output_text:
        raise RuntimeError("Validation analyzer agent returned empty output.")
    return output_text


def _parse_agent_output(output_text: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(output_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Agent output is not valid JSON: {output_text}") from exc

    required_keys = {"objective_validations", "result_analysis", "manifest_fix_required", "validations_result"}
    missing = required_keys - parsed.keys()
    if missing:
        raise ValueError(f"Agent output missing keys: {sorted(missing)}")

    objective = parsed["objective_validations"]
    if not isinstance(objective, Mapping):
        raise TypeError("objective_validations must be a mapping.")

    for section in ("kubconform", "kube-linter"):
        if section not in objective:
            raise ValueError(f"objective_validations missing '{section}' block.")
        block = objective[section]
        if not isinstance(block, Mapping):
            raise TypeError(f"{section} block must be a mapping.")
        if "result" not in block or "messages" not in block:
            raise ValueError(f"{section} block missing required fields.")

    return parsed


async def evaluate(context: Dict[str, JsonValue], **params: JsonValue) -> Dict[str, JsonValue]:
    """Module-level entry point to satisfy the ExecutableNode protocol."""
    node = KubernetesValidationAnalyzerNode()
    return await node.evaluate(context, **params)


__all__ = ["KubernetesValidationAnalyzerNode", "evaluate"]

# Backwards compatibility for legacy imports
AnsibleValidationAnalyzerNode = KubernetesValidationAnalyzerNode
