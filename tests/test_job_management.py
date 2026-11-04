#!/usr/bin/env python3
# pyright: reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportUnknownLambdaType=false, reportMissingParameterType=false, reportUnusedCallResult=false, reportUnusedImport=false, reportUnusedVariable=false, reportUnusedParameter=false
"""Regression tests for the registered smart-async MCP job API."""

import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from python_mcp_server import mcp
from python_mcp_server.smart_async import initialize_state


def _unwrap_tool_result(result: Any) -> Any:
    """Return the structured payload from a FastMCP tool invocation."""
    structured = result.structured_content
    if isinstance(structured, dict) and set(structured) == {"result"}:
        return structured["result"]
    return structured


class JobManagementSurfaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_smart_async_job_tools_are_registered(self) -> None:
        tools = await mcp.get_tools()
        registered_names = set(tools)

        self.assertTrue(
            {
                "py_job_status",
                "py_list_jobs",
                "py_cancel_job",
                "py_prune_jobs",
            }.issubset(registered_names)
        )
        self.assertTrue(
            {
                "py_get_job_output",
                "py_list_running_jobs",
                "py_kill_job",
                "py_cleanup_jobs",
            }.isdisjoint(registered_names)
        )

    async def test_legacy_job_stream_resource_is_not_registered(self) -> None:
        templates = await mcp.get_resource_templates()
        self.assertNotIn("job-stream://{job_id}", templates)

    async def _wait_for_completion(self, tools: dict[str, Any], job_id: str) -> Any:
        deadline = time.monotonic() + 5
        while True:
            result = await tools["py_job_status"].run(
                {
                    "job_id": job_id,
                    "incremental": False,
                    "markdown_output": False,
                }
            )
            status = _unwrap_tool_result(result)
            self.assertNotIn("error", status)
            self.assertEqual(status["job"]["id"], job_id)
            if status["job"]["status"] not in {"pending", "running"}:
                return status
            if time.monotonic() >= deadline:
                self.fail(
                    f"Background job did not finish within five seconds: {status!r}"
                )
            await asyncio.sleep(0.05)

    async def test_job_status_remains_available_after_listing(self) -> None:
        with (
            tempfile.TemporaryDirectory() as state_dir,
        ):
            initialize_state(Path(state_dir))
            tools = await mcp.get_tools()

            start_result = await tools["py_run_script_in_dir"].run(
                {
                    "directory": str(Path(__file__).resolve().parents[1]),
                    "script_path": str(
                        Path(__file__).resolve().parent
                        / "fixtures"
                        / "job_status_probe.py"
                    ),
                    "use_uv": False,
                    "async_mode": True,
                    "markdown_output": False,
                }
            )
            started = _unwrap_tool_result(start_result)
            job_id = started["job_id"]

            status_result = await tools["py_job_status"].run(
                {
                    "job_id": job_id,
                    "incremental": False,
                    "markdown_output": False,
                }
            )
            first_status = _unwrap_tool_result(status_result)
            self.assertNotIn("error", first_status)

            list_result = await tools["py_list_jobs"].run({"markdown_output": False})
            jobs = _unwrap_tool_result(list_result)
            self.assertIn(job_id, {job["id"] for job in jobs["jobs"]})

            status = await self._wait_for_completion(tools, job_id)

            self.assertEqual(status["job"]["status"], "completed")
            self.assertIn("finished", status["job"]["result"]["stdout"])

    async def test_auto_background_completion_persists_pydantic_result(self) -> None:
        with tempfile.TemporaryDirectory() as state_dir:
            initialize_state(Path(state_dir))
            tools = await mcp.get_tools()

            with patch.dict(os.environ, {"SMART_ASYNC_TIMEOUT_SECONDS": "0.01"}):
                start_result = await tools["py_run_script_in_dir"].run(
                    {
                        "directory": str(Path(__file__).resolve().parents[1]),
                        "script_path": str(
                            Path(__file__).resolve().parent
                            / "fixtures"
                            / "job_status_probe.py"
                        ),
                        "use_uv": False,
                        "markdown_output": False,
                    }
                )

            started = _unwrap_tool_result(start_result)
            self.assertIn("job_id", started)
            status = await self._wait_for_completion(tools, started["job_id"])
            self.assertEqual(status["job"]["status"], "completed")
            self.assertIsInstance(status["job"]["result"], dict)
            self.assertIn("finished", status["job"]["result"]["stdout"])


if __name__ == "__main__":
    unittest.main()
