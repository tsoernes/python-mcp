# pyright: reportAny=false, reportExplicitAny=false
"""Helpers for managing subprocess lifetimes owned by async MCP jobs."""

from __future__ import annotations

import asyncio
import os
import signal
import time
from contextlib import suppress
from typing import Any


def subprocess_session_kwargs() -> dict[str, Any]:
    """Start each command in its own POSIX session/process group."""
    if os.name == "posix":
        return {"start_new_session": True}
    return {}


def _process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _signal_process_tree(
    process: asyncio.subprocess.Process, sig: signal.Signals
) -> None:
    if os.name == "posix":
        with suppress(ProcessLookupError):
            os.killpg(process.pid, sig)
    elif process.returncode is None:
        if sig == signal.SIGKILL:
            process.kill()
        else:
            process.terminate()


async def terminate_subprocess_tree(
    process: asyncio.subprocess.Process, *, grace_seconds: float = 0.5
) -> None:
    """Terminate and reap a command and its descendants, if any remain."""
    if os.name != "posix":
        if process.returncode is None:
            process.terminate()
            try:
                _ = await asyncio.wait_for(process.wait(), timeout=grace_seconds)
            except TimeoutError:
                process.kill()
        _ = await process.wait()
        return

    process_group_id = process.pid
    _signal_process_tree(process, signal.SIGTERM)
    deadline = time.monotonic() + max(grace_seconds, 0.0)
    while _process_group_exists(process_group_id) and time.monotonic() < deadline:
        await asyncio.sleep(min(0.05, max(deadline - time.monotonic(), 0.0)))

    # Signal the entire group after the grace period even if its direct child
    # exited first: descendants can otherwise outlive the MCP job and keep pipe
    # readers blocked indefinitely.
    if _process_group_exists(process_group_id):
        _signal_process_tree(process, signal.SIGKILL)
    _ = await process.wait()


async def communicate_with_timeout(
    process: asyncio.subprocess.Process, timeout_seconds: float | None
) -> tuple[bytes, bytes]:
    """Collect output while ensuring timeout/cancellation also reaps children."""
    communication = asyncio.create_task(process.communicate())
    try:
        if timeout_seconds is None or timeout_seconds <= 0:
            return await asyncio.shield(communication)
        return await asyncio.wait_for(
            asyncio.shield(communication), timeout=timeout_seconds
        )
    except TimeoutError:
        await terminate_subprocess_tree(process)
        _ = await asyncio.gather(communication, return_exceptions=True)
        raise
    except asyncio.CancelledError:
        await terminate_subprocess_tree(process)
        _ = await asyncio.gather(communication, return_exceptions=True)
        raise
