# pyright: reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportUnknownLambdaType=false, reportMissingParameterType=false, reportUnusedCallResult=false, reportUnusedImport=false, reportUnusedVariable=false, reportUnusedParameter=false, reportPrivateUsage=false
"""Regression tests for the registered smart-async MCP job API."""

import asyncio
import json
import os
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from python_mcp_server import mcp
from python_mcp_server.smart_async import (
    JobMeta,
    STATE,
    _save_jobs,
    initialize_state,
)


def _unwrap_tool_result(result: Any) -> Any:
    """Return the structured payload from a FastMCP tool invocation."""
    structured = result.structured_content
    if isinstance(structured, dict) and set(structured) == {"result"}:
        return structured["result"]
    return structured


async def _list_registered_tools() -> dict[str, Any]:
    """Use the public tool-list API across the supported FastMCP generations."""
    if hasattr(mcp, "list_tools"):
        tools = await mcp.list_tools()
        return {tool.name: tool for tool in tools}
    legacy_list = getattr(mcp, "get_tools", None)
    if legacy_list is None:
        raise RuntimeError("FastMCP does not expose a public tool-list API")
    return await legacy_list()


async def _call_tool(name: str, arguments: dict[str, Any]) -> Any:
    """Call a registered MCP tool using the current public FastMCP API."""
    if hasattr(mcp, "call_tool"):
        return await mcp.call_tool(name, arguments)
    tools = await _list_registered_tools()
    return await tools[name].run(arguments)


class JobManagementSurfaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_smart_async_job_tools_are_registered(self) -> None:
        tools = await _list_registered_tools()
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
        if hasattr(mcp, "list_resource_templates"):
            templates = await mcp.list_resource_templates()
            template_uris = {
                getattr(template, "uri_template", None)
                or getattr(template, "uriTemplate", None)
                for template in templates
            }
        else:
            legacy_list = getattr(mcp, "get_resource_templates", None)
            if legacy_list is None:
                raise RuntimeError("FastMCP does not expose resource templates")
            templates = await legacy_list()
            template_uris = set(templates)
        self.assertNotIn("job-stream://{job_id}", template_uris)

    async def _wait_for_completion(self, job_id: str) -> Any:
        deadline = time.monotonic() + 5
        while True:
            result = await _call_tool(
                "py_job_status",
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
            start_result = await _call_tool(
                "py_run_script_in_dir",
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

            status_result = await _call_tool(
                "py_job_status",
                {
                    "job_id": job_id,
                    "incremental": False,
                    "markdown_output": False,
                }
            )
            first_status = _unwrap_tool_result(status_result)
            self.assertNotIn("error", first_status)

            list_result = await _call_tool(
                "py_list_jobs", {"markdown_output": False}
            )
            jobs = _unwrap_tool_result(list_result)
            self.assertIn(job_id, {job["id"] for job in jobs["jobs"]})

            status = await self._wait_for_completion(job_id)

            self.assertEqual(status["job"]["status"], "completed")
            self.assertIn("finished", status["job"]["result"]["stdout"])

    async def test_job_registry_is_atomically_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as state_dir:
            initialize_state(Path(state_dir))
            STATE.jobs.clear()
            STATE.jobs["atomic-registry-test"] = JobMeta(
                id="atomic-registry-test",
                label="registry snapshot test",
                status="running",
            )
            real_replace = os.replace
            observed_snapshot = False

            def validate_then_replace(
                source: str | os.PathLike[str],
                destination: str | os.PathLike[str],
            ) -> None:
                nonlocal observed_snapshot
                source_path = Path(source)
                destination_path = Path(destination)
                self.assertEqual(source_path.parent, destination_path.parent)
                snapshot = json.loads(source_path.read_text(encoding="utf-8"))
                self.assertEqual(snapshot[0]["id"], "atomic-registry-test")
                observed_snapshot = True
                real_replace(source, destination)

            with patch(
                "python_mcp_server.smart_async.os.replace",
                side_effect=validate_then_replace,
            ):
                _save_jobs()

            self.assertTrue(observed_snapshot)
            jobs_path = Path(state_dir) / "meta" / "jobs.json"
            self.assertEqual(
                json.loads(jobs_path.read_text(encoding="utf-8"))[0]["id"],
                "atomic-registry-test",
            )

    async def test_auto_background_completion_persists_pydantic_result(self) -> None:
        with tempfile.TemporaryDirectory() as state_dir:
            initialize_state(Path(state_dir))
            with patch.dict(os.environ, {"SMART_ASYNC_TIMEOUT_SECONDS": "0.01"}):
                start_result = await _call_tool(
                    "py_run_script_in_dir",
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
            status = await self._wait_for_completion(started["job_id"])
            self.assertEqual(status["job"]["status"], "completed")
            self.assertIsInstance(status["job"]["result"], dict)
            self.assertIn("finished", status["job"]["result"]["stdout"])

    async def _wait_for_marker(self, marker: Path) -> tuple[int, int]:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if marker.exists():
                parent_pid, child_pid = marker.read_text(encoding="utf-8").split()
                return int(parent_pid), int(child_pid)
            await asyncio.sleep(0.05)
        self.fail("The test process tree did not create its PID marker")

    async def _assert_process_stopped(self, pid: int) -> None:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                process = psutil.Process(pid)
                if process.status() == psutil.STATUS_ZOMBIE:
                    return
            except psutil.NoSuchProcess:
                return
            await asyncio.sleep(0.05)
        self.fail(f"Process {pid} is still running after cancellation")

    async def test_cancel_job_terminates_and_reaps_script_descendants(self) -> None:
        with tempfile.TemporaryDirectory() as state_dir, tempfile.TemporaryDirectory() as work_dir:
            initialize_state(Path(state_dir))
            marker = Path(work_dir) / "pids.txt"
            start_result = await _call_tool(
                "py_run_script_in_dir",
                {
                    "directory": str(Path(__file__).resolve().parents[1]),
                    "script_path": str(
                        Path(__file__).resolve().parent
                        / "fixtures"
                        / "job_process_tree.py"
                    ),
                    "args": [str(marker)],
                    "use_uv": False,
                    "async_mode": True,
                    "markdown_output": False,
                }
            )
            started = _unwrap_tool_result(start_result)
            job_id = started["job_id"]
            parent_pid: int | None = None
            child_pid: int | None = None
            try:
                parent_pid, child_pid = await self._wait_for_marker(marker)
                cancel_result = await _call_tool(
                    "py_cancel_job",
                    {"job_id": job_id, "markdown_output": False},
                )
                cancelled = _unwrap_tool_result(cancel_result)
                self.assertEqual(cancelled["status"], "cancelled")

                status_result = await _call_tool(
                    "py_job_status",
                    {
                        "job_id": job_id,
                        "incremental": False,
                        "markdown_output": False,
                    }
                )
                status = _unwrap_tool_result(status_result)
                self.assertEqual(status["job"]["status"], "cancelled")
                await self._assert_process_stopped(parent_pid)
                await self._assert_process_stopped(child_pid)
            finally:
                if parent_pid is not None:
                    try:
                        os.killpg(parent_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    async def test_cancelled_sync_call_cleans_up_while_shielded(self) -> None:
        with tempfile.TemporaryDirectory() as state_dir, tempfile.TemporaryDirectory() as work_dir:
            initialize_state(Path(state_dir))
            marker = Path(work_dir) / "pids.txt"
            with patch.dict(os.environ, {"SMART_ASYNC_TIMEOUT_SECONDS": "30"}):
                call = asyncio.create_task(
                    _call_tool(
                        "py_run_script_in_dir",
                        {
                            "directory": str(Path(__file__).resolve().parents[1]),
                            "script_path": str(
                                Path(__file__).resolve().parent
                                / "fixtures"
                                / "job_process_tree.py"
                            ),
                            "args": [str(marker)],
                            "use_uv": False,
                            "markdown_output": False,
                        }
                    )
                )
                parent_pid: int | None = None
                child_pid: int | None = None
                try:
                    parent_pid, child_pid = await self._wait_for_marker(marker)
                    call.cancel()
                    await asyncio.gather(call, return_exceptions=True)
                    await self._assert_process_stopped(parent_pid)
                    await self._assert_process_stopped(child_pid)
                finally:
                    if parent_pid is not None:
                        try:
                            os.killpg(parent_pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass

    async def test_cancelled_benchmark_terminates_script_descendants(self) -> None:
        with tempfile.TemporaryDirectory() as state_dir, tempfile.TemporaryDirectory() as work_dir:
            initialize_state(Path(state_dir))
            marker = Path(work_dir) / "benchmark-pids.txt"
            start_result = await _call_tool(
                "py_benchmark_script",
                {
                    "script_path": str(
                        Path(__file__).resolve().parent
                        / "fixtures"
                        / "job_process_tree.py"
                    ),
                    "python_version": "3.13",
                    "args": [str(marker)],
                    "timeout_seconds": 120,
                    "sample_interval": 0.05,
                    "async_mode": True,
                    "markdown_output": False,
                },
            )
            started = _unwrap_tool_result(start_result)
            job_id = started["job_id"]
            parent_pid: int | None = None
            child_pid: int | None = None
            try:
                parent_pid, child_pid = await self._wait_for_marker(marker)
                cancel_result = await _call_tool(
                    "py_cancel_job",
                    {"job_id": job_id, "markdown_output": False},
                )
                cancelled = _unwrap_tool_result(cancel_result)
                self.assertEqual(cancelled["status"], "cancelled")
                await self._assert_process_stopped(parent_pid)
                await self._assert_process_stopped(child_pid)
            finally:
                if parent_pid is not None:
                    try:
                        os.killpg(parent_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass


if __name__ == "__main__":
    unittest.main()
