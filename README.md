# IaC-Optimizers Workflow

Stateless orchestration for infrastructure-as-code optimizers. The repository contains:

- **`workflow_orchestrator/`** — engine, sample workflows, batch tooling, and architecture documentation.
- **`ansible_optimizer/`** — (legacy name) hosts the Kubernetes-focused optimizer nodes, automation helpers, datasets, and live integration tests.
- **`ansible_optimizer/automation/`** — kubconform/kube-linter runner, MCP server, and related tooling used by the workflows.
- **`ansible_optimizer/dataset/<index>/optimization_flow.json`** — per-dataset state stores that capture instructions, original manifests, and every workflow output.

The engine remains stateless: each workflow run loads the JSON datastore, executes a node, persists results, and exits. Reruns resume exactly where the previous run stopped.

---

## Quick Start

The provided [workflows](./workflow_orchestrator/flow-examples/) now target Kubernetes manifest optimization pipelines. The dataset entries mirror the structure in [ansible_optimizer/dataset](./ansible_optimizer/dataset).

### Prerequisites

1. **Install Python 3.13** and create two virtual environments:

   Automation environment (used by the validator MCP server):
   ```bash
   python3.13 -m venv ansible_optimizer/.venv-automation
   source ansible_optimizer/.venv-automation/bin/activate
   pip install -r ansible_optimizer/automation/requirements.txt
   deactivate
   ```

   Workflow environment (keep this one activated for day-to-day work):
   ```bash
   python3.13 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Provide credentials**

   Copy `.env` into `ansible_optimizer/.env` (or export variables directly) with an `OPENAI_API_KEY` for the evaluator/optimizer agents.

3. **Install Codex CLI**

   The optimizer node drives the Codex CLI locally. Install and verify that `codex` launches successfully:
   <https://developers.openai.com/codex/cli/>

4. **Install validation tooling**

   Ensure [`kubconform`](https://github.com/yannh/kubconform) and [`kube-linter`](https://github.com/stackrox/kube-linter) binaries are available on your `PATH`, or export `KUBECONFORM_BIN` / `KUBE_LINTER_BIN`.

### Run a workflow

With the workflow environment active (`source .venv/bin/activate`), run a dataset entry—e.g., index 1:

```bash
./workflow_orchestrator/workflow-batch-runner.sh \
  --workflow workflow_orchestrator/flow-examples/eval-validate-transform-loop.json \
  --batch-size 1 \
  --start-index 1 \
  --number-of-batches 1
```

`workflow_orchestrator/workflow-batch-runner.sh` supports batching (default size: 5) to process multiple manifests in parallel. Example for datasets 1‑5:

```bash
./workflow_orchestrator/workflow-batch-runner.sh \
  --workflow workflow_orchestrator/flow-examples/eval-validate-transform-loop.json \
  --batch-size 5 \
  --start-index 1 \
  --number-of-batches 1
```

Outputs:

- Workflow results: `ansible_optimizer/dataset/<index>/optimization_flow.json`
- Logs: `workflow_orchestrator/logs/workflow_<index>.log`
- Markdown summary: `workflow_orchestrator/reports/workflow_results_<timestamp>.md`

Useful flags:

- `--workflow PATH` — path to the workflow config (defaults to `workflow_orchestrator/flow-examples/eval-validate-transform-loop.json`).
- `--batch-size N` — number of datasets per batch (defaults to 5).
- `--start-index N` — 1-based dataset index.
- `--number-of-batches N` — how many batches to run.
- `--update-report [FILE]` — rehydrate metrics in an existing report without rerunning workflows.

---

## Development Tests

- **Node integration tests** (optimizer, subjective evaluator, validation analyzer):  
  `source .venv/bin/activate && python -m unittest discover ansible_optimizer/tests -v`

- **Engine unit tests**:  
  `source .venv/bin/activate && python -m unittest discover workflow_orchestrator/tests -v`

Some tests require network access (OpenAI API plus kubconform/kube-linter binaries). Install prerequisites before running the suite.

### Manual validator check

The validator MCP server does not use OpenAI. It shells out to kubconform and kube-linter:

```bash
cd ansible_optimizer
source .venv-automation/bin/activate
python automation/run_validations.py path/to/manifest.yaml
```

Refer to [`workflow_orchestrator/stateless-workflow-architecture.md`](workflow_orchestrator/stateless-workflow-architecture.md) for architectural details.
