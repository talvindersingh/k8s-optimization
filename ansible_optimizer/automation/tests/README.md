# Kubernetes Validator MCP Server - Test Suite

This directory contains automated checks for the Kubernetes manifest validator MCP server.

## Quick Start

```bash
cd IaC-Optimizers-Workflow/ansible_optimizer
source .venv-automation/bin/activate
python automation/tests/test_mcp_basic.py
```

## Coverage Highlights

- **Server Initialization** — MCP server launches and responds.
- **Tool Discovery** — `validate_manifest` appears in the tool list.
- **Validation Flow** — Both inline manifest content and file paths are accepted.
- **Schema Guardrails** — Responses match the expected `objective_validations` layout (kubconform + kube-linter blocks).

## Manual Testing

```bash
# Interactive web UI
npx @modelcontextprotocol/inspector python automation/validator_mcp_server.py

# Direct validation
python automation/run_validations.py path/to/manifest.yaml
```

See [../MCP_SERVER.md](../MCP_SERVER.md) for additional details.
