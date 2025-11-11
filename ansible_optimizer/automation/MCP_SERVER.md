# Kubernetes Validator MCP Server

This MCP (Model Context Protocol) server exposes the Kubernetes manifest validation workflow as a tool that clients (IDEs, automation frameworks, assistants) can call. The server wraps `run_validations.py`, which runs kubconform and kube-linter and emits structured results.

## What is MCP?

MCP is a standard protocol for surfacing external tools and resources to AI assistants. It allows models to invoke utilities such as validators, linters, or data sources over a simple JSON-RPC channel.

## Features

The `validate_manifest` tool performs:

1. **kubconform** — validates manifests against Kubernetes schemas.
2. **kube-linter** — checks for best-practice and security issues.

Results are returned as a JSON payload that contains an `objective_validations` object with individual tool statuses plus an overall summary.

## Installation

```bash
# From the repository root
cd IaC-Optimizers-Workflow/ansible_optimizer
python3.13 -m venv .venv-automation
source .venv-automation/bin/activate
pip install -r automation/requirements.txt
```

Make sure `kubconform` and `kube-linter` binaries are installed and on your `PATH`.

## Usage

### Running the Server

```bash
cd IaC-Optimizers-Workflow/ansible_optimizer
source .venv-automation/bin/activate
python automation/validator_mcp_server.py
```

The server operates over stdio and speaks JSON-RPC 2.0. It is compatible with tools like MCP Inspector and Claude Desktop.

### Testing with MCP Inspector

```bash
npx @modelcontextprotocol/inspector python automation/validator_mcp_server.py
```

Use the Inspector UI to invoke `validate_manifest` interactively by pasting manifest YAML or referencing a file path.

### Claude Desktop Configuration

Add the following entry to your Claude Desktop configuration:

```json
{
  "mcpServers": {
    "kubernetes-validator": {
      "command": "/path/to/IaC-Optimizers-Workflow/ansible_optimizer/.venv-automation/bin/python",
      "args": [
        "/path/to/IaC-Optimizers-Workflow/ansible_optimizer/automation/validator_mcp_server.py"
      ],
      "env": {}
    }
  }
}
```

Restart Claude Desktop to pick up the change.

## Tool Schema

### `validate_manifest`

Validates a Kubernetes manifest.

**Parameters**

- `manifest_path` (string, optional): Path to the manifest file.
- `manifest_content` (string, optional): Raw manifest YAML. Required if `manifest_path` is not supplied.
- `name` (string, optional): Friendly label for reports.
- `kubconform_bin` / `kube_linter_bin` (string, optional): Override executable paths.
- `kubconform_args` / `kube_linter_args` (string or list, optional): Additional CLI flags relayed to each tool.

**Returns**

JSON object identical to the `run_validations.py` output, e.g.:

```json
{
  "objective_validations": {
    "file_under_test": "manifests/web.yaml",
    "overall_result": "fail",
    "overall_messages": [
      "kubconform: fail - Deployment/spec/replicas: Invalid value: 0",
      "kube-linter: fail - Missing recommended livenessProbe"
    ],
    "kubconform": {
      "result": "fail",
      "messages": [
        "Deployment/spec/replicas: Invalid value: 0"
      ]
    },
    "kube-linter": {
      "result": "fail",
      "messages": [
        "workload-missing-liveness-probe: add a livenessProbe"
      ]
    }
  }
}
```

## Testing

Automated tests reside in `automation/tests/`. After activating the automation virtual environment, run:

```bash
python -m pytest automation/tests
```

These tests cover server start-up, tool registration, and validation call flows.

## Troubleshooting

- **kubconform / kube-linter not found**: Install the binaries and ensure they are available on `PATH`, or pass explicit `kubconform_bin`/`kube_linter_bin` arguments.
- **Malformed output**: The server returns a structured error with captured stdout/stderr to aid debugging.
- **MCP client cannot connect**: Double-check the Python interpreter path and the working directory in your client configuration.

## References

- [MCP Specification](https://modelcontextprotocol.io/)
- [kubconform](https://github.com/yannh/kubconform)
- [kube-linter](https://github.com/stackrox/kube-linter)
