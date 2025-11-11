#!/usr/bin/env python3
"""Automation for running kubconform and kube-linter validations on Kubernetes manifests."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KUBECONFORM_BIN = os.environ.get("KUBECONFORM_BIN", "kubconform")
DEFAULT_KUBE_LINTER_BIN = os.environ.get("KUBE_LINTER_BIN", "kube-linter")
ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-9;?]*[A-Za-z]")
MAX_MESSAGE_LINES = 40
SUMMARY_SNIPPET_LIMIT = 800


class AutomationError(RuntimeError):
    """Raised when validation automation encounters an unrecoverable error."""


@dataclass
class CommandResult:
    command: Sequence[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def output(self) -> str:
        return f"{self.stdout}{self.stderr}"

    def cleaned_output(self) -> str:
        return ANSI_ESCAPE_RE.sub("", self.output)


@dataclass
class StepResult:
    name: str
    result: str  # pass | fail | skipped | mocked
    messages: List[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run kubconform and kube-linter validations against a Kubernetes manifest.",
    )
    parser.add_argument(
        "manifest",
        help="Path to the manifest file to validate.",
    )
    parser.add_argument(
        "--name",
        help="Friendly name for the manifest in reports (defaults to manifest path).",
    )
    parser.add_argument(
        "--kubconform-bin",
        default=DEFAULT_KUBECONFORM_BIN,
        help="kubconform executable to invoke (default: %(default)s).",
    )
    parser.add_argument(
        "--kube-linter-bin",
        default=DEFAULT_KUBE_LINTER_BIN,
        help="kube-linter executable to invoke (default: %(default)s).",
    )
    parser.add_argument(
        "--kubconform-args",
        default="",
        help="Extra arguments to pass to kubconform (parsed with shlex.split).",
    )
    parser.add_argument(
        "--kube-linter-args",
        default="",
        help="Extra arguments to pass to kube-linter (parsed with shlex.split).",
    )
    return parser.parse_args()


def _split_additional_args(raw: str) -> List[str]:
    return shlex.split(raw) if raw.strip() else []


def ensure_manifest_exists(path: Path) -> Path:
    if not path.exists():
        raise AutomationError(f"Manifest not found: {path}")
    if path.is_dir():
        raise AutomationError(f"Manifest path points to a directory: {path}")
    return path


def run_command(command: Sequence[str], *, cwd: Optional[Path] = None) -> CommandResult:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise AutomationError(f"Command not found: {command[0]}") from exc
    return CommandResult(command=command, returncode=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)


def _extract_messages(output: str) -> List[str]:
    cleaned = ANSI_ESCAPE_RE.sub("", output)
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if not lines:
        return []
    if len(lines) <= MAX_MESSAGE_LINES:
        return lines
    head = lines[: MAX_MESSAGE_LINES // 2]
    tail = lines[-(MAX_MESSAGE_LINES // 2) :]
    return head + ["... (output truncated) ..."] + tail


def run_kubconform(manifest: Path, executable: str, extra_args: Sequence[str]) -> StepResult:
    command = [executable, "-summary", *extra_args, str(manifest)]
    try:
        result = run_command(command)
    except AutomationError as exc:
        return StepResult("kubconform", "fail", [str(exc)])

    status = "pass" if result.ok else "fail"
    messages = _extract_messages(result.cleaned_output())
    if not messages:
        messages = ["kubconform reported no findings."]
    return StepResult("kubconform", status, messages)


def run_kube_linter(manifest: Path, executable: str, extra_args: Sequence[str]) -> StepResult:
    command = [executable, "lint", *extra_args, str(manifest)]
    try:
        result = run_command(command)
    except AutomationError as exc:
        return StepResult("kube-linter", "fail", [str(exc)])

    status = "pass" if result.ok else "fail"
    messages = _extract_messages(result.cleaned_output())
    if not messages:
        messages = ["kube-linter reported no findings."]
    return StepResult("kube-linter", status, messages)


def determine_overall(step_results: Iterable[StepResult]) -> str:
    results = [step.result for step in step_results]
    if any(result == "fail" for result in results):
        return "fail"
    if results and all(result == "pass" for result in results):
        return "pass"
    if results and all(result in {"pass", "skipped", "mocked"} for result in results):
        return "pass"
    if any(result == "skipped" for result in results):
        return "skipped"
    return "unknown"


def summarize(step: StepResult) -> str:
    headline = f"{step.name}: {step.result}"
    if step.messages:
        first = step.messages[0]
        snippet = first if len(first) <= SUMMARY_SNIPPET_LIMIT else first[:SUMMARY_SNIPPET_LIMIT] + "..."
        return f"{headline} - {snippet}"
    return headline


def build_report(
    *,
    manifest: Path,
    friendly_name: Optional[str],
    kubconform_step: StepResult,
    kube_linter_step: StepResult,
) -> dict:
    step_blocks = {
        "kubconform": {
            "result": kubconform_step.result,
            "messages": kubconform_step.messages,
        },
        "kube-linter": {
            "result": kube_linter_step.result,
            "messages": kube_linter_step.messages,
        },
    }

    steps = [kubconform_step, kube_linter_step]
    overall_result = determine_overall(steps)
    overall_messages = [summarize(step) for step in steps]

    try:
        rel_path = str(manifest.relative_to(PROJECT_ROOT))
    except ValueError:
        rel_path = os.path.relpath(manifest, PROJECT_ROOT)

    return {
        "file_under_test": friendly_name or rel_path,
        "overall_result": overall_result,
        "overall_messages": overall_messages,
        **step_blocks,
    }


def main() -> int:
    args = parse_args()
    manifest_path = ensure_manifest_exists(Path(args.manifest).expanduser().resolve())
    kubconform_args = _split_additional_args(args.kubconform_args)
    kube_linter_args = _split_additional_args(args.kube_linter_args)

    kubconform_step = run_kubconform(manifest_path, args.kubconform_bin, kubconform_args)
    kube_linter_step = run_kube_linter(manifest_path, args.kube_linter_bin, kube_linter_args)

    report = build_report(
        manifest=manifest_path,
        friendly_name=args.name,
        kubconform_step=kubconform_step,
        kube_linter_step=kube_linter_step,
    )

    print("\nValidation summary:")
    print(f"  kubconform: {kubconform_step.result}")
    for line in kubconform_step.messages:
        print(f"    - {line}")
    print(f"  kube-linter: {kube_linter_step.result}")
    for line in kube_linter_step.messages:
        print(f"    - {line}")

    payload = {"objective_validations": report}
    print("\n" + json.dumps(payload, indent=2))

    return 0 if report["overall_result"] == "pass" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AutomationError as error:
        raise SystemExit(f"ERROR: {error}")
