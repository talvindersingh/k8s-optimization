"""Pydantic models for workflow configuration files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class BranchCondition(BaseModel):
    """Represents a conditional branch predicate."""

    op: Optional[str] = None
    compare_to: Optional[str] = Field(default=None, alias="compare_to")
    python: Optional[str] = None

    model_config = ConfigDict(extra="forbid", validate_by_name=True)

    @model_validator(mode="after")
    def validate_mode(self) -> "BranchCondition":
        has_comparison = self.op is not None or self.compare_to is not None
        if self.python and has_comparison:
            raise ValueError("Condition cannot mix 'python' expression with comparator fields.")
        if self.python:
            if not self.python.strip():
                raise ValueError("Python condition must not be empty.")
            return self
        if self.op is None and self.compare_to is None:
            raise ValueError("Condition must define either a python expression or an op/compare_to pair.")
        if self.op is None or self.compare_to is None:
            raise ValueError("Comparator conditions require both 'op' and 'compare_to'.")
        return self


class ConditionalBranch(BaseModel):
    """Single branch definition within a conditional node."""

    value: Optional[str] = None
    condition: Optional[BranchCondition] = None
    goto: str = Field(...)

    model_config = ConfigDict(extra="forbid", validate_by_name=True)

    @model_validator(mode="after")
    def validate_branch(self) -> "ConditionalBranch":
        if self.condition and not (self.value or self.condition.python):
            # Branch condition referencing a comparator requires a value.
            if self.condition.op and not self.value:
                raise ValueError("Comparator-based branches must provide a 'value'.")
        return self


class BaseNode(BaseModel):
    """Common fields shared by all node types."""

    id: str

    model_config = ConfigDict(extra="forbid")

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("Node id must be a non-empty string.")
        return value


class ExecuteNode(BaseNode):
    """Node that executes a module implementing the ExecutableNode protocol."""

    type: Literal["execute"] = "execute"
    node: str
    inputs: Dict[str, str] = Field(default_factory=dict)
    outputs: Dict[str, str] = Field(default_factory=dict)
    skip_if_output_present: bool = Field(default=False, alias="skipIfOutputPresent")

    model_config = ConfigDict(extra="forbid", validate_by_name=True)

    @field_validator("node")
    @classmethod
    def validate_node(cls, value: str) -> str:
        if not value or "." not in value:
            raise ValueError("Execute node 'node' field must be a dotted module path.")
        return value


class ConditionalNode(BaseNode):
    """Node that evaluates conditions and routes control flow."""

    type: Literal["conditional"] = "conditional"
    branches: List[ConditionalBranch]
    else_goto: str = Field(..., alias="else")

    model_config = ConfigDict(extra="forbid", validate_by_name=True)

    @model_validator(mode="after")
    def validate_branches(self) -> "ConditionalNode":
        if not self.branches:
            raise ValueError("Conditional nodes require at least one branch.")
        return self


WorkflowNode = Annotated[ExecuteNode | ConditionalNode, Field(discriminator="type")]


class WorkflowConfig(BaseModel):
    """Complete workflow definition."""

    name: str
    code_type: str = Field(..., alias="code_type")
    vars: Dict[str, Any] = Field(default_factory=dict)
    flow: List[WorkflowNode]

    model_config = ConfigDict(extra="forbid", validate_by_name=True)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "WorkflowConfig":
        seen: set[str] = set()
        for node in self.flow:
            if node.id in seen:
                raise ValueError(f"Duplicate node id detected: '{node.id}'")
            seen.add(node.id)
        if not self.flow:
            raise ValueError("Workflow must contain at least one node in 'flow'.")
        return self


def load_workflow_config(path: Path | str) -> WorkflowConfig:
    """Read and validate a workflow configuration from a JSON file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Workflow configuration not found: {path}")

    payload = json.loads(path.read_text(encoding="utf-8"))
    return WorkflowConfig.model_validate(payload)
