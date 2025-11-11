import asyncio
import json
import os
import stat
import sys
import tempfile
import textwrap
import unittest
from datetime import timedelta
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


TESTS_DIR = Path(__file__).resolve().parent
AUTOMATION_ROOT = TESTS_DIR.parent
VALIDATOR_SERVER = AUTOMATION_ROOT / "validator_mcp_server.py"


class ValidatorMCPServerStubbedIntegrationTest(unittest.TestCase):
    """Exercise validator_mcp_server.py with a stubbed run_validations implementation."""

    def test_validate_manifest_with_stubbed_runner(self) -> None:
        asyncio.run(self._run_validation_with_stub())

    async def _run_validation_with_stub(self) -> None:
        manifest_content = "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: stub\n"

        stub_path = self._create_stub_runner()
        server_env = os.environ.copy()
        repo_root = AUTOMATION_ROOT.parent.parent
        existing_path = server_env.get("PYTHONPATH", "")
        server_env["PYTHONPATH"] = f"{repo_root}{os.pathsep}{existing_path}" if existing_path else str(repo_root)

        wrapper_source = textwrap.dedent(
            f"""\
            import asyncio
            import pathlib
            import sys

            sys.path.insert(0, {repr(str(AUTOMATION_ROOT))})
            sys.path.insert(0, {repr(str(AUTOMATION_ROOT.parent.parent))})
            import ansible_optimizer.automation.validator_mcp_server as server

            server.RUN_VALIDATIONS = pathlib.Path({stub_path!r})

            if __name__ == "__main__":
                asyncio.run(server.main())
            """
        )

        wrapper_fd, wrapper_path = tempfile.mkstemp(suffix=".py", text=True)
        with os.fdopen(wrapper_fd, "w", encoding="utf-8") as wrapper_file:
            wrapper_file.write(wrapper_source)
        os.chmod(wrapper_path, os.stat(wrapper_path).st_mode | stat.S_IEXEC)

        server_params = StdioServerParameters(
            command=sys.executable,
            args=[wrapper_path],
            cwd=str(AUTOMATION_ROOT),
            env=server_env,
        )

        try:
            async with stdio_client(server_params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()

                    call_result = await session.call_tool(
                        "validate_manifest",
                        {
                            "manifest_content": manifest_content,
                        },
                        read_timeout_seconds=timedelta(seconds=10),
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
                    self.assertEqual(validations.get("overall_result"), "pass")
                    self.assertIn("kubconform", validations)
                    self.assertIn("kube-linter", validations)
        finally:
            Path(wrapper_path).unlink(missing_ok=True)
            Path(stub_path).unlink(missing_ok=True)

    @staticmethod
    def _create_stub_runner() -> str:
        stub_fd, stub_path = tempfile.mkstemp(suffix=".py", text=True)
        with os.fdopen(stub_fd, "w", encoding="utf-8") as stub_file:
            stub_file.write(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import json
                    import sys

                    if len(sys.argv) < 2:
                        sys.exit("usage: stub_run_validations <manifest>")

                    manifest = sys.argv[1]
                    print("Validation Summary")
                    print("==================")
                    print(f"{manifest} - stub")
                    print("  kubconform: success - stub")
                    print("  kube-linter: success - stub")
                    print()
                    print(
                        json.dumps(
                            {
                                "objective_validations": {
                                    "file_under_test": manifest,
                                    "overall_result": "pass",
                                    "overall_messages": [],
                                    "kubconform": {"result": "pass", "messages": []},
                                    "kube-linter": {"result": "pass", "messages": []},
                                }
                            },
                            indent=2,
                        )
                    )
                    """
                )
            )
        abs_path = os.path.abspath(stub_path)
        os.chmod(abs_path, os.stat(abs_path).st_mode | stat.S_IEXEC)
        return abs_path


if __name__ == "__main__":
    unittest.main()
