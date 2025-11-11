# Stateless Workflow Orchestrator Architecture

This document captures the target design for the new stateless workflow engine that will drive all IaC optimizer flows. The engine owns orchestration, persistence, and control-flow; individual nodes focus purely on domain logic (subjective evaluators, objective validators, code optimizers, etc.). Everything the engine needs to resume, loop, or branch lives inside the workflow definition and the `optimization_flow.json` store.

---

## 1. Design Goals

1. **Stateless execution** – The engine reads a workflow definition and an existing `optimization_flow.json`, executes a single node at a time, writes results back, and discards in-memory state. Reruns continue from where the JSON store indicates.
2. **Declarative control** – A workflow JSON file fully describes execution order, conditional routing, loop behavior, and output destinations. No Python glue is required to compose nodes.
3. **Reusable nodes** – Every executable node exposes the same async interface. Nodes do not read or mutate the store directly; the engine performs all I/O.
4. **Traceability & provenance** – Each persisted output records timestamps, input provenance keys, and loop counters so that the optimization trail is auditable.
5. **Minimal surface** – Only two node types are required for iteration 1 (`execute` and `conditional`). More complex constructs (routers, parallelism) can be layered on top later without changing node contracts.

---

## 2. Workflow Configuration Schema

Workflows are JSON documents with the following top-level shape:

```json
{
  "name": "string",
  "code_type": "string",
  "vars": { "var_name": <literal> },
  "flow": [ { <node definition> }, ... ]
}
```

- **`vars`** – A dictionary of workflow-scoped variables initialised before execution. Variables are mutable; nodes and conditionals may read or write them through templating expressions. Typical examples: score thresholds, iteration counters, and the current `latest_manifest_key`.
- **`flow`** – Ordered set of nodes. The engine walks this list sequentially unless a node directs execution elsewhere (e.g., a conditional branch with `goto`).

### Example Workflow (`eval-transform-loop.json`)

```json
{
  "name": "eval-transform-loop",
  "code_type": "ansible",
  "vars": {
    "subjective_pass_score": 0.8,
    "subjective_warning_score": 0.6,
    "max_subjective_iterations": 2,
    "subjective_iteration_count": 0,
    "max_code_iterations": 5,
    "code_iteration_count": 0,
    "latest_manifest_key": "original_code"
  },
  "flow": [
    {
      "id": "subjective-eval-1",
      "type": "execute",
      "skipIfOutputPresent": true,
      "node": "ansible_nodes.subjective_evaluator_agent",
      "inputs": {
        "instruction": "instruction",
        "code": "{{latest_manifest_key}}"
      },
      "outputs": {
        "result": "optimization_flow.subjective_evaluation_{{++subjective_iteration_count}}",
        "instruction_key": "instruction",
        "code_key": "{{latest_manifest_key}}",
        "iterations": "{{subjective_iteration_count}}"
      }
    },
    {
      "id": "check-subjective-pass",
      "type": "conditional",
      "branches": [
        {
          "value": "optimization_flow.subjective_evaluation_{{subjective_iteration_count}}.weighted_overall_score",
          "condition": {
            "op": ">=",
            "compare_to": "{{subjective_pass_score}}"
          },
          "goto": "END"
        },
        {
          "value": "{{code_iteration_count}}",
          "condition": {
            "python": "value >= {{max_code_iterations}}"
          },
          "goto": "END"
        }
      ],
      "else": "ansible-code-optimizer-1"
    },
    {
      "id": "ansible-code-optimizer-1",
      "type": "execute",
      "skipIfOutputPresent": true,
      "node": "ansible_nodes.ansible_code_optimizer_agent",
      "inputs": {
        "instruction": "instruction",
        "before_code": "{{latest_manifest_key}}",
        "feedback": "optimization_flow.subjective_evaluation_{{subjective_iteration_count}}.scores"
      },
      "outputs": {
        "result": "optimization_flow.improved_code_B{{++code_iteration_count}}",
        "instruction_key": "instruction",
        "before_code_key": "{{latest_manifest_key}}",
        "feedback_key": "optimization_flow.subjective_evaluation_{{subjective_iteration_count}}.scores",
        "iterations": "{{code_iteration_count}}",
        "latest_manifest_key": "optimization_flow.improved_code_B{{code_iteration_count}}.code"
      }
    },
    {
      "id": "loop-eval",
      "type": "conditional",
      "branches": [
        {
          "value": "{{subjective_iteration_count}}",
          "condition": {
            "python": "value >= {{max_subjective_iterations}}"
          },
          "goto": "END"
        }
      ],
      "else": "subjective-eval-1"
    }
  ]
}
```

The flow performs a subjective evaluation, conditionally terminates if the score passes threshold or the iteration limit is hit, runs the optimizer otherwise, and loops back until `max_subjective_iterations` is reached.

---

### Validation Workflow (`validate-transform-loop.json`)

The validation-driven loop mirrors the subjective example but swaps in the objective analyzer and routes on its findings:

```json
{
  "name": "validate-transform-loop",
  "code_type": "ansible",
  "vars": {
    "max_validation_iterations": 2,
    "validation_iteration_count": 1,
    "last_validation_result_key": "",
    "max_code_iterations": 1,
    "code_iteration_count": 1,
    "latest_manifest_key": "original_code"
  },
  "flow": [
    { "...": "resume-validation-check" },
    { "...": "objective-validation" },
    { "...": "check-validation-result" },
    { "...": "ansible-code-optimizer" },
    { "...": "loop-continue" }
  ]
}
```

- **`objective-validation` execute node**
  - `node`: `ansible_optimizer.ansible_nodes.ansible_validation_analyzer.AnsibleValidationAnalyzerNode`
  - Inputs: current instruction, the latest code reference (the engine resolves `{{latest_manifest_key}}` to either the original code or the most recent optimizer output), and an optional `max_turns` override for longer validation runs (examples use `8`).
  - Outputs: writes the analyzer payload to `optimization_flow.objective_validation_N` and updates the workflow variables:
    - `validations_result` (pass/fail string) mirrors `objective_validations.overall_result`.
    - `manifest_fix_required` boolean flags whether any failing check is caused by code issues.
    - `result_analysis` summarises the first actionable failure reason.
    - `analysis_details` captures a human-readable, multi-line breakdown; `analysis_details_items` retains the structured list for programmatic checks.
    - `vars.last_validation_result_key` stores the JSON path to the latest analyzer output for downstream conditionals.
- **Conditionals**
  - `resume-validation-check` short-circuits reruns: if the latest analyzer already passed or determined no fix is required, execution stops immediately on re-entry.
  - `check-validation-result` decides between exiting and invoking the optimizer based on the analyzer verdict and quota limits.
- **`ansible-code-optimizer` execute node**
  - Receives `feedback` as the entire analyzer result block (e.g., the optimizer can read `feedback["analysis_details"]` to explain failures in its prompt).
  - Accepts an optional `max_turns` input (string or number) to override the default conversation length with the Codex MCP server (examples use `12`).
  - Updates `vars.latest_manifest_key` to the newly generated manifest path before the loop repeats.

#### Validator runtime configuration

The analyzer defaults to the automation virtual environment located at `ansible_optimizer/.venv-automation/bin/python` and the MCP server script `ansible_optimizer/automation/validator_mcp_server.py`. Override paths on a per-node basis with the optional `validator_python` or `validator_server` inputs:

```json
{
  "node": "ansible_optimizer.ansible_nodes.ansible_validation_analyzer.AnsibleValidationAnalyzerNode",
  "inputs": {
    "manifest": "{{latest_manifest_key}}",
    "validator_python": "/custom/venv/bin/python",
    "validator_server": "/custom/path/validator_mcp_server.py"
  }
}
```

Set `ee_image` when you need a different execution environment image for ansible-navigator validations. The analyzer transparently handles inline manifest content, writing it to a temporary file for the MCP server.

##### Validator MCP tools

The automation server exposes two MCP tools that the analyzer (or any MCP client) can call:

- `validator.validate_manifest` – runs kubconform and kube-linter and returns the structured `objective_validations` payload shown above.
- `validator.validate_manifest` – runs kubconform and kube-linter against the provided manifest and returns a structured `objective_validations` payload (kubconform + kube-linter blocks).

---

## 3. Placeholder & Variable Semantics

Templating applies to every string value in the workflow definition (`inputs`, `outputs`, conditions). The engine supports:

- **Direct substitution** – `{{var}}` inserts the current value of a workflow variable.
- **JSON path substitution** – Any templated string can resolve to a JSON path within the store. For example `optimization_flow.subjective_evaluation_{{subjective_iteration_count}}.scores` becomes `optimization_flow.subjective_evaluation_2.scores` after interpolation.
- **Pre/Post increments**  
  - `{{++var}}` increments `var` first, then yields the new value.  
  - `{{var++}}` (if used) yields the current value, then increments.  
  Both update the variable inside the persisted `vars` block.
- **Arbitrary Python snippets** – Conditions may include `"python": "value >= {{max_code_iterations}}"`. The engine evaluates the expression with:
  - `value` = resolved current value (from `branches[i].value`).
  - `vars` = latest variable dictionary.
  - Read-only `store` = entire `optimization_flow.json` payload.

All substitutions happen before node execution or condition evaluation. After a node runs, updated variables are written back to `optimization_flow.json` so the next run starts from the new state.

---

## 4. Node Types

### 4.1 Execute Nodes

Executable nodes subscribe to a shared protocol located in `workflow_orchestrator/interfaces.py` (implementation iteration will add the actual module). The interface is:

```python
from __future__ import annotations

from typing import Any, Dict, Protocol, runtime_checkable

JsonValue = Dict[str, Any] | list | str | int | float | bool | None

@runtime_checkable
class ExecutableNode(Protocol):
    async def evaluate(self, context: Dict[str, JsonValue], **params: JsonValue) -> Dict[str, JsonValue]:
        """Return JSON-friendly outputs using only the supplied context and params."""
```

An `execute` node imports a module and calls its `evaluate(context, **params)` coroutine. The engine never dictates concrete classes; any module exposing a top-level async `evaluate` function, or a class with such a coroutine, satisfies the contract.

```python
async def evaluate(context: dict, **params) -> dict:
    """
    context: Current in-memory copy of optimization_flow.json (including vars).
    params:  Keyword arguments resolved from the `inputs` map.
    Returns: Dict whose keys line up with entries in the node's `outputs`.
    """
```

- `context` is read-only; nodes should treat it as informational.
- The engine passes each `inputs` entry as `params[name] = resolved_value`.
- The coroutine returns JSON-serialisable structures.

### 4.2 Conditional Nodes

Conditional nodes steer control flow. Structure:

```json
{
  "id": "check-subjective-pass",
  "type": "conditional",
  "branches": [
    {
      "value": "<path or literal>",
      "condition": { "op": ">=", "compare_to": "<templated literal>" },
      "goto": "<node-id or END>"
    },
    {
      "value": "<path or literal>",
      "condition": { "python": "<expression>" },
      "goto": "<node-id or END>"
    }
  ],
  "else": "<node-id>"
}
```

The engine evaluates each branch in order:

1. Resolve `value` (respecting templating). If a JSON path is provided, the engine reads that location from the store.
2. Evaluate `condition`:
   - Comparator format: `{ "op": ">=", "compare_to": "{{subjective_pass_score}}" }`.
   - Python format: `{ "python": "value >= {{max_code_iterations}}" }`.
3. On first truthy branch, jump to `goto`. `goto: "END"` terminates the workflow.
4. If no branch matches, continue with the node id in `else`. `else` is required; use `"else": "END"` if the node should fall through to completion.

Conditional nodes never modify the store directly. They may, however, rely on variables that previous nodes changed.

---

## 5. Engine Responsibilities

For each node the engine:

1. **Reload state** – Read `optimization_flow.json` fresh before processing the node to capture any external edits.
2. **Apply templating** – Resolve all placeholders in the node definition using the latest `vars` and store contents.
3. **Skip completed outputs** – If `skipIfOutputPresent` is true, the engine inspects the resolved primary output path (`outputs.result` by convention). If a non-null value exists, the node is skipped.
4. **Execute**  
   - For `execute` nodes, dynamically import the module and call `await module.evaluate(context, **params)`. Sync callables are wrapped with `asyncio.to_thread`.
   - For `conditional` nodes, evaluate branches and select the next node without invoking external code.
5. **Persist outputs** – For every entry in `outputs`:
   - Create any intermediate objects in the store.
   - Write `value` and add `"created_at": <UTC ISO 8601>` siblings when the entry is an object (for primitive outputs append `created_at` on a parallel metadata key if needed).
   - For provenance keys (`*_key`) store the literal string verbatim.
6. **Update variables** – After templating, any variable mutations (e.g., `{{++subjective_iteration_count}}`) are reflected in the in-memory `vars` map. The engine writes the updated map back to `optimization_flow.json` before the next node runs.
7. **Determine the next node** – By default advance sequentially. Conditional nodes override this via `goto`. `"END"` stops execution immediately.

The engine itself remains stateless because it only keeps the current node in memory. Everything else is re-derived from disk at each step.

---

## 6. Expected `optimization_flow.json` Output (Sample Run)

Running the example workflow above on a dataset with `instruction` and `original_code` fields produces a store similar to:

```json
{
  "instruction": "Document the current APIC tenants and create a Hetzner network in the same manifest.",
  "original_code": "... original manifest YAML ...",
  "optimization_flow": {
    "subjective_evaluation_1": {
      "created_at": "2024-04-15T09:02:11.712349Z",
      "instruction_key": "instruction",
      "code_key": "original_code",
      "iterations": 1,
      "scores": {
        "syntax_correctness": {
          "score": 3,
          "reason": "Manifest is valid YAML and task structure parses."
        },
        "structural_accuracy": {
          "score": 3,
          "reason": "Uses Cisco APIC and Hetzner modules appropriately."
        },
        "parameter_accuracy": {
          "score": 1,
          "reason": "Tenant query omits required filters; Hetzner task lacks token."
        },
        "completeness": {
          "score": 1,
          "reason": "Tenant data isn't persisted to disk."
        },
        "best_practice_refinements": {
          "score": 1,
          "reason": "Credential handling and idempotency guidance missing."
        },
        "weighted_overall_score": 0.62
      }
    },
    "improved_code_B1": {
      "created_at": "2024-04-15T09:05:44.587920Z",
      "instruction_key": "instruction",
      "before_code_key": "original_code",
      "feedback_key": "optimization_flow.subjective_evaluation_1.scores",
      "iterations": 1,
      "latest_manifest_key": "optimization_flow.improved_code_B1.code",
      "code": "... first improved manifest YAML ..."
    },
    "subjective_evaluation_2": {
      "created_at": "2024-04-15T09:10:22.041118Z",
      "instruction_key": "instruction",
      "code_key": "optimization_flow.improved_code_B1.code",
      "iterations": 2,
      "scores": {
        "syntax_correctness": {
          "score": 3,
          "reason": "Still parses cleanly; tasks grouped correctly."
        },
        "structural_accuracy": {
          "score": 2,
          "reason": "Hetzner network creation added but APIC query not isolated."
        },
        "parameter_accuracy": {
          "score": 2,
          "reason": "Adds token handling, but subnet mask value is off."
        },
        "completeness": {
          "score": 2,
          "reason": "Tenant data registered but still not written out."
        },
        "best_practice_refinements": {
          "score": 2,
          "reason": "Comments cover credentials; validation tasks still missing."
        },
        "weighted_overall_score": 0.68
      }
    },
    "improved_code_B2": {
      "created_at": "2024-04-15T09:14:06.903512Z",
      "instruction_key": "instruction",
      "before_code_key": "optimization_flow.improved_code_B1.code",
      "feedback_key": "optimization_flow.subjective_evaluation_2.scores",
      "iterations": 2,
      "latest_manifest_key": "optimization_flow.improved_code_B2.code",
      "code": "... second improved manifest YAML ..."
    }
  },
  "vars": {
    "subjective_pass_score": 0.8,
    "subjective_warning_score": 0.6,
    "max_subjective_iterations": 2,
    "subjective_iteration_count": 2,
    "max_code_iterations": 5,
    "code_iteration_count": 2,
    "latest_manifest_key": "optimization_flow.improved_code_B2.code"
  }
}
```

Key characteristics:

- Each node execution writes a new object with `created_at`, provenance keys, and iteration counters.
- Counter variables (`subjective_iteration_count`, `code_iteration_count`) persist under `vars`.
- `latest_manifest_key` always points to the most recently accepted code artifact so subsequent nodes resolve the right input without bespoke logic.
- The workflow stops once `subjective_iteration_count` reaches `max_subjective_iterations` even though no passing score was achieved.

---

## 7. Engine Implementation Outline

1. **Config loading & validation**
   - Use Pydantic models (or similar) to validate node shapes, required fields, and placeholder syntax prior to execution.
   - Normalise node ids and ensure branch `goto` targets exist.
2. **JSON store utilities**
   - `resolve_path(store, path: str) -> Any` supporting dot notation and list indices.
   - `write_path(store, path: str, value: Any) -> None` that creates intermediate dicts.
   - Metadata injection helpers for `created_at` and provenance keys.
3. **Templating engine**
   - Tokenise `{{ ... }}` expressions.
   - Maintain a mutable `vars` dictionary; expose convenience functions for `++`/`--`.
   - Support string interpolation inside longer paths (`"optimization_flow.subjective_evaluation_{{n}}"`).
4. **Execution loop**
   - Iterate through nodes, updating the index based on conditional results.
   - For `execute` nodes, import modules at runtime with `importlib`.
   - Provide the node with a shallow copy of the current store (`context`) to avoid accidental mutation.
5. **Persistence**
   - After each node, flush the updated store (with new outputs and mutated `vars`) back to disk.
   - Optionally keep a `.bak` file for crash recovery.
6. **Logging & observability**
   - Log node start/finish, skips, and branch decisions with resolved variable values.
   - Capture node outputs in debug logs (subject to redaction rules).

---

## 8. Testing Strategy

1. **Templating unit tests**
   - Variable substitution, pre/post increment semantics, nested path expansion.
   - Python condition evaluation with injected variables.
2. **JSON path utilities**
   - Resolving paths, creating intermediate structures, verifying `created_at` stamping.
3. **Node execution tests**
   - Fake node modules that echo inputs to confirm the engine wires data correctly.
   - `skipIfOutputPresent` scenarios where reruns avoid re-execution.
4. **Conditional routing**
   - Branch prioritisation, `else` fall-through, `goto: "END"`.
5. **End-to-end workflow**
  - Use the `eval-transform-loop` example with stubbed evaluator/optimizer modules that manipulate loop counters deterministically.
   - Assert the final store matches the structure in Section 6 (minus textual reasoning that the stub nodes cannot produce).
6. **Integration**
   - Run against real nodes (`ansible_nodes.subjective_evaluator_agent`) with dataset index 1 to ensure external orchestration behaves as expected once API keys are provided.
   - All tests must run inside the dedicated Python 3.13 virtual environment created for this repository.

---

## 9. Directory Layout (Iteration 1)

```
IaC-optimizers/
├── ansible_optimizer/
│   └── ansible_nodes/
│       ├── __init__.py
│       ├── subjective_evaluator_agent.py
│       ├── subjective_evaluator_prompt.txt
│       └── (future) ansible_code_optimizer_agent.py
├── workflow_orchestrator/
│   ├── stateless-workflow-architecture.md
│   ├── engine.py              # upcoming implementation
│   ├── flow_examples/
│   │   ├── eval-transform.json
│   │   ├── eval-transform-loop.json
│   │   └── loop-with-counters.json
│   └── tests/
│       └── (engine unit & integration tests)
└── requirements.txt
```

This architecture keeps node logic under domain-specific packages (e.g., `ansible_optimizer`) while the generic orchestration runtime, configuration files, and tests live under `workflow_orchestrator`.

---

## 10. Iteration Plan

### Completed Iterations

- **Iteration 1 – Executable Nodes**
  - Implemented the shared `ExecutableNode` protocol (exposed via the repository-level `interfaces.py`).
  - Refactored the Kubernetes subjective evaluator to honour the interface, calculate weighted scores in Python, and avoid direct mutations of the store.
  - Added live integration tests that call the evaluator through the protocol using `ansible_optimizer/dataset/1/optimization_flow.json` and `optimization_flow1.json`, with outputs logged to stdout.
  - Introduced the Kubernetes manifest optimizer node, wired to Codex MCP, returning normalised JSON payloads.
  - Added optimizer integration tests exercising runs with and without feedback using the same datasets.
  - Outstanding follow-ups:
    - Re-run `python -m unittest discover ansible_optimizer/tests -v` inside the Python 3.13 venv to confirm optimizer tests succeed after the package renaming.
    - Document the agent test workflow in `ansible_optimizer/tests/README.md` (venv setup, required env vars, and commands for both suites).

### Iteration 1 – Executable Nodes

- **Deliverables**
  - Implement `ExecutableNode` protocol in `workflow_orchestrator/interfaces.py`.
  - Update `ansible_nodes.subjective_evaluator_agent` to expose `async def evaluate(context, **params)` conforming to the interface, computing metric weights in Python and returning `weighted_overall_score` with metric breakdowns.
  - Create `ansible_nodes.ansible_code_optimizer_agent` implementing the same interface, leveraging the Codex CLI MCP tool chain.
  - Author live tests under `ansible_optimizer/tests/` (or new path under the refreshed structure) that exercise each agent against:
    - `/Users/bj/playground/slm/IaC-Optimizers-workflow/ansible_optimizer/dataset/1/optimization_flow.json`
    - `/Users/bj/playground/slm/IaC-Optimizers-workflow/ansible_optimizer/dataset/1/optimization_flow1.json`
    The tests must instantiate the modules strictly through the `ExecutableNode` protocol, make real OpenAI API calls, and validate schema compliance.
  - Every development and test command in this iteration must run inside the shared Python 3.13 virtual environment.
- **Supporting assets**
  - Sample prompts or tool utility functions.
  - Requirements files capturing runtime dependencies (OpenAI SDK, etc.).

### Iteration 2 – Engine Implementation (Sub-Iterations)

- **Overall Deliverable**
  - Implement the stateless workflow engine in `workflow_orchestrator/engine.py`, fully supporting templating, variable mutation, branching, skip logic, persistence, timestamping, provenance recording, and integration with live nodes. All code and tests run inside the shared Python 3.13 virtual environment.

- **Sub-Iteration 2.1 – Configuration & Data Models**
  - Create Pydantic (or equivalent) models for workflow files (`vars`, node definitions, conditional branches).
  - Implement config loader/validator that reports schema issues with actionable error messages.
  - Tests:
    - Valid configurations round-trip through the models.
    - Invalid configs (missing ids, duplicate ids, bad templating syntax) raise precise exceptions.

- **Sub-Iteration 2.2 – JSON Store & Path Utilities**
  - Implement helpers (`resolve_path`, `write_path`, metadata injection) operating on in-memory copies of `optimization_flow.json`.
  - Add timestamp/provenance writer utilities (handles `created_at`, `_key` fields).
  - Tests:
    - Path resolution for nested dicts/lists, including non-existent parents.
    - Output writes create missing containers and stamp metadata correctly.
    - Store mutations remain isolated per operation.

- **Sub-Iteration 2.3 – Templating & Variable Engine**
  - Build templating module handling `{{var}}`, `{{++var}}`, `{{var++}}`, JSON-path substitutions, and literal escape rules.
  - Maintain mutable `vars` context synced back to `optimization_flow.json`.
  - Tests:
    - Variable substitution (string interpolation, nested paths).
    - Pre/post increment semantics with bounds checking.
    - Python-condition templates receive correct data (dry-run via utility function).

- **Sub-Iteration 2.4 – Execute Node Runner**
  - Implement asynchronous execution pipeline:
    - Skip logic (`skipIfOutputPresent`).
    - Dynamic import of nodes via module path.
    - Execution context creation and result persistence (including timestamps & provenance).
  - Tests:
    - Stub nodes returning deterministic outputs.
    - Skip behaviour when outputs already exist.
    - Error propagation when nodes return malformed payloads.

- **Sub-Iteration 2.5 – Conditional Node Engine**
  - Implement conditional node evaluation with ordered branches, comparator conditions, Python snippet conditions, and `goto` routing (including `"END"`).
  - Allow optional mutation of variables inside conditions (if templating expressions include increments).
  - Tests:
    - Branch ordering, fall-through to `else`.
    - Python expression evaluation with workflow vars and store context.
    - Loop routing scenarios (rewind to earlier node, exit on `"END"`).

- **Sub-Iteration 2.6 – End-to-End Workflow Execution**
  - Wire the full engine loop (load store, execute nodes sequentially, respect `goto` targets).
  - Provide CLI entry point or helper function to run `engine.execute(workflow_path, optimization_flow_path)`.
  - Tests:
    - Integration run with stub nodes reproducing the expected `optimization_flow.json` structure from Section 6.
    - Live smoke test using `ansible_optimizer/ansible_nodes/subjective_evaluator_agent` and `ansible_code_optimizer_agent` against dataset `ansible_optimizer/dataset/1/optimization_flow.json`; capture outputs to verify compatibility.
    - Validation that rerunning the workflow skips nodes flagged with `skipIfOutputPresent`.

- **Sub-Iteration 2.7 – Documentation & Tooling**
  - Update README/test instructions with commands for running engine unit tests and live workflows.
  - Add sample CLI usage and troubleshooting notes (e.g., missing API keys, Codex MCP requirements).
  - Tests: docs lint or automated check optional; manual verification checklist.

- **Integration Expectations**
  - Maintain deterministic unit tests by using stub nodes; reserve live API calls for dedicated integration tests that can be toggled via environment flag.
  - Post-implementation, rerun the live subjective evaluator/optimizer tests to confirm end-to-end compatibility from the engine entry point.

---

With these specifications the implementation team can now build the engine: templating, conditional routing, variable management, and persistence are all fully described; the example workflow and resulting `optimization_flow.json` provide a concrete target for acceptance tests.

---

### Iteration 3 – Validation Analyzer (Sub-Iterations)

- **Overall Deliverable**
  - Added a `KubernetesValidationAnalyzer` executable node that uses the validator MCP server, analyses the returned JSON, and emits a structured result indicating whether the manifest needs further fixes.

- **Sub-Iteration 3.1 – MCP Input Support**
  - Extend the validator MCP wrapper to accept optional `manifest_content` in addition to a file path.
  - When `manifest_content` is provided, write it to a temp file (timestamped) and invoke `run_validations.py` with that file path. Existing behaviour remains unchanged.
  - Tests: unit tests (mocking subprocess) to verify temp-file creation, command invocation, and cleanup.

- **Sub-Iteration 3.2 – Validation Analyzer Node**
  - Implement `ansible_optimizer/ansible_nodes/ansible_validation_analyzer.py`:
    - Inputs: manifest path/content, optional instruction for context, optional validator env settings.
    - Behaviour: call validator MCP, parse JSON, derive `validations_result`, `manifest_fix_required`, `result_analysis`, and return the full `objective_validations` block.
  - Tests:
    - Unit tests with mocked MCP responses (pass/fail/malformed payloads).
    - Guard clauses ensuring missing manifest data raises clear errors.

- **Sub-Iteration 3.3 – Workflow Integration & Config**
  - Create `workflow_orchestrator/flow-examples/validate-transform-loop.json`, mirroring the subjective loop but driven by the validation analyzer.
  - Conditionals should branch on `validations_result` and `manifest_fix_required` to decide whether to transform or exit.
  - Document required configuration (validator venv path, optional args).

- **Sub-Iteration 3.4 – Test Coverage**
  - Unit tests: validation analyzer node logic, workflow routing decisions.
  - Integration tests:
    1. Controlled run with a sample manifest that triggers a known validation failure (requires validator dependencies).
    2. Happy-path run where validations pass and the workflow exits immediately.
  - Document how to execute live tests (`python -m workflow_orchestrator.engine workflow_orchestrator/flow-examples/validate-transform-loop.json ...`).

- **Acceptance Criteria**
  - Validation analyzer node returns the expected JSON shape.
  - Workflow terminates when validations pass and no fix is required.
  - Failing validations lead to another transform iteration when `manifest_fix_required` is true.
  - Unit and integration tests pass in the Python 3.13 virtual environment.
