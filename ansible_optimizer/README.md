# Kubernetes Optimizer Nodes (legacy package name)

This package hosts the reusable nodes, datasets, automation utilities, and integration tests that power the Kubernetes manifest optimization workflows.

## Contents

- **Nodes** (`ansible_nodes/` — path retained for compatibility):
  - `kubernetes_manifest_optimizer_agent.py` — Codex MCP-enabled optimizer that produces improved manifest variants.
  - `subjective_evaluator_agent.py` — GPT-5 reviewer scoring manifests across schema, configuration, security, resilience, and best-practice metrics.
  - `kubernetes_validation_analyzer.py` — OpenAI agent connected to the validator MCP tool for kubconform/kube-linter checks.
- **Datasets** (`dataset/<index>/optimization_flow.json`): Canonical inputs containing `instruction`, `original_manifest`, and existing workflow outputs.
- **Automation** (`automation/`): kubconform/kube-linter runner, MCP server, Makefile helper, and supplemental documentation.
- **Tests** (`tests/`): Live integration suites validating node contracts, Codex interactions, MCP invocations, and JSON schemas.

## Prerequisites

1. **Primary virtual environment**
   ```bash
   python3.13 -m venv .venv
   source .venv/bin/activate
   pip install -r ../requirements.txt
   ```
2. **Credentials**
   - Create `ansible_optimizer/.env` and set:
     ```
     OPENAI_API_KEY=sk-...
     ```
   - Install the Codex CLI so `npx codex mcp-server` works for the optimizer node.
3. **Automation validator environment**
   ```bash
   python3.13 -m venv .venv-automation
   source .venv-automation/bin/activate
   pip install -r automation/requirements.txt
   pip install -r automation/tests/requirements-test.txt
   ```
   Ensure `kubconform` and `kube-linter` binaries are on `PATH` (or export `KUBECONFORM_BIN` / `KUBE_LINTER_BIN`).
4. Optional: set `DATASET_INDEX` (defaults to `1`) to target a different dataset folder during testing.

## Running Tests

All suites hit real services (OpenAI + kubconform + kube-linter). Activate `.venv`, export your `OPENAI_API_KEY`, and run:

```bash
python -m unittest discover ansible_optimizer/tests -v
```

Key suites:

- `test_kubernetes_manifest_optimizer_agent.py`
- `test_subjective_evaluator_agent.py`
- `test_kubernetes_validation_analyzer.py`

Each test prints the structured payloads returned by the nodes for manual inspection.

## Notes

- Nodes implement `interfaces.ExecutableNode` and are consumed by the workflow orchestrator.
- Dataset entries can be duplicated/modified to seed new optimization scenarios.
- The package name remains `ansible_optimizer` to avoid breaking existing import paths; new code can treat it as the Kubernetes optimizer module.
