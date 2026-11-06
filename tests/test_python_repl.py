# pyright: reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportUnknownLambdaType=false, reportMissingParameterType=false, reportUnusedCallResult=false, reportUnusedFunction=false, reportUnusedParameter=false, reportDeprecated=false, reportOptionalMemberAccess=false, reportUnnecessaryComparison=false, reportImplicitOverride=false, reportUnannotatedClassAttribute=false
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
SERVER = SCRIPTS / "python_repl_mcp.py"
WORKER = SCRIPTS / "python_repl_worker.py"
sys.path.insert(0, str(REPO))
from scripts import python_repl_mcp


class WorkerProtocolTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.process: subprocess.Popen[bytes] | None = None
        self.ready: dict[str, Any] = {}
        self.next_id = 0

    def setUp(self) -> None:
        self.process = subprocess.Popen(
            [sys.executable, str(WORKER), "--max-output-chars", "2000"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert self.process.stdout is not None
        self.ready = json.loads(self.process.stdout.readline())
        self.assertEqual(self.ready["type"], "ready")
        self.next_id = 1

    def tearDown(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                _ = self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                _ = self.process.wait(timeout=2)
        if self.process is not None:
            for stream in (
                self.process.stdin,
                self.process.stdout,
                self.process.stderr,
            ):
                if stream is not None:
                    stream.close()

    def request(self, method: str, **kwargs: Any) -> dict[str, Any]:
        assert self.process is not None
        assert self.process.stdin is not None and self.process.stdout is not None
        request_id = self.next_id
        self.next_id += 1
        self.process.stdin.write(
            (json.dumps({"id": request_id, "method": method, **kwargs}) + "\n").encode()
        )
        self.process.stdin.flush()
        response = json.loads(self.process.stdout.readline())
        self.assertEqual(response.get("id"), request_id)
        return response

    def test_cells_share_state_capture_output_and_return_final_expression(self) -> None:
        first = self.request("execute", code="number = 40\nprint('ready')\nnumber + 2")
        second = self.request("execute", code="number += 1\nnumber")
        self.assertTrue(first["ok"])
        self.assertEqual(first["stdout"], "ready\n")
        self.assertEqual(first["result"], "42")
        self.assertEqual(second["result"], "41")

    def test_top_level_await_and_exceptions_keep_worker_alive(self) -> None:
        awaited = self.request(
            "execute",
            code="import asyncio\nawait asyncio.sleep(0)\n'async-ok'",
        )
        failed = self.request("execute", code="raise ValueError('cell failed')")
        recovered = self.request("execute", code="6 * 7")
        self.assertEqual(awaited["result"], "'async-ok'")
        self.assertFalse(failed["ok"])
        self.assertIn("ValueError: cell failed", failed["error"])
        self.assertTrue(recovered["ok"])
        self.assertEqual(recovered["result"], "42")

    def test_input_cannot_consume_worker_protocol_and_unicode_survives(self) -> None:
        interrupted = self.request("execute", code="input()")
        unicode_output = self.request("execute", code="print('π')\n'✓'")
        recovered = self.request("execute", code="40 + 2")
        self.assertFalse(interrupted["ok"])
        self.assertIn("EOFError", interrupted["error"])
        self.assertEqual(unicode_output["stdout"], "π\n")
        self.assertEqual(unicode_output["result"], "'✓'")
        self.assertEqual(recovered["result"], "42")

    def test_output_is_bounded(self) -> None:
        result = self.request("execute", code="print('x' * 5000)")
        self.assertTrue(result["output_truncated"])
        self.assertLessEqual(len(result["stdout"]), 2_100)


class UvCommandTests(unittest.TestCase):
    def test_startup_dependencies_are_separate_uv_arguments(self) -> None:
        uv = shutil.which("uv")
        self.assertIsNotNone(uv)
        settings = python_repl_mcp.parse_settings(
            [
                "--uv",
                str(uv),
                "--dependency",
                "numpy>=2",
                "--dependency",
                "pandas==2.3.*",
            ]
        )
        command = python_repl_mcp.build_worker_command(settings)
        self.assertEqual(command[0], str(Path(str(uv)).resolve()))
        with_indexes = [i for i, item in enumerate(command) if item == "--with"]
        self.assertEqual(len(with_indexes), 2)
        self.assertEqual(
            [command[i + 1] for i in with_indexes], ["numpy>=2", "pandas==2.3.*"]
        )
        self.assertIn("--no-project", command)
        self.assertIn("--", command)

    def test_project_venv_selects_its_interpreter_without_modifying_it(self) -> None:
        with __import__("tempfile").TemporaryDirectory() as temp_dir:
            venv = Path(temp_dir)
            _ = (venv / "pyvenv.cfg").write_text("home = /usr\n")
            python = venv / (
                "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
            )
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("placeholder")
            if os.name == "posix":
                python.unlink()
                python.symlink_to(sys.executable)
            settings = python_repl_mcp.parse_settings(
                ["--uv", sys.executable, "--project-venv", str(venv)]
            )
            self.assertEqual(settings.base_python, str(python))
            command = python_repl_mcp.build_worker_command(settings)
            self.assertEqual(command[command.index("--python") + 1], str(python))
            self.assertEqual((venv / "pyvenv.cfg").read_text(), "home = /usr\n")


class StdioMcpIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_tools_persist_reset_and_isolate_two_server_processes(
        self,
    ) -> None:
        uv = shutil.which("uv")
        self.assertIsNotNone(uv)
        project_venv = str(REPO / ".venv-daemon")
        args = [
            str(SERVER),
            "--uv",
            str(uv),
            "--project-venv",
            project_venv,
            "--default-timeout-seconds",
            "3",
        ]
        client_a = Client(
            StdioTransport(command=sys.executable, args=args, keep_alive=False)
        )
        client_b = Client(
            StdioTransport(command=sys.executable, args=args, keep_alive=False)
        )
        worker_pid: int | None = None
        async with client_a, client_b:
            tools = await client_a.list_tools()
            self.assertEqual(
                {tool.name for tool in tools},
                {"python_repl_execute", "python_repl_reset", "python_repl_info"},
            )
            first = await client_a.call_tool(
                "python_repl_execute", {"code": "private_value = 40\nprivate_value + 2"}
            )
            second = await client_a.call_tool(
                "python_repl_execute", {"code": "private_value += 1\nprivate_value"}
            )
            isolated = await client_b.call_tool(
                "python_repl_execute", {"code": "private_value"}
            )
            self.assertEqual(first.data["result"], "42")
            self.assertEqual(second.data["result"], "41")
            self.assertFalse(isolated.data["ok"])
            timeout = await client_a.call_tool(
                "python_repl_execute",
                {"code": "import time\ntime.sleep(2)", "timeout_seconds": 0.1},
            )
            self.assertFalse(timeout.data["ok"])
            self.assertIn("worker was terminated", timeout.data["error"])
            recovered = await client_a.call_tool(
                "python_repl_execute", {"code": "6 * 7"}
            )
            self.assertTrue(recovered.data["ok"])
            self.assertEqual(recovered.data["result"], "42")
            reset = await client_a.call_tool("python_repl_reset", {})
            self.assertTrue(reset.data["ok"])
            cleared = await client_a.call_tool(
                "python_repl_execute", {"code": "private_value"}
            )
            self.assertFalse(cleared.data["ok"])
            info = await client_a.call_tool("python_repl_info", {})
            worker_pid = info.data["worker_pid"]
            self.assertIsInstance(worker_pid, int)
        assert worker_pid is not None
        with self.assertRaises(ProcessLookupError):
            os.kill(worker_pid, 0)


if __name__ == "__main__":
    _ = unittest.main()
