#!/usr/bin/env bash
#
# workflow-batch-runner: Batch executor for workflow orchestrator pipelines.
#
# Usage:
#   ./workflow-batch-runner.sh [--workflow PATH] [--batch-size N] [--start-index N] [--number-of-batches N] [--validator-ee-image IMAGE] [--update-report [REPORT]]
#
# Options:
#   --workflow PATH         Workflow JSON to execute (default: flow-examples/eval-validate-transform-loop.json)
#   --batch-size N          Number of datasets per batch (default: 5)
#   --start-index N         1-based dataset index to start from (default: 1)
#   --number-of-batches N   Number of consecutive batches to run (default: 1)
#   --update-report [FILE]  Rehydrate metrics in an existing report instead of running workflows.
#                           Pass a specific markdown file or omit to update the latest report.
#   --validator-ee-image IMG
#                         Override the validator execution-environment image for all runs in this invocation
#   -h, --help              Show this message

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

PYTHON_BIN="python3.13"
DEFAULT_WORKFLOW="workflow_orchestrator/flow-examples/eval-validate-transform-loop.json"
WORKFLOW_PATH="$DEFAULT_WORKFLOW"
BATCH_SIZE=5
START_INDEX=1
NUMBER_OF_BATCHES=1
DATASET_ROOT="$REPO_ROOT/ansible_optimizer/dataset"
RESULTS_DIR="$REPO_ROOT/workflow_orchestrator/reports"
LOG_DIR="$REPO_ROOT/workflow_orchestrator/logs"
BINS_SOURCE=""

UPDATE_MODE=0
UPDATE_REPORT_FILE=""
VALIDATOR_EE_IMAGE=""

mkdir -p "$RESULTS_DIR" "$LOG_DIR"

usage() {
    sed -n '2,18p' "$0"
    exit 0
}

is_positive_integer() {
    [[ "$1" =~ ^[1-9][0-9]*$ ]]
}

format_duration() {
    local total=$1
    local hours=$(( total / 3600 ))
    local minutes=$(( (total % 3600) / 60 ))
    local seconds=$(( total % 60 ))

    if (( hours > 0 )); then
        printf "%dh %02dm %02ds" "$hours" "$minutes" "$seconds"
    elif (( minutes > 0 )); then
        printf "%dm %02ds" "$minutes" "$seconds"
    else
        printf "%ds" "$seconds"
    fi
}

update_last_updated() {
    LAST_UPDATED_TS=$(date '+%Y-%m-%d %H:%M:%S %Z')
    python - "$results_file" "$LAST_UPDATED_TS" <<'PY'
import pathlib, re, sys

path = pathlib.Path(sys.argv[1])
timestamp = sys.argv[2]

if not path.exists():
    sys.exit(0)

text = path.read_text(encoding="utf-8")
pattern = r"^\*\*Last updated:\*\* .*$"
replacement = f"**Last updated:** {timestamp}"

if re.search(pattern, text, flags=re.MULTILINE):
    text = re.sub(pattern, replacement, text, count=1, flags=re.MULTILINE)
else:
    started_pattern = r"^\*\*Run started:\*\* .*$"
    match = re.search(started_pattern, text, flags=re.MULTILINE)
    if match:
        insert_pos = match.end()
        text = text[:insert_pos] + "\n" + replacement + text[insert_pos:]
    else:
        text = replacement + "\n" + text

text = re.sub(r"^\s*\\1[^\n]*\n?", "", text, flags=re.MULTILINE)

path.write_text(text, encoding="utf-8")
PY
}

perform_update() {
    local report_path="$1"

    if [ ! -f "$report_path" ]; then
        echo "Error: Report file not found at $report_path" >&2
        exit 1
    fi

    "$PYTHON_BIN" - "$report_path" "$DATASET_ROOT" <<'PY'
import json
import pathlib
import re
import sys
from datetime import datetime

report_path = pathlib.Path(sys.argv[1])
dataset_root = pathlib.Path(sys.argv[2])

if not report_path.exists():
    print(f"Error: Report file {report_path} not found", file=sys.stderr)
    sys.exit(1)

if not dataset_root.exists():
    print(f"Error: Dataset root {dataset_root} not found", file=sys.stderr)
    sys.exit(1)

text = report_path.read_text(encoding="utf-8")

start_pattern = re.compile(r"- \[[^\]]+\] Starting Batch (\d+) \(datasets: ([^)]+)\)")
complete_pattern = re.compile(r"- \[[^\]]+\] Completed batch (\d+)\b", re.IGNORECASE)

batch_datasets = {}
batch_order = []
batch_start_times = {}
batch_end_times = {}
for match in start_pattern.finditer(text):
    line = match.group(0)
    batch_num = int(match.group(1))
    dataset_blob = match.group(2).strip()
    timestamp_match = re.match(r"- \[([^\]]+)\]", line)
    if timestamp_match:
        batch_start_times[batch_num] = timestamp_match.group(1)
    if not dataset_blob:
        continue
    ids = []
    for token in re.split(r"[\s,]+", dataset_blob):
        if not token:
            continue
        if not token.isdigit():
            print(f"Error: Invalid dataset identifier '{token}' for batch {batch_num}", file=sys.stderr)
            sys.exit(1)
        ids.append(token)
    batch_datasets[batch_num] = ids
    batch_order.append(batch_num)

completed_batches = {int(m.group(1)) for m in complete_pattern.finditer(text)}
for match in complete_pattern.finditer(text):
    line = match.group(0)
    batch_num = int(match.group(1))
    timestamp_match = re.match(r"- \[([^\]]+)\]", line)
    if timestamp_match:
        batch_end_times[batch_num] = timestamp_match.group(1)

if not batch_order:
    print("Error: No batch information found in report.", file=sys.stderr)
    sys.exit(1)

missing_completion = [str(b) for b in batch_order if b not in completed_batches]
if missing_completion:
    print(f"Error: Batches not completed in report: {', '.join(missing_completion)}", file=sys.stderr)
    sys.exit(1)

missing_def = [str(b) for b in batch_order if b not in batch_datasets]
if missing_def:
    print(f"Error: Missing dataset listing for batches: {', '.join(missing_def)}", file=sys.stderr)
    sys.exit(1)

def format_duration(total_seconds: int) -> str:
    if total_seconds is None:
        return "N/A"
    if total_seconds <= 0:
        return "0s"
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    if hours > 0:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes > 0:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"

def safe_json(path: pathlib.Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

def latest_entry(store: dict, prefix: str):
    best = None
    best_idx = -1
    for key, payload in store.items():
        if not key.startswith(prefix):
            continue
        suffix = key[len(prefix):]
        try:
            idx = int(suffix)
        except ValueError:
            continue
        if idx > best_idx:
            best_idx = idx
            best = payload
    return best

def normalize_code(value):
    if not isinstance(value, str):
        return ""
    stripped = value.strip("\n")
    if not stripped.strip():
        return ""
    return "\n".join(line.rstrip() for line in stripped.splitlines())

def parse_dt(value):
    if not isinstance(value, str) or not value.strip():
        return None
    cleaned = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        try:
            return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S %Z")
        except ValueError:
            try:
                return datetime.strptime(value.strip()[:19], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return None

def boolish(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}
    return bool(value)

def weighted_score(flow: dict, index: int):
    node = flow.get(f"subjective_evaluation_{index}") or {}
    scores = node.get("scores") or {}
    value = scores.get("weighted_overall_score")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None

def sanitize_reason(text_value: str) -> str:
    if not isinstance(text_value, str):
        return ""
    cleaned = " ".join(text_value.split())
    return cleaned.replace("|", "/")

def collect_dataset_metrics(dataset_id: str):
    dataset_dir = dataset_root / dataset_id
    store_path = dataset_dir / "optimization_flow.json"
    if not store_path.exists():
        return {
            "dataset_id": dataset_id,
            "status": "❌ Missing store",
            "duration_secs": 0,
            "duration_fmt": "N/A",
            "score": None,
            "score_fmt": "N/A",
            "score_pass": False,
            "score_diff": None,
            "score_diff_fmt": "N/A",
            "has_b": False,
            "has_c": False,
            "diff_bc": False,
            "full_pass": False,
            "code_fix": False,
            "reason": "No optimization_flow.json present",
            "success": False,
        }

    data = safe_json(store_path)
    if data is None:
        return {
            "dataset_id": dataset_id,
            "status": "❌ Invalid JSON",
            "duration_secs": 0,
            "duration_fmt": "N/A",
            "score": None,
            "score_fmt": "N/A",
            "score_pass": False,
            "score_diff": None,
            "score_diff_fmt": "N/A",
            "has_b": False,
            "has_c": False,
            "diff_bc": False,
            "full_pass": False,
            "code_fix": False,
            "reason": "Unable to parse optimization_flow.json",
            "success": False,
        }

    optimization_flow = data.get("optimization_flow") or {}

    latest_b = latest_entry(optimization_flow, "improved_code_B")
    latest_c = latest_entry(optimization_flow, "improved_code_C")
    code_b = (latest_b or {}).get("code") or ""
    code_c = (latest_c or {}).get("code") or ""

    norm_b = normalize_code(code_b)
    norm_c = normalize_code(code_c)

    has_b = bool(latest_b and code_b)
    has_c = bool(latest_c and code_c)
    is_diff_bc = bool(code_b and code_c and norm_b != norm_c)

    score_a = weighted_score(optimization_flow, 1)
    score_b = weighted_score(optimization_flow, 2)

    score_current = score_b if score_b is not None else score_a
    score_fmt = f"{score_current:.3f}" if score_current is not None else "N/A"
    score_pass = bool(score_current is not None and score_current >= 0.9)

    score_delta = None
    score_delta_fmt = "N/A"
    if score_a is not None and score_b is not None:
        score_delta = score_b - score_a
        score_delta_fmt = f"{score_delta:+0.3f}"

    vars_block = data.get("vars") or {}
    validation = None
    last_key = vars_block.get("last_validation_result_key")
    if isinstance(last_key, str):
        current = data
        for part in last_key.split("."):
            if isinstance(current, dict):
                current = current.get(part)
            else:
                current = None
                break
        if isinstance(current, dict):
            validation = current
    if validation is None:
        prefix = "objective_validation_"
        best_idx = -1
        for key, payload in optimization_flow.items():
            if not key.startswith(prefix):
                continue
            suffix = key[len(prefix):]
            try:
                idx = int(suffix)
            except ValueError:
                continue
            if idx > best_idx:
                best_idx = idx
                validation = payload

    validations_result = ""
    manifest_fix = None
    reason = ""
    if isinstance(validation, dict):
        validations_result = str(validation.get("validations_result", "")).lower()
        manifest_fix = validation.get("manifest_fix_required")
        reason = validation.get("result_analysis") or ""

    full_pass = validations_result == "pass"
    code_fix = boolish(manifest_fix)

    first_eval = optimization_flow.get("subjective_evaluation_1") or {}
    start_ts = parse_dt(first_eval.get("created_at") or first_eval.get("evaluated_at"))

    end_ts = None
    if isinstance(validation, dict):
        for field in ("created_at", "completed_at", "evaluated_at", "finished_at"):
            candidate = parse_dt(validation.get(field))
            if candidate:
                end_ts = candidate
                break

    duration_secs = 0
    duration_fmt = "N/A"
    if start_ts and end_ts and end_ts >= start_ts:
        duration_secs = int((end_ts - start_ts).total_seconds())
        duration_fmt = format_duration(duration_secs)

    cleaned_reason = sanitize_reason(reason)
    if not cleaned_reason:
        cleaned_reason = "—"

    return {
        "dataset_id": dataset_id,
        "status": "✅ Complete",
        "duration_secs": duration_secs,
        "duration_fmt": duration_fmt,
        "score": score_current,
        "score_fmt": score_fmt,
        "score_pass": score_pass,
        "score_diff": score_delta,
        "score_diff_fmt": score_delta_fmt,
        "has_b": has_b,
        "has_c": has_c,
        "diff_bc": is_diff_bc,
        "full_pass": full_pass,
        "code_fix": code_fix,
        "reason": cleaned_reason,
        "success": True,
    }

batch_rows = {}
summary_metadata = {}

overall_dataset_count = 0
overall_success = 0
overall_duration = 0
overall_has_b = 0
overall_has_c = 0
overall_diff_bc = 0
overall_full_pass = 0
overall_code_fix = 0
overall_score_pass = 0
overall_score_deltas = []

for batch_num in batch_order:
    dataset_ids = batch_datasets.get(batch_num, [])
    rows = [collect_dataset_metrics(ds_id) for ds_id in dataset_ids]
    batch_rows[batch_num] = rows

    dataset_count = len(rows)
    success_count = sum(1 for row in rows if row["success"])
    duration_total = sum(row["duration_secs"] or 0 for row in rows)
    duration_avg = int(duration_total / dataset_count) if dataset_count else 0
    start_ts = parse_dt(batch_start_times.get(batch_num))
    end_ts = parse_dt(batch_end_times.get(batch_num))
    duration_wall = duration_total
    if start_ts and end_ts and end_ts >= start_ts:
        duration_wall = int((end_ts - start_ts).total_seconds())

    has_b_true = sum(1 for row in rows if row["has_b"])
    has_c_true = sum(1 for row in rows if row["has_c"])
    diff_bc_true = sum(1 for row in rows if row["diff_bc"])
    full_pass_true = sum(1 for row in rows if row["full_pass"])
    code_fix_true = sum(1 for row in rows if row["code_fix"])
    score_pass_true = sum(1 for row in rows if row["score_pass"])
    score_deltas = [row["score_diff"] for row in rows if row["score_diff"] is not None]

    summary_metadata[batch_num] = {
        "dataset_count": dataset_count,
        "success_count": success_count,
        "duration_total": duration_total,
        "duration_avg": duration_avg,
        "duration_wall": duration_wall,
        "has_b_true": has_b_true,
        "has_c_true": has_c_true,
        "diff_bc_true": diff_bc_true,
        "full_pass_true": full_pass_true,
        "code_fix_true": code_fix_true,
        "score_pass_true": score_pass_true,
        "score_deltas": score_deltas,
    }

    overall_dataset_count += dataset_count
    overall_success += success_count
    overall_duration += duration_total
    overall_has_b += has_b_true
    overall_has_c += has_c_true
    overall_diff_bc += diff_bc_true
    overall_full_pass += full_pass_true
    overall_code_fix += code_fix_true
    overall_score_pass += score_pass_true
    overall_score_deltas.extend(score_deltas)

def percent(numerator, denominator):
    if denominator <= 0:
        return 0
    return int((100 * numerator) / denominator)

def avg_delta(values):
    if not values:
        return "N/A"
    value = sum(values) / len(values)
    return f"{value:+0.3f}"

dataset_section_lines = [
    "## Dataset Results",
    "",
    "| Data Index | Run Status | Run Duration | Subjective score (threshold 0.9) | B-A score diff | Has B | Has C | is B_C diff | Full pass C | code_fix_required | Reason |",
    "|-----------|-----------------|-------------------|----------------------------------|---------------|-------|-------|-------------|-------------|------------------|--------|",
]

existing_status_duration = {}
existing_summary_avg = None
existing_duration_secs = {}
existing_dataset_section = re.search(r"## Dataset Results\n(?:\n)?(.*?)(?:\n## |\Z)", text, re.DOTALL | re.MULTILINE)
if existing_dataset_section:
    section_body = existing_dataset_section.group(1)
    for line in section_body.splitlines():
        row_match = re.match(r"\|\s*(\d+)\s*\|\s*([^|]*)\|\s*([^|]*)\|", line)
        if row_match:
            dataset_id = row_match.group(1).strip()
            status_cell = row_match.group(2).strip()
            duration_cell = row_match.group(3).strip()
            existing_status_duration[dataset_id] = (status_cell, duration_cell)
            duration_value = duration_cell.strip()
            if duration_value and duration_value.lower() != "n/a":
                total = 0
                failed_parse = False
                token = ""
                for part in duration_value.split():
                    part = part.strip()
                    if not part:
                        continue
                    if part.endswith("h"):
                        try:
                            total += int(part[:-1]) * 3600
                        except ValueError:
                            failed_parse = True
                            break
                    elif part.endswith("m"):
                        try:
                            total += int(part[:-1]) * 60
                        except ValueError:
                            failed_parse = True
                            break
                    elif part.endswith("s"):
                        try:
                            total += int(part[:-1])
                        except ValueError:
                            failed_parse = True
                            break
                    else:
                        failed_parse = True
                        break
                if not failed_parse:
                    existing_duration_secs[dataset_id] = total
        else:
            summary_match = re.match(r"\|\s*\*\*Summary\*\*\s*\|\s*\*\*[^|]*\*\*\s*\|\s*\*\*Avg\s*([^*]+)\*\*", line)
            if summary_match:
                existing_summary_avg = summary_match.group(1).strip()

override_duration_secs = {}
override_batch_totals = {batch_num: 0 for batch_num in batch_order}

for batch_num in batch_order:
    rows = batch_rows[batch_num]
    for row in rows:
        dataset_id = str(row["dataset_id"])
        duration = row["duration_fmt"] if row["duration_fmt"] else "N/A"
        score = row["score_fmt"] if row["score_fmt"] else "N/A"
        score_diff = row["score_diff_fmt"] if row["score_diff_fmt"] else "N/A"
        reason = row["reason"] if row["reason"] else "—"
        status = row["status"]
        if dataset_id in existing_status_duration:
            existing_status, existing_duration = existing_status_duration[dataset_id]
            if existing_status:
                status = existing_status
            if existing_duration:
                duration = existing_duration
        duration_seconds = row["duration_secs"] or 0
        if dataset_id in existing_duration_secs:
            duration_seconds = existing_duration_secs[dataset_id]
        override_duration_secs[dataset_id] = duration_seconds
        override_batch_totals[batch_num] = override_batch_totals.get(batch_num, 0) + duration_seconds
        dataset_section_lines.append(
            "| {dataset_id} | {status} | {duration} | {score} | {score_diff} | {has_b} | {has_c} | {diff_bc} | {full_pass} | {code_fix} | {reason} |".format(
                dataset_id=row["dataset_id"],
                status=status,
                duration=duration,
                score=score,
                score_diff=score_diff,
                has_b="true" if row["has_b"] else "false",
                has_c="true" if row["has_c"] else "false",
                diff_bc="true" if row["diff_bc"] else "false",
                full_pass="true" if row["full_pass"] else "false",
                code_fix="true" if row["code_fix"] else "false",
                reason=reason,
            )
        )

override_total_duration = sum(override_duration_secs.values())
override_avg_secs = int(override_total_duration / overall_dataset_count) if overall_dataset_count else 0

if overall_dataset_count > 0:
    summary_avg_time_fmt = format_duration(override_avg_secs)
    summary_score_pass_percent = percent(overall_score_pass, overall_dataset_count)
    summary_has_b_percent = percent(overall_has_b, overall_dataset_count)
    summary_has_c_percent = percent(overall_has_c, overall_dataset_count)
    summary_diff_bc_percent = percent(overall_diff_bc, overall_dataset_count)
    summary_full_pass_percent = percent(overall_full_pass, overall_dataset_count)
    summary_code_fix_percent = percent(overall_code_fix, overall_dataset_count)
    summary_score_delta_avg = avg_delta(overall_score_deltas)
    summary_avg_display = summary_avg_time_fmt
    if existing_summary_avg:
        summary_avg_display = existing_summary_avg
    dataset_section_lines.append(
        "| **Summary** | **{success}/{total} complete** | **Avg {avg_time}** | **{score_pass}% ≥0.9** | **Avg {delta}** | **{has_b}% true** | **{has_c}% true** | **{diff_bc}% true** | **{full_pass}% true** | **{code_fix}% true** | — |".format(
            success=overall_success,
            total=overall_dataset_count,
            avg_time=summary_avg_display,
            score_pass=summary_score_pass_percent,
            delta=summary_score_delta_avg,
            has_b=summary_has_b_percent,
            has_c=summary_has_c_percent,
            diff_bc=summary_diff_bc_percent,
            full_pass=summary_full_pass_percent,
            code_fix=summary_code_fix_percent,
        )
    )
else:
    dataset_section_lines.append("| **Summary** |  |  |  |  |  |  |  |  |  |  |")

dataset_section = "\n".join(dataset_section_lines).rstrip() + "\n\n"

batch_stats_lines = ["## Batch Statistics", "", "| Batch | Range | Processed | Successful | Failed | Average Time | Batch Time |", "|-------|-------|-----------|-----------|--------|--------------|------------|"]

total_batch_duration = 0

for batch_num in batch_order:
    summary = summary_metadata[batch_num]
    dataset_ids = batch_datasets[batch_num]
    dataset_count = summary["dataset_count"]
    success_count = summary["success_count"]
    failed_count = dataset_count - success_count
    total_secs = override_batch_totals.get(batch_num, summary["duration_wall"])
    avg_secs = int(total_secs / dataset_count) if dataset_count else 0
    total_batch_duration += total_secs

    if dataset_ids:
        range_start = dataset_ids[0]
        range_end = dataset_ids[-1]
    else:
        range_start = "N/A"
        range_end = "N/A"

    batch_stats_lines.append(
        "| {batch} | {start}-{end} | {processed} | {success} | {failed} | {avg} | {total} |".format(
            batch=batch_num,
            start=range_start,
            end=range_end,
            processed=dataset_count,
            success=success_count,
            failed=failed_count,
            avg=format_duration(avg_secs),
            total=format_duration(total_secs),
        )
    )

overall_avg = override_avg_secs
batch_stats_lines.append(
    "| **Summary** | — | — | — | — | **{avg} avg** | **{total} total** |".format(
        avg=format_duration(overall_avg),
        total=format_duration(total_batch_duration),
    )
)
batch_stats_lines.append("")
batch_stats_section = "\n".join(batch_stats_lines).rstrip() + "\n\n"

overall_failed = overall_dataset_count - overall_success
has_b_percent = percent(overall_has_b, overall_dataset_count)
has_c_percent = percent(overall_has_c, overall_dataset_count)
diff_bc_percent = percent(overall_diff_bc, overall_dataset_count)
full_pass_percent = percent(overall_full_pass, overall_dataset_count)
code_fix_percent = percent(overall_code_fix, overall_dataset_count)
score_pass_percent = percent(overall_score_pass, overall_dataset_count)
overall_score_delta_avg = avg_delta(overall_score_deltas)

overall_avg_fmt = format_duration(overall_avg)
overall_total_fmt = format_duration(total_batch_duration)

batch_size_values = ",".join(str(summary_metadata[b]["dataset_count"]) for b in batch_order)

overall_lines = [
    "## Overall Statistics",
    "",
    "| Metric | Value |",
    "|--------|-------|",
    f"| Batches Run | {len(batch_order)} |",
    f"| Batch Sizes | {batch_size_values} |",
    f"| Playbooks Processed | {overall_dataset_count} |",
    f"| Successful | {overall_success} |",
    f"| Failed | {overall_failed} |",
    f"| Average Playbook Optimization Time | {overall_avg_fmt} |",
    f"| Total Batch Time | {overall_total_fmt} |",
    "",
]
overall_section = "\n".join(overall_lines).rstrip() + "\n\n"

def update_section(content: str, title: str, replacement: str) -> str:
    pattern = re.compile(rf"## {re.escape(title)}\n.*?(?=^## |\Z)", re.DOTALL | re.MULTILINE)
    match = pattern.search(content)
    if match:
        return content[: match.start()] + replacement + content[match.end():]
    # Append section if missing
    if not content.endswith("\n"):
        content += "\n"
    if not content.endswith("\n\n"):
        content += "\n"
    return content + replacement

text = update_section(text, "Dataset Results", dataset_section)
text = update_section(text, "Batch Statistics", batch_stats_section)
text = update_section(text, "Overall Statistics", overall_section)

report_path.write_text(text, encoding="utf-8")
PY

    local rc=$?
    if [ $rc -ne 0 ]; then
        echo "Error: Failed to refresh report content" >&2
        exit $rc
    fi

    results_file="$report_path"
    update_last_updated
    echo "Report updated: $report_path"
}
while [[ $# -gt 0 ]]; do
    case "$1" in
        --workflow)
            WORKFLOW_PATH="$2"
            shift 2
            ;;
        --batch-size)
            BATCH_SIZE="$2"
            shift 2
            ;;
        --start-index)
            START_INDEX="$2"
            shift 2
            ;;
        --number-of-batches)
            NUMBER_OF_BATCHES="$2"
            shift 2
            ;;
        --validator-ee-image)
            VALIDATOR_EE_IMAGE="$2"
            shift 2
            ;;
        --validator-ee-image=*)
            VALIDATOR_EE_IMAGE="${1#*=}"
            shift 1
            ;;
        --update-report)
            UPDATE_MODE=1
            if [[ $# -gt 1 && "$2" != --* ]]; then
                UPDATE_REPORT_FILE="$2"
                shift 2
            else
                UPDATE_REPORT_FILE="latest"
                shift 1
            fi
            ;;
        --update-report=*)
            UPDATE_MODE=1
            UPDATE_REPORT_FILE="${1#*=}"
            shift 1
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage
            ;;
    esac
done

if ! is_positive_integer "$BATCH_SIZE"; then
    echo "Error: --batch-size must be a positive integer" >&2
    exit 1
fi
if ! is_positive_integer "$START_INDEX"; then
    echo "Error: --start-index must be a positive integer" >&2
    exit 1
fi
if ! is_positive_integer "$NUMBER_OF_BATCHES"; then
    echo "Error: --number-of-batches must be a positive integer" >&2
    exit 1
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "Error: $PYTHON_BIN not found on PATH" >&2
    exit 1
fi

if (( UPDATE_MODE == 0 )); then
    if [ ! -d ".venv" ]; then
        echo "Error: .venv not found. Create it with 'python3.13 -m venv .venv' and install dependencies." >&2
        exit 1
    fi

    if [ ! -f "$WORKFLOW_PATH" ]; then
        echo "Error: Workflow file not found at $WORKFLOW_PATH" >&2
        exit 1
    fi
    WORKFLOW_PATH="$(realpath "$WORKFLOW_PATH")"
elif [ -f "$WORKFLOW_PATH" ]; then
    WORKFLOW_PATH="$(realpath "$WORKFLOW_PATH")"
fi

DATASET_ROOT="$(realpath "$DATASET_ROOT")"
if [ ! -d "$DATASET_ROOT" ]; then
    echo "Error: Dataset root not found at $DATASET_ROOT" >&2
    exit 1
fi

if (( UPDATE_MODE == 1 )); then
    report_path="$UPDATE_REPORT_FILE"
    if [ -z "$report_path" ] || [ "$report_path" = "latest" ]; then
        latest_report=$(ls -1t "$RESULTS_DIR"/workflow_results_*.md 2>/dev/null | head -n1 || true)
        if [ -z "$latest_report" ]; then
            echo "Error: No workflow_results_*.md files found in $RESULTS_DIR" >&2
            exit 1
        fi
        report_path="$latest_report"
    fi

    perform_update "$report_path"
    exit 0
fi


ALL_DATASETS=()
while IFS= read -r line; do
    ALL_DATASETS+=("$line")
done < <(find "$DATASET_ROOT" -mindepth 1 -maxdepth 1 -type d -exec basename {} \; | awk '/^[0-9]+$/' | sort -n)
TOTAL_DATASETS=${#ALL_DATASETS[@]}

if [ "$TOTAL_DATASETS" -eq 0 ]; then
    echo "Error: No dataset directories found under $DATASET_ROOT" >&2
    exit 1
fi

run_workflow() {
    local dataset_id="$1"
    local result_file="$2"
    local dataset_dir="$DATASET_ROOT/$dataset_id"
    local store_path="$dataset_dir/optimization_flow.json"
    local log_file="$LOG_DIR/workflow_${dataset_id}.log"

    if [ -n "$VALIDATOR_EE_IMAGE" ] && [ -f "$store_path" ]; then
        python <<'PY' "$store_path" "$VALIDATOR_EE_IMAGE"
import json
import sys
from pathlib import Path

store_path = Path(sys.argv[1])
ee_image = sys.argv[2]

try:
    data = json.loads(store_path.read_text(encoding="utf-8"))
except Exception:
    data = {}

if not isinstance(data, dict):
    data = {}

vars_block = data.setdefault("vars", {})
if not isinstance(vars_block, dict):
    vars_block = {}
    data["vars"] = vars_block

vars_block["validator_ee_image"] = ee_image
store_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
PY
    fi

    if [ ! -f "$store_path" ]; then
        echo "$dataset_id|0|1|false|N/A|false|false|false|false|false" > "$result_file"
        return
    fi

    local start
    start=$(date +%s)

    set +e
    # shellcheck disable=SC1091
    source .venv/bin/activate
    python -m workflow_orchestrator.engine "$WORKFLOW_PATH" "$store_path" >"$log_file" 2>&1
    local exit_code=$?
    deactivate
    set -e

    local end
    end=$(date +%s)
    local duration=$((end - start))

    local metrics
    METRIC_DATASET_ID="$dataset_id" \
    METRIC_DATASET_PATH="$store_path" \
    metrics=$(python <<'PY'
import json
import os
import pathlib
import sys

dataset_id = os.environ.get("METRIC_DATASET_ID", "")
dataset_path = pathlib.Path(os.environ.get("METRIC_DATASET_PATH", ""))

def safe_load(path: pathlib.Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

data = safe_load(dataset_path)
optimization_flow = data.get("optimization_flow") or {}
original_code = data.get("original_code") or ""

def latest_entry(prefix: str):
    best = None
    best_idx = -1
    prefix_len = len(prefix)
    for name, payload in optimization_flow.items():
        if not name.startswith(prefix):
            continue
        suffix = name[prefix_len:]
        try:
            idx = int(suffix)
        except ValueError:
            continue
        if idx > best_idx:
            best_idx = idx
            best = payload
    return best, best_idx

def normalize(text: str) -> str:
    if not isinstance(text, str):
        return ""
    stripped = text.strip("\n")
    if not stripped.strip():
        return ""
    return "\n".join(line.rstrip() for line in stripped.splitlines())

from datetime import datetime

latest_b, _ = latest_entry("improved_code_B")
latest_c, _ = latest_entry("improved_code_C")
code_b = (latest_b or {}).get("code") or ""
code_c = (latest_c or {}).get("code") or ""

norm_b = normalize(code_b)
norm_c = normalize(code_c)

has_b = bool(latest_b and code_b)
has_c = bool(latest_c and code_c)
is_diff_b_c = bool(code_b and code_c) and norm_b != norm_c

def weighted_score(index: int):
    key = f"subjective_evaluation_{index}"
    evaluation = optimization_flow.get(key) or {}
    scores = evaluation.get("scores") or {}
    value = scores.get("weighted_overall_score")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None

score_a = weighted_score(1)
score_b = weighted_score(2)
score_delta = None
score_delta_fmt = "N/A"
if score_a is not None and score_b is not None:
    score_delta = score_b - score_a
    score_delta_fmt = f"{score_delta:+0.3f}"

score_current = score_b if score_b is not None else score_a
score_fmt = "N/A" if score_current is None else f"{score_current:.3f}"
score_raw = "" if score_current is None else f"{score_current}"

def lookup(root, dotted):
    if not isinstance(dotted, str) or not dotted:
        return None
    current = root
    for part in dotted.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current

vars_block = data.get("vars") or {}
latest_validation = lookup(data, vars_block.get("last_validation_result_key"))
if latest_validation is None:
    best_val = None
    best_idx = -1
    prefix = "objective_validation_"
    for name, payload in optimization_flow.items():
        if not name.startswith(prefix):
            continue
        suffix = name[len(prefix):]
        try:
            idx = int(suffix)
        except ValueError:
            continue
        if idx > best_idx:
            best_idx = idx
            best_val = payload
    latest_validation = best_val

validations_result = ""
manifest_fix_required = None
reason = ""
if isinstance(latest_validation, dict):
    validations_result = str(latest_validation.get("validations_result", "")).lower()
    manifest_fix_required = latest_validation.get("manifest_fix_required")
    reason = latest_validation.get("result_analysis") or ""

def boolish(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}
    return bool(value)

c_pass_all = validations_result == "pass"
code_fix_required = boolish(manifest_fix_required)

def parse_dt(value: str):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

def format_duration(seconds: int) -> str:
    if seconds <= 0:
        return "0s"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes > 0:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"

first_eval = optimization_flow.get("subjective_evaluation_1") or {}
start_dt = parse_dt(first_eval.get("created_at") or first_eval.get("evaluated_at"))
end_dt = None
if isinstance(latest_validation, dict):
    for key in ("created_at", "completed_at", "evaluated_at", "finished_at"):
        candidate = parse_dt(latest_validation.get(key))
        if candidate:
            end_dt = candidate
            break

workflow_duration_seconds = None
workflow_duration_fmt = "N/A"
if start_dt and end_dt and end_dt >= start_dt:
    workflow_duration_seconds = int((end_dt - start_dt).total_seconds())
    workflow_duration_fmt = format_duration(workflow_duration_seconds)

reason = " ".join(str(reason).split())
reason = reason.replace("|", "/")

result_fields = [
    workflow_duration_fmt,
    "" if workflow_duration_seconds is None else str(workflow_duration_seconds),
    score_fmt,
    score_raw,
    score_delta_fmt,
    "" if score_delta is None else f"{score_delta}",
    "true" if has_b else "false",
    "true" if has_c else "false",
    "true" if is_diff_b_c else "false",
    "true" if c_pass_all else "false",
    "true" if code_fix_required else "false",
    reason,
]
print("|".join(result_fields))
PY
)

    echo "${dataset_id}|${duration}|${exit_code}|${metrics:-N/A|0|N/A|0|N/A|0|false|false|false|false|false|}" > "$result_file"
}

DATASET_IDS=()
DATASET_WALL_TIMES=()
DATASET_EXIT_CODES=()
DATASET_DURATION_FMT=()
DATASET_DURATION_SECS=()
DATASET_SCORE_FMT=()
DATASET_SCORE_RAW=()
DATASET_SCORE_DELTA_FMT=()
DATASET_SCORE_DELTA_RAW=()
DATASET_HAS_B=()
DATASET_HAS_C=()
DATASET_DIFF_BC=()
DATASET_FULL_PASS=()
DATASET_CODE_FIX=()
DATASET_REASONS=()

BATCH_LABELS=()
BATCH_RANGE_START=()
BATCH_RANGE_END=()
BATCH_PROCESSED=()
BATCH_SUCCESS=()
BATCH_FAILED=()
BATCH_AVG_TIMES=()
BATCH_DURATIONS=()
BATCH_SIZE_VALUES=()

SUMMARY_DATASET_COUNT=0
SUMMARY_SUCCESS_COUNT=0
SUMMARY_AVG_DURATION_SECS=0
SUMMARY_AVG_BATCH_SECS=0
SUMMARY_TOTAL_BATCH_SECS=0

PROGRESS_LOG=()

METRIC_DATASET_COUNT=0
METRIC_DURATION_COUNT=0
METRIC_TOTAL_DURATION_SECS=0
METRIC_AVG_DURATION_SECS=0
METRIC_AVG_DURATION_FMT="N/A"
METRIC_SUCCESS_COUNT=0
METRIC_SUCCESS_PERCENT=0
METRIC_HAS_B_PERCENT=0
METRIC_HAS_C_PERCENT=0
METRIC_DIFF_BC_PERCENT=0
METRIC_FULL_PASS_PERCENT=0
METRIC_CODE_FIX_PERCENT=0
METRIC_SCORE_PASS_PERCENT=0
METRIC_SCORE_DELTA_AVG="N/A"
METRIC_BATCH_TOTAL_SECS=0

render_report() {
    if [ -z "${results_file:-}" ]; then
        return
    fi

    local dataset_count=${#DATASET_IDS[@]}
    local total_time=0
    local duration_count=0
    local success_count=0
    local idx
    for idx in "${!DATASET_IDS[@]}"; do
        local duration_raw="${DATASET_DURATION_SECS[idx]}"
        if [[ -n "$duration_raw" && "$duration_raw" =~ ^[0-9]+$ ]]; then
            total_time=$((total_time + duration_raw))
            duration_count=$((duration_count + 1))
        fi
        if [ "${DATASET_EXIT_CODES[idx]}" -eq 0 ]; then
            success_count=$((success_count + 1))
        fi
    done

    local average_time=0
    if [ $duration_count -gt 0 ]; then
        average_time=$((total_time / duration_count))
    fi

    local total_batch_duration=0
    local batch_count=0
    if [[ ${BATCH_DURATIONS+x} ]]; then
        for dur in "${BATCH_DURATIONS[@]}"; do
            total_batch_duration=$((total_batch_duration + dur))
        done
        batch_count=${#BATCH_DURATIONS[@]}
    fi
    local average_batch_time=0
    if [ "$batch_count" -gt 0 ]; then
        average_batch_time=$((total_batch_duration / batch_count))
    fi

    SUMMARY_DATASET_COUNT=$dataset_count
    SUMMARY_SUCCESS_COUNT=$success_count
    SUMMARY_AVG_DURATION_SECS=$average_time
    SUMMARY_AVG_BATCH_SECS=$average_batch_time
    SUMMARY_TOTAL_BATCH_SECS=$total_batch_duration

    {
        printf "# Workflow Batch Results\n\n"
        printf "**Run started:** %s\n" "$RUN_START_TS"
        printf "**Last updated:** %s\n\n" "$LAST_UPDATED_TS"

        printf "## Progress Log\n\n"
        if [ ${#PROGRESS_LOG[@]} -eq 0 ]; then
            printf "- (No entries)\n\n"
        else
            for entry in "${PROGRESS_LOG[@]}"; do
                printf "%s\n" "$entry"
            done
            printf "\n"
        fi

        printf "## Parameters\n\n"
        printf -- "- **Workflow:** %s\n" "$WORKFLOW_PATH"
        printf -- "- **Python Version:** %s\n" "$PYTHON_VERSION_STR"
        printf -- "- **Batch Size Requested:** %s\n" "$BATCH_SIZE"
        printf -- "- **Batches Run:** %s\n" "${#BATCH_LABELS[@]}"
        printf -- "- **Datasets Processed:** %s\n\n" "$dataset_count"

        printf "## Dataset Results\n\n"
        printf "| Data Index | Run Status | Run Duration | Subjective score (threshold 0.9) | B-A score diff | Has B | Has C | is B_C diff | Full pass C | code_fix_required | Reason |\n"
        printf "|-----------|-----------------|-------------------|----------------------------------|---------------|-------|-------|-------------|-------------|------------------|--------|\n"

        for idx in "${!DATASET_IDS[@]}"; do
            local status="✅ Complete"
            if [ "${DATASET_EXIT_CODES[idx]}" -ne 0 ]; then
                status="❌ Exit ${DATASET_EXIT_CODES[idx]}"
            fi

            local duration_display="${DATASET_DURATION_FMT[idx]}"
            [ -z "$duration_display" ] && duration_display=""

            printf "| %s | %s | %s |  |  |  |  |  |  |  |  |\n" \
                "${DATASET_IDS[idx]}" \
                "$status" \
                "$duration_display"
        done

        if [ "$dataset_count" -gt 0 ]; then
            printf "| **Summary** | **%s/%s complete** | **Avg %s** |  |  |  |  |  |  |  |  |\n\n" \
                "$success_count" \
                "$dataset_count" \
                "$(format_duration "$average_time")"
        else
            printf "| **Summary** |  |  |  |  |  |  |  |  |  |  |\n\n"
        fi

        printf "## Batch Statistics\n\n"
        printf "| Batch | Range | Processed | Successful | Failed | Average Time | Batch Time |\n"
        printf "|-------|-------|-----------|-----------|--------|--------------|------------|\n"

        if [[ ${BATCH_LABELS+x} ]]; then
            for idx in "${!BATCH_LABELS[@]}"; do
                printf "| %s | %s-%s | %s | %s | %s | %s | %s |\n" \
                    "${BATCH_LABELS[idx]}" \
                    "${BATCH_RANGE_START[idx]}" \
                    "${BATCH_RANGE_END[idx]}" \
                    "${BATCH_PROCESSED[idx]}" \
                    "${BATCH_SUCCESS[idx]}" \
                    "${BATCH_FAILED[idx]}" \
                    "$(format_duration "${BATCH_AVG_TIMES[idx]}")" \
                    "$(format_duration "${BATCH_DURATIONS[idx]}")"
            done
        fi
        printf "| **Summary** | — | — | — | — | **%s avg** | **%s total** |\n\n" \
            "$(format_duration "$average_time")" \
            "$(format_duration "$total_batch_duration")"

        printf "## Overall Statistics\n\n"
        printf "| Metric | Value |\n|--------|-------|\n"
        printf "| Batches Run | %s |\n" "${#BATCH_LABELS[@]}"
        if [[ ${BATCH_SIZE_VALUES+x} && ${#BATCH_SIZE_VALUES[@]} -gt 0 ]]; then
            printf "| Batch Sizes | %s |\n" "$(IFS=,; echo "${BATCH_SIZE_VALUES[*]}")"
        else
            printf "| Batch Sizes |  |\n"
        fi
        printf "| Playbooks Processed | %s |\n" "$dataset_count"
        printf "| Successful | %s |\n" "$success_count"
        printf "| Failed | %s |\n" "$((dataset_count - success_count))"
        printf "| Average Data Duration | %s |\n" "$(format_duration "$average_time")"
        printf "| Average Batch Duration | %s |\n" "$(format_duration "$average_batch_time")"
        printf "| Total Time | %s |\n" "$(format_duration "$total_batch_duration")"
    } > "$results_file"
}

refresh_report() {
    LAST_UPDATED_TS=$(date '+%Y-%m-%d %H:%M:%S %Z')
    render_report
}

timestamp="$(date '+%Y%m%d_%H%M%S')"
results_file="$RESULTS_DIR/workflow_results_${timestamp}.md"

RUN_START_TS=$(date '+%Y-%m-%d %H:%M:%S %Z')
PYTHON_VERSION_STR="$($PYTHON_BIN --version)"
LAST_UPDATED_TS="$RUN_START_TS"

PROGRESS_LOG+=("- [$RUN_START_TS] Workflow initialized (batches: $NUMBER_OF_BATCHES, batch size: $BATCH_SIZE, datasets detected: $TOTAL_DATASETS)")
refresh_report

dataset_offset_base=$((START_INDEX - 1))
if (( dataset_offset_base < 0 )); then
    echo "Error: start-index must be at least 1" >&2
    exit 1
fi

batch_counter=0

while (( batch_counter < NUMBER_OF_BATCHES )); do
    batch_number=$((batch_counter + 1))
    start_pos=$((dataset_offset_base + batch_counter * BATCH_SIZE))

    if (( start_pos >= TOTAL_DATASETS )); then
        if (( batch_counter == 0 )); then
            echo "Error: start-index $START_INDEX exceeds available datasets (total $TOTAL_DATASETS)" >&2
            exit 1
        fi
        break
    fi

    BATCH_DATASETS=("${ALL_DATASETS[@]:$start_pos:$BATCH_SIZE}")
    if [ ${#BATCH_DATASETS[@]} -eq 0 ]; then
        break
    fi

    batch_start_stamp=$(date '+%Y-%m-%d %H:%M:%S %Z')
    CURRENT_BATCH="Batch $batch_number (datasets: ${BATCH_DATASETS[*]})"
    PROGRESS_LOG+=("- [$batch_start_stamp] Starting $CURRENT_BATCH")
    refresh_report

    echo ""
    echo "──────────────────────────────────────────────"
    echo "Batch $batch_number (${#BATCH_DATASETS[@]} dataset(s))"
    echo "──────────────────────────────────────────────"
    printf '  - %s\n' "${BATCH_DATASETS[@]}"

    batch_start_time=$(date +%s)
    tmpfiles=()
    pids=()

    for dataset_id in "${BATCH_DATASETS[@]}"; do
        tmpfile=$(mktemp)
        tmpfiles+=("$tmpfile")
        run_workflow "$dataset_id" "$tmpfile" &
        pids+=("$!")
    done

    for idx in "${!pids[@]}"; do
        if ! wait "${pids[idx]}"; then
            echo "Dataset ${BATCH_DATASETS[idx]} encountered an execution error (see logs)." >&2
        fi
    done

    batch_dataset_ids=()
    batch_times=()
    batch_exit_codes=()

    for idx in "${!tmpfiles[@]}"; do
        result_line=$(cat "${tmpfiles[idx]}")
        rm -f "${tmpfiles[idx]}"
        IFS='|' read -r ds_id ds_time ds_exit duration_fmt duration_raw score_fmt score_raw score_delta_fmt score_delta_raw has_b has_c diff_bc c_pass code_fix reason <<< "$result_line"
        [ -z "$ds_id" ] && continue
        batch_dataset_ids+=("$ds_id")
        batch_times+=("$ds_time")
        batch_exit_codes+=("$ds_exit")

        DATASET_IDS+=("$ds_id")
        DATASET_WALL_TIMES+=("$ds_time")
        DATASET_EXIT_CODES+=("$ds_exit")

        duration_wall_fmt="$(format_duration "$ds_time")"
        DATASET_DURATION_FMT+=("$duration_wall_fmt")
        DATASET_DURATION_SECS+=("$ds_time")

        DATASET_SCORE_FMT+=("$score_fmt")
        DATASET_SCORE_RAW+=("$score_raw")
        DATASET_SCORE_DELTA_FMT+=("$score_delta_fmt")
        DATASET_SCORE_DELTA_RAW+=("$score_delta_raw")
        DATASET_HAS_B+=("$has_b")
        DATASET_HAS_C+=("$has_c")
        DATASET_DIFF_BC+=("$diff_bc")
        DATASET_FULL_PASS+=("$c_pass")
        DATASET_CODE_FIX+=("$code_fix")
        DATASET_REASONS+=("$reason")
    done

    batch_finish_stamp=$(date '+%Y-%m-%d %H:%M:%S %Z')
    PROGRESS_LOG+=("- [$batch_finish_stamp] Completed batch $batch_number (${#DATASET_IDS[@]}/$TOTAL_DATASETS datasets processed)")

    batch_end_time=$(date +%s)
    batch_duration=$((batch_end_time - batch_start_time))
    batch_total_time=0
    batch_success=0
    for idx in "${!batch_dataset_ids[@]}"; do
        batch_total_time=$((batch_total_time + batch_times[idx]))
        if [ "${batch_exit_codes[idx]}" -eq 0 ]; then
            batch_success=$((batch_success + 1))
        fi
    done

    batch_processed=${#batch_dataset_ids[@]}
    batch_failed=$((batch_processed - batch_success))
    batch_avg_time=0
    if [ $batch_processed -gt 0 ]; then
        batch_avg_time=$((batch_total_time / batch_processed))
    fi

    BATCH_LABELS+=("$batch_number")
    if [ $batch_processed -gt 0 ]; then
        BATCH_RANGE_START+=("${BATCH_DATASETS[0]}")
        BATCH_RANGE_END+=("${BATCH_DATASETS[$((batch_processed - 1))]}")
    else
        BATCH_RANGE_START+=("$((start_pos + 1))")
        BATCH_RANGE_END+=("$((start_pos + batch_processed))")
    fi
    BATCH_PROCESSED+=("$batch_processed")
    BATCH_SUCCESS+=("$batch_success")
    BATCH_FAILED+=("$batch_failed")
    BATCH_AVG_TIMES+=("$batch_avg_time")
    BATCH_DURATIONS+=("$batch_duration")
    BATCH_SIZE_VALUES+=("${#BATCH_DATASETS[@]}")

    refresh_report

    batch_counter=$((batch_counter + 1))
done

RUN_END_TS=$(date '+%Y-%m-%d %H:%M:%S %Z')
PROGRESS_LOG+=("- [$RUN_END_TS] All batches complete")
refresh_report

if [ ${#DATASET_IDS[@]} -eq 0 ]; then
    echo "Error: No datasets were processed." >&2
    exit 1
fi

# Console summary
dataset_count=$SUMMARY_DATASET_COUNT
success_count=$SUMMARY_SUCCESS_COUNT
average_time=$SUMMARY_AVG_DURATION_SECS
average_batch_time=$SUMMARY_AVG_BATCH_SECS
total_batch_duration=$SUMMARY_TOTAL_BATCH_SECS

echo ""
echo "==================== Summary ===================="
printf "%-12s %s\n" "Datasets:" "$dataset_count"
printf "%-12s %s\n" "Successful:" "$success_count"
printf "%-12s %s\n" "Failed:" "$((dataset_count - success_count))"
printf "%-12s %s\n" "Avg data:" "$(format_duration "$average_time")"
printf "%-12s %s\n" "Avg batch:" "$(format_duration "$average_batch_time")"
printf "%-12s %s\n" "Total time:" "$(format_duration "$total_batch_duration")"
echo ""
printf "\nLogs written to %s\n" "$LOG_DIR"
printf "Markdown summary saved to %s\n" "$results_file"
