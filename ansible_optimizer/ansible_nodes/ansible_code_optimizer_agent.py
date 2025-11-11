"""Kubernetes manifest optimizer node that leverages the Codex CLI MCP tool."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, MutableMapping

from agents import Agent, Runner
from agents.mcp import MCPServerStdio
from openai import APIConnectionError, OpenAIError

from interfaces import ExecutableNode, JsonValue

PROMPT_PATH = Path(__file__).with_name("kubernetes_manifest_optimizer_prompt.txt")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CODEX_HOME = PROJECT_ROOT / ".codex-home"
DEFAULT_MODEL = "gpt-5"
DEFAULT_MAX_TURNS = 20


class KubernetesManifestOptimizerNode(ExecutableNode):
    """Executable node that generates an improved Kubernetes manifest."""

    def __init__(self, *, model: str = DEFAULT_MODEL, max_turns: int = DEFAULT_MAX_TURNS) -> None:
        self.model = model
        self.max_turns = max_turns

    async def evaluate(self, context: Dict[str, JsonValue], **params: JsonValue) -> Dict[str, JsonValue]:
        instruction = _coalesce_param("instruction", context, params, fallback_key="instruction")
        before_code = _coalesce_param(
            "before_manifest",
            context,
            params,
            fallback_key="latest_manifest",
        ) or _coalesce_param("before_code", context, params, fallback_key="original_code")
        _validate_text("instruction", instruction)
        _validate_text("before_code", before_code)

        feedback = _resolve_feedback(context, params)
        if feedback is None or not isinstance(feedback, Mapping):
            raise ValueError("feedback must be provided as a mapping of evaluation insights.")

        model = str(params.get("model") or self.model)
        max_turns = int(params.get("max_turns") or self.max_turns)

        transformed = await _run_optimizer(
            instruction=instruction,
            current_code=before_code,
            feedback=feedback,
            model=model,
            max_turns=max_turns,
        )

        return {"result": transformed}


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


def _validate_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string.")
    placeholder_keys = {"instruction", "original_code", "before_code", "before_manifest", "code"}
    if value.strip() in placeholder_keys and len(value.strip()) <= 18:
        raise ValueError(f"{name} appears to be unresolved template placeholder ('{value}').")
    if value.strip().startswith("optimization_flow"):
        raise ValueError(f"{name} appears to reference a path ('{value}'); expected resolved value.")


def _resolve_feedback(context: Mapping[str, JsonValue], params: Mapping[str, JsonValue]) -> Mapping[str, Any] | None:
    raw_feedback = params.get("feedback")
    if isinstance(raw_feedback, Mapping):
        return raw_feedback

    feedback_path = params.get("feedback_path")
    if isinstance(feedback_path, str) and feedback_path.strip():
        resolved = _resolve_path(context, feedback_path.strip())
        if isinstance(resolved, Mapping):
            return resolved
    return None


def _resolve_path(context: Mapping[str, JsonValue], dotted_path: str) -> Any:
    current: Any = context
    for part in dotted_path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            return None
    return current


def _load_prompt() -> str:
    if not PROMPT_PATH.exists():
        raise FileNotFoundError(f"Kubernetes manifest optimizer prompt not found: {PROMPT_PATH}")
    return PROMPT_PATH.read_text(encoding="utf-8")


def _ensure_codex_home() -> Path:
    codex_home_env = os.environ.get("CODEX_HOME")
    if codex_home_env:
        return Path(codex_home_env)

    DEFAULT_CODEX_HOME.mkdir(parents=True, exist_ok=True)
    os.environ["CODEX_HOME"] = str(DEFAULT_CODEX_HOME)
    return DEFAULT_CODEX_HOME


async def _run_optimizer(
    *,
    instruction: str,
    current_code: str,
    feedback: Mapping[str, Any] | None,
    model: str,
    max_turns: int,
) -> Dict[str, JsonValue]:
    _ensure_codex_home()

    prompt = _load_prompt()
    agent = Agent(
        name="kubernetes_manifest_optimizer",
        instructions=prompt,
        model=model,
    )

    message_sections = [
        "Improve the following Kubernetes manifest or collection of manifests per the instruction.",
        "",
        "Instruction:",
        instruction.strip(),
        "",
        "Current Kubernetes manifest YAML:",
        current_code.strip()
    ]
    if feedback:
        message_sections.extend(
            [
                "",
                "Strictly based on this Evaluation Feedback (JSON):",
                json.dumps(feedback, indent=2, ensure_ascii=False),
            ]
        )
    message_sections.append("")
    message_sections.append("Return only the JSON response defined in your system prompt.")
    message = "\n".join(message_sections)

    codex_command = {
        "command": "npx",
        "args": ["-y", "codex", "mcp-server"],
    }

    original_cwd = Path.cwd()
    os.chdir(PROJECT_ROOT)
    try:
        async with MCPServerStdio(
            name="codex-cli",
            params=codex_command,
            client_session_timeout_seconds=6000,
        ) as codex_server:
            agent.mcp_servers = [codex_server]
            try:
                run_result = await Runner.run(agent, message, max_turns=max_turns)
            except (APIConnectionError, OpenAIError) as exc:
                raise RuntimeError(
                    f"Kubernetes manifest optimizer failed to contact OpenAI: {exc.__class__.__name__}: {exc}"
                ) from exc
    finally:
        os.chdir(original_cwd)

    output_text = run_result.final_output.strip()
    if not output_text:
        raise RuntimeError("Kubernetes manifest optimizer agent returned empty output.")

    try:
        parsed = json.loads(output_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Optimizer output is not valid JSON: {output_text}") from exc

    if "improved_code" not in parsed:
        raise ValueError("Optimizer output missing 'improved_code'.")

    improved_code = str(parsed["improved_code"])
    if not improved_code.endswith("\n"):
        improved_code = f"{improved_code}\n"

    rationale = parsed.get("rationale", "")
    if not isinstance(rationale, str):
        raise TypeError("Optimizer 'rationale' must be a string.")

    timestamp = datetime.now(timezone.utc).isoformat()
    return {
        "code": improved_code,
        "rationale": rationale.strip(),
        "transformed_at": timestamp,
        "model": model,
    }


async def evaluate(context: Dict[str, JsonValue], **params: JsonValue) -> Dict[str, JsonValue]:
    """Module-level entrypoint compatible with ExecutableNode."""
    node = KubernetesManifestOptimizerNode()
    return await node.evaluate(context, **params)


__all__ = [
    "KubernetesManifestOptimizerNode",
    "evaluate",
]

# Backwards compatibility for any legacy imports
AnsibleCodeOptimizerNode = KubernetesManifestOptimizerNode
