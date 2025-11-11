"""Subjective evaluation node for Kubernetes manifests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping

from agents import Agent, Runner
from openai import APIConnectionError, OpenAIError

from interfaces import ExecutableNode, JsonValue

PROMPT_PATH = Path(__file__).with_name("kubernetes_subjective_evaluator_prompt.txt")
DEFAULT_MODEL = "gpt-5"
DEFAULT_MAX_TURNS = 10

METRIC_WEIGHTS: Dict[str, float] = {
    "schema_compliance": 0.25,
    "resource_configuration": 0.25,
    "security_posture": 0.20,
    "operational_resilience": 0.20,
    "best_practice_alignment": 0.10,
}


class SubjectiveEvaluatorNode(ExecutableNode):
    """Executable node that runs the subjective evaluation agent."""

    def __init__(self, *, model: str = DEFAULT_MODEL, max_turns: int = DEFAULT_MAX_TURNS) -> None:
        self.model = model
        self.max_turns = max_turns

    async def evaluate(self, context: Dict[str, JsonValue], **params: JsonValue) -> Dict[str, JsonValue]:
        instruction = _coalesce_param("instruction", context, params, fallback_key="instruction")
        code = _coalesce_param(
            "manifest",
            context,
            params,
            fallback_key="latest_manifest",
        ) or _coalesce_param("code", context, params, fallback_key="original_code")

        _validate_text("instruction", instruction)
        _validate_text("code", code)

        agent_payload = await _run_agent(
            instruction=instruction,
            manifest_yaml=code,
            model=str(params.get("model") or self.model),
            max_turns=int(params.get("max_turns") or self.max_turns),
        )

        evaluation = _extract_evaluation(agent_payload)
        weighted_score = _compute_weighted_score(evaluation, METRIC_WEIGHTS)
        evaluation["weighted_overall_score"] = weighted_score

        result = {
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "scores": evaluation,
        }
        return {"result": result}


def _coalesce_param(
    name: str,
    context: Mapping[str, JsonValue],
    params: Mapping[str, JsonValue],
    *,
    fallback_key: str | None = None,
) -> str:
    """Return string parameter from params or context."""
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
    placeholder_keys = {"instruction", "original_code", "code", "manifest", "latest_manifest", "latest_code_key"}
    if value.strip() in placeholder_keys and len(value.strip()) <= 18:
        raise ValueError(f"{name} appears to be unresolved template placeholder ('{value}').")
    if value.strip().startswith("optimization_flow"):
        raise ValueError(f"{name} appears to reference a path ('{value}'); expected resolved value.")


def _load_prompt() -> str:
    if not PROMPT_PATH.exists():
        raise FileNotFoundError(f"Kubernetes subjective evaluator prompt not found: {PROMPT_PATH}")
    return PROMPT_PATH.read_text(encoding="utf-8")


def _build_agent(model: str) -> Agent:
    return Agent(
        name="kubernetes_manifest_subjective_evaluator",
        instructions=_load_prompt(),
        model=model,
    )


async def _run_agent(
    *,
    instruction: str,
    manifest_yaml: str,
    model: str,
    max_turns: int,
) -> Dict[str, Any]:
    """Invoke the OpenAI agent and parse its JSON response."""
    agent = _build_agent(model=model)
    user_prompt = (
        "Evaluate the Kubernetes manifest against the instruction.\n\n"
        f"Instruction:\n{instruction.strip()}\n\n"
        "Manifest YAML:\n```yaml\n"
        f"{manifest_yaml.strip()}\n"
        "```\n\n"
        "Respond with the required JSON schema only."
    )

    try:
        result = await Runner.run(agent, user_prompt, max_turns=max_turns)
    except (APIConnectionError, OpenAIError) as exc:
        raise RuntimeError(
            f"Subjective evaluator failed to contact OpenAI: {exc.__class__.__name__}: {exc}"
        ) from exc
    output_text = result.final_output.strip()
    if not output_text:
        raise RuntimeError("Subjective evaluator agent returned empty output.")

    try:
        parsed = json.loads(output_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Agent output is not valid JSON: {output_text}") from exc

    return parsed


def _extract_evaluation(agent_payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Extract the subjective evaluation dictionary from the agent payload."""
    if "subjective_evaluation" not in agent_payload:
        raise ValueError("Agent output missing 'subjective_evaluation' key.")
    evaluation = agent_payload["subjective_evaluation"]
    if not isinstance(evaluation, Mapping):
        raise TypeError("'subjective_evaluation' must be a mapping.")

    evaluation_dict = {}
    for metric in METRIC_WEIGHTS:
        metric_value = evaluation.get(metric)
        if not isinstance(metric_value, Mapping):
            raise ValueError(f"Metric '{metric}' missing or not an object.")
        score = metric_value.get("score")
        reason = metric_value.get("reason")
        if not isinstance(score, int):
            raise TypeError(f"Metric '{metric}' score must be an integer.")
        if score < 0 or score > 3:
            raise ValueError(f"Metric '{metric}' score must be between 0 and 3.")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"Metric '{metric}' reason must be a non-empty string.")

        evaluation_dict[metric] = {
            "score": score,
            "reason": reason.strip(),
        }

    return evaluation_dict


def _compute_weighted_score(
    evaluation: Mapping[str, Mapping[str, Any]],
    weights: Mapping[str, float],
) -> float:
    """Calculate the weighted overall score (0-1)."""
    total = 0.0
    for metric, weight in weights.items():
        metric_entry = evaluation.get(metric)
        if not metric_entry:
            raise ValueError(f"Metric '{metric}' missing in evaluation.")
        score = metric_entry.get("score")
        if not isinstance(score, int):
            raise TypeError(f"Metric '{metric}' score must be an integer.")
        total += (score / 3.0) * weight

    # Clamp to [0, 1] to protect against tiny floating point deviations.
    return max(0.0, min(round(total, 4), 1.0))


async def evaluate(context: Dict[str, JsonValue], **params: JsonValue) -> Dict[str, JsonValue]:
    """Convenience module-level entrypoint that satisfies the ExecutableNode protocol."""
    node = SubjectiveEvaluatorNode()
    return await node.evaluate(context, **params)


__all__ = [
    "SubjectiveEvaluatorNode",
    "evaluate",
]
