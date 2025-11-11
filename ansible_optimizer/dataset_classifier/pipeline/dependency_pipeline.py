"""
Utility to extract Ansible dependency metadata and classify records by cloud.

This script parses Ansible playbooks stored in a JSONL dataset, infers the
required Ansible collections and related Python packages, and groups each
record into Azure, AWS, dual-cloud (cross Azure/AWS), or neutral categories.
Designed for incremental execution (e.g. first N records) so that the workflow
can be validated before processing an entire corpus. By default, JSONL outputs
are written to ``ansible_optimizer/dataset_classifier/output/jsonl`` while
per-group dependency summaries are stored alongside in the ``output`` folder.
"""

from __future__ import annotations

import argparse
import json
import itertools
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Set, Tuple


STRUCTURAL_KEYS = {
    "name",
    "hosts",
    "gather_facts",
    "vars",
    "become",
    "become_user",
    "tasks",
    "pre_tasks",
    "post_tasks",
    "handlers",
    "roles",
    "collections",
    "block",
    "rescue",
    "always",
    "delegate_to",
    "environment",
    "import_tasks",
    "include_tasks",
    "include_role",
    "import_role",
    "connection",
    "vars_files",
    "vars_prompt",
    "module_defaults",
    "strategy",
    "when",
    "loop",
    "loop_control",
    "with_items",
    "with_list",
    "with_dict",
    "notify",
    "register",
    "until",
    "retries",
    "delay",
    "throttle",
    "check_mode",
    "diff",
    "tags",
}

TASK_SECTION_KEYS = {"tasks", "pre_tasks", "post_tasks", "handlers", "block", "rescue", "always"}
VAR_SECTION_KEYS = {"vars"}

MODULE_LINE_PATTERN = re.compile(r"^\s*(-\s*)?([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*)\s*:\s*(.*)$", re.ASCII)

MODULE_PREFIX_COLLECTION_MAP: Sequence[Tuple[str, str]] = (
    ("azure_rm", "azure.azcollection"),
    ("azure_rm_", "azure.azcollection"),
    ("azure_", "azure.azcollection"),
    ("azcollection.", "azure.azcollection"),
    ("ec2", "amazon.aws"),
    ("aws_", "amazon.aws"),
    ("iam_", "amazon.aws"),
    ("rds", "amazon.aws"),
    ("s3", "amazon.aws"),
    ("cloudwatch", "amazon.aws"),
    ("cloudtrail", "amazon.aws"),
    ("cloudformation", "amazon.aws"),
    ("lambda_", "amazon.aws"),
    ("redshift", "amazon.aws"),
    ("route53", "amazon.aws"),
    ("elb", "amazon.aws"),
    ("efs_", "amazon.aws"),
    ("eks_", "amazon.aws"),
    ("ecs_", "amazon.aws"),
)

COLLECTION_PACKAGE_DEFAULTS: Dict[str, Set[str]] = {
    "azure.azcollection": {"azure-identity", "azure-mgmt-resource"},
    "azure.azcollection_preview": {"azure-identity", "azure-mgmt-resource"},
    "community.azure": {"azure-identity", "azure-mgmt-resource"},
    "amazon.aws": {"boto3", "botocore"},
    "community.aws": {"boto3", "botocore"},
}

MODULE_PACKAGE_OVERRIDES: Dict[str, Set[str]] = {
    "community.aws.inspector_target": {"boto3", "botocore"},
    "community.aws.aws_inspector_target": {"boto3", "botocore"},
    "amazon.aws.ec2_instance": {"boto3", "botocore"},
    "amazon.aws.ec2_group": {"boto3", "botocore"},
    "azure.azcollection.azure_rm_virtualmachine": {"azure-identity", "azure-mgmt-compute"},
}

AZURE_COLLECTION_PREFIXES = ("azure.", "community.azure")
AWS_COLLECTION_PREFIXES = ("amazon.aws", "community.aws", "aws.")
AZURE_PACKAGE_PREFIXES = ("azure-",)
AWS_PACKAGE_NAMES = {"boto3", "botocore"}
AZURE_MODULE_PREFIXES = ("azure_rm", "azure.azcollection")
AWS_MODULE_PREFIXES = ("amazon.aws", "community.aws", "ec2", "aws_", "iam_", "route53", "rds")

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


@dataclass
class RecordDependencies:
    index: int
    instruction: str
    yaml: str
    collections: List[str]
    python_packages: List[str]
    modules: List[str]
    group: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract Ansible dependency metadata.")
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Path to the input JSONL dataset.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=(
            "Destination directory for dependency artifacts "
            "(defaults to ansible_optimizer/dataset_classifier/output)."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional maximum number of records to process (default: all).",
    )
    return parser.parse_args()


def extract_collections_from_yaml_text(yaml_text: str) -> List[str]:
    collections: List[str] = []
    lines = yaml_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        if stripped.startswith("collections:"):
            indent = len(line) - len(line.lstrip(" "))
            remainder = stripped[len("collections:") :].strip()
            if remainder:
                if remainder.startswith("[") and remainder.endswith("]"):
                    items = remainder[1:-1].split(",")
                    for item in items:
                        value = item.strip().strip("\"'")
                        if value:
                            collections.append(value)
                else:
                    value = remainder.strip("\"'")
                    if value:
                        collections.append(value)
            else:
                j = i + 1
                while j < len(lines):
                    next_line = lines[j]
                    next_stripped = next_line.strip()
                    next_indent = len(next_line) - len(next_line.lstrip(" "))
                    if not next_stripped:
                        j += 1
                        continue
                    if next_indent <= indent:
                        break
                    if next_stripped.startswith("- "):
                        value = next_stripped[2:].strip().strip("\"'")
                        if value:
                            collections.append(value)
                    j += 1
                i = j - 1
        i += 1
    return collections


def extract_modules_from_yaml_text(yaml_text: str) -> List[str]:
    modules: List[str] = []
    context_stack: List[Tuple[int, str]] = []

    for raw_line in yaml_text.splitlines():
        if not raw_line.strip() or raw_line.strip().startswith("#"):
            continue

        indent = len(raw_line) - len(raw_line.lstrip(" "))

        while context_stack and indent <= context_stack[-1][0]:
            context_stack.pop()

        match = MODULE_LINE_PATTERN.match(raw_line)
        if not match:
            continue

        key = match.group(2)
        remainder = match.group(3)

        lowered_key = key.lower()

        if lowered_key in TASK_SECTION_KEYS or lowered_key in VAR_SECTION_KEYS:
            if remainder == "":
                context_stack.append((indent, lowered_key))
            continue

        # Add to context stack if this line opens a nested mapping (no inline value).
        if remainder == "":
            context_stack.append((indent, lowered_key))

        if lowered_key in STRUCTURAL_KEYS:
            continue

        if not any(section in TASK_SECTION_KEYS for _, section in context_stack):
            continue

        if any(section in VAR_SECTION_KEYS for _, section in context_stack):
            continue

        modules.append(key)

    return modules


def module_to_collection(module: str) -> str | None:
    if "." in module:
        parts = module.split(".")
        if len(parts) >= 2:
            if parts[0] == "ansible" and len(parts) >= 3:
                return ".".join(parts[:2])
            return ".".join(parts[:2])
    lowered = module.lower()
    for prefix, target in MODULE_PREFIX_COLLECTION_MAP:
        if lowered.startswith(prefix):
            return target
    return "ansible.builtin"


def infer_python_packages(collections: Iterable[str], modules: Iterable[str]) -> Set[str]:
    packages: Set[str] = set()
    for collection in collections:
        packages.update(COLLECTION_PACKAGE_DEFAULTS.get(collection, set()))
    for module in modules:
        packages.update(MODULE_PACKAGE_OVERRIDES.get(module, set()))
    return packages


def classify_record(
    collections: Iterable[str], python_packages: Iterable[str], modules: Iterable[str]
) -> str:
    azure_collections = any(coll.lower().startswith(AZURE_COLLECTION_PREFIXES) for coll in collections)
    aws_collections = any(coll.lower().startswith(AWS_COLLECTION_PREFIXES) for coll in collections)
    azure_packages = any(pkg.lower().startswith(AZURE_PACKAGE_PREFIXES) for pkg in python_packages)
    aws_packages = any(pkg.lower() in AWS_PACKAGE_NAMES for pkg in python_packages)
    azure_modules = any(module.lower().startswith(AZURE_MODULE_PREFIXES) for module in modules)
    aws_modules = any(module.lower().startswith(AWS_MODULE_PREFIXES) for module in modules)

    azure = azure_collections or azure_packages or azure_modules
    aws = aws_collections or aws_packages or aws_modules

    if azure and aws:
        return "cross-azure-aws"
    if azure:
        return "azure"
    if aws:
        return "aws"
    return "non-azure-aws"


def normalise_sequence(values: Iterable[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        if not value:
            continue
        if value not in seen:
            seen.add(value)
            result.append(value)
    return sorted(result)


def process_record(index: int, record: Dict[str, str]) -> RecordDependencies:
    yaml_text = record["yaml"]
    explicit_collections = extract_collections_from_yaml_text(yaml_text)
    modules = extract_modules_from_yaml_text(yaml_text)

    derived_collections: Set[str] = set(explicit_collections)
    for module in modules:
        collection = module_to_collection(module)
        if collection:
            derived_collections.add(collection)

    python_packages = infer_python_packages(derived_collections, modules)
    group = classify_record(derived_collections, python_packages, modules)

    return RecordDependencies(
        index=index,
        instruction=record["instruction"],
        yaml=yaml_text,
        collections=normalise_sequence(derived_collections),
        python_packages=normalise_sequence(python_packages),
        modules=modules,
        group=group,
    )


def write_jsonl(path: Path, records: Sequence[RecordDependencies]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            payload = {
                "index": record.index,
                "instruction": record.instruction,
                "yaml": record.yaml,
                "collections": record.collections,
                "python_packages": record.python_packages,
            }
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def write_dependency_summary(path: Path, collections: Set[str], python_packages: Set[str]) -> None:
    payload = {
        "collections": sorted(collections),
        "python_packages": sorted(python_packages),
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main() -> None:
    args = parse_args()

    if not args.input.exists():
        raise FileNotFoundError(f"Input file {args.input} does not exist")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_dir = args.output_dir / "jsonl"
    jsonl_dir.mkdir(parents=True, exist_ok=True)

    grouped_records: Dict[str, List[RecordDependencies]] = {
        "azure": [],
        "aws": [],
        "cross-azure-aws": [],
        "non-azure-aws": [],
    }
    grouped_collections: Dict[str, Set[str]] = {
        "azure": set(),
        "aws": set(),
        "cross-azure-aws": set(),
        "non-azure-aws": set(),
    }
    grouped_packages: Dict[str, Set[str]] = {
        "azure": set(),
        "aws": set(),
        "cross-azure-aws": set(),
        "non-azure-aws": set(),
    }

    with args.input.open("r", encoding="utf-8") as f:
        iterator = itertools.islice(f, args.limit) if args.limit is not None else f
        for idx, line in enumerate(iterator, start=1):
            record = json.loads(line)
            record_index = record.get("index", idx)
            dependencies = process_record(record_index, record)
            grouped_records[dependencies.group].append(dependencies)
            grouped_collections[dependencies.group].update(dependencies.collections)
            grouped_packages[dependencies.group].update(dependencies.python_packages)

    write_jsonl(jsonl_dir / "azure_records.jsonl", grouped_records["azure"])
    write_jsonl(jsonl_dir / "aws_records.jsonl", grouped_records["aws"])
    write_jsonl(jsonl_dir / "cross_azure_aws_records.jsonl", grouped_records["cross-azure-aws"])
    write_jsonl(jsonl_dir / "non_azure_aws_records.jsonl", grouped_records["non-azure-aws"])

    write_dependency_summary(
        args.output_dir / "azure_dependencies.json",
        grouped_collections["azure"],
        grouped_packages["azure"],
    )
    write_dependency_summary(
        args.output_dir / "aws_dependencies.json",
        grouped_collections["aws"],
        grouped_packages["aws"],
    )
    write_dependency_summary(
        args.output_dir / "cross_azure_aws_dependencies.json",
        grouped_collections["cross-azure-aws"],
        grouped_packages["cross-azure-aws"],
    )
    write_dependency_summary(
        args.output_dir / "non_azure_aws_dependencies.json",
        grouped_collections["non-azure-aws"],
        grouped_packages["non-azure-aws"],
    )


if __name__ == "__main__":
    main()

