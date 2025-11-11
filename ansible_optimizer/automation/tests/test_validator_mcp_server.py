import asyncio
import json
import os
import sys
import unittest
from datetime import timedelta
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

TESTS_DIR = Path(__file__).resolve().parent
AUTOMATION_ROOT = TESTS_DIR.parent
PACKAGE_ROOT = AUTOMATION_ROOT.parent
DATASET_ROOT = PACKAGE_ROOT / "dataset"
VALIDATOR_SERVER = AUTOMATION_ROOT / "validator_mcp_server.py"


class ValidatorMCPServerIntegrationTest(unittest.TestCase):
    """Live test for the Kubernetes validator MCP server using dataset manifest."""

    def test_validate_manifest_inline(self) -> None:
        asyncio.run(self._run_validation())

    async def _run_validation(self) -> None:
        dataset_path = DATASET_ROOT / "1" / "optimization_flow.json"
        self.assertTrue(dataset_path.exists(), f"Dataset not found: {dataset_path}")
        dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
        manifest_content = dataset.get("original_manifest") or dataset["original_code"]

        kubconform_bin = os.environ.get("KUBECONFORM_BIN", "kubconform")
        kube_linter_bin = os.environ.get("KUBE_LINTER_BIN", "kube-linter")

        server_params = StdioServerParameters(
            command=sys.executable,
            args=[str(VALIDATOR_SERVER)],
            cwd=str(AUTOMATION_ROOT),
            env={**os.environ, "KUBECONFORM_BIN": kubconform_bin, "KUBE_LINTER_BIN": kube_linter_bin},
        )

        async with stdio_client(server_params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()

                tools_result = await session.list_tools()
                tool_names = {tool.name for tool in tools_result.tools}
                self.assertIn("validate_manifest", tool_names)

                call_result = await session.call_tool(
                    "validate_manifest",
                    {
                        "manifest_content": manifest_content,
                    },
                    read_timeout_seconds=timedelta(minutes=5),
                )

                self.assertFalse(call_result.isError, "MCP call returned an error")
                text_chunks = [
                    content.text
                    for content in call_result.content
                    if hasattr(content, "text") and content.text is not None
                ]
                self.assertTrue(text_chunks, "No textual content returned from MCP server")

                payload = json.loads("".join(text_chunks))
                self.assertIn("objective_validations", payload)
                validations = payload["objective_validations"]
                self.assertIn("kubconform", validations)
                self.assertIn("kube-linter", validations)
                self.assertIn("overall_result", validations)


if __name__ == "__main__":
    unittest.main()
