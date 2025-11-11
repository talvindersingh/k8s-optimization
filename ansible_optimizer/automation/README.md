# Automation Overview

This directory hosts the automation utilities that back the Kubernetes manifest validation workflow. The primary entry point is `run_validations.py`, which executes [kubconform](https://github.com/yannh/kubconform) and [kube-linter](https://github.com/stackrox/kube-linter) against a manifest and emits structured results that downstream agents can consume.

## Prerequisites

1. Clone or switch to the IaC Optimizers workspace.
2. (Recommended) Create and activate a Python virtual environment:

   ```bash
   cd IaC-Optimizers-Workflow/ansible_optimizer
   python3.13 -m venv .venv-automation
   source .venv-automation/bin/activate
   pip install -r automation/requirements.txt
   ```

3. Ensure `kubconform` and `kube-linter` binaries are installed and available on your `PATH`. You can also point to alternative executables via environment variables (`KUBECONFORM_BIN`, `KUBE_LINTER_BIN`) or Makefile variables.

## Running Validations

Use the provided `Makefile` wrapper for convenience:

```bash
cd IaC-Optimizers-Workflow/ansible_optimizer/automation
make validate MANIFEST=../dataset/example/deployment.yaml NAME=my-deployment
```

The command runs both tools, prints a readable summary, and finishes by emitting a JSON payload with an `objective_validations` object that includes `kubconform` and `kube-linter` results. The script returns a non-zero exit code when either validation fails.

Additional options:

- `KUBECONFORM_ARGS="--strict"` to forward flags to kubconform.
- `KUBE_LINTER_ARGS="--format json"` to change kube-linter output mode.
- `KUBECONFORM_BIN` / `KUBE_LINTER_BIN` to override the executable path.

The MCP server in this directory (`validator_mcp_server.py`) exposes the same validations through a `validate_manifest` tool that other agents can call.
