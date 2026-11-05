# pyright: reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportUnknownLambdaType=false, reportMissingParameterType=false, reportUnusedCallResult=false, reportUnusedParameter=false
"""
Smart async decorator for MCP tools with intelligent timeout handling.

This module provides the @smart_async decorator that enables MCP tools to:
- Attempt synchronous completion within a timeout budget
- Automatically switch to background execution on timeout
- Support explicit async mode for known long-running operations
- Track job progress and status
- Persist job metadata to disk

Based on the production-tested pattern from the mcp-builder skill.
"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import json
import logging
import os
import tempfile
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic_core import to_jsonable_python

from .ids import generate_readable_id
from .markdown import maybe_render_markdown

logger = logging.getLogger(__name__)

# Context variable to track current job_id in async tasks (for progress tracking)
current_job_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_job_id", default=None
)


@dataclass
class JobMeta:
    """Metadata for async background jobs with progress tracking."""

    id: str
    label: str
    status: str = "pending"  # pending, running, completed, failed, cancelled
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    result: Any | None = None
    task: asyncio.Task[Any] | None = None
    monitor_task: asyncio.Task[Any] | None = None
    progress: dict[str, Any] | None = (
        None  # {"current": int, "total": int, "message": str}
    )
    partial_stdout: str = ""  # Incremental stdout for running jobs
    partial_stderr: str = ""  # Incremental stderr for running jobs
    stdout_offset: int = 0  # Track what's been read for incremental access
    stderr_offset: int = 0  # Track what's been read for incremental access


@dataclass
class AppState:
    """Global application state for job tracking."""

    jobs: dict[str, JobMeta] = field(default_factory=dict)
    persistence_dir: Path = field(default_factory=lambda: Path.home() / ".python_mcp")


# Global state instance
STATE = AppState()
RESERVED_JOB_IDS: set[str] = set()


def _reserved_or_registered_job_ids() -> set[str]:
    """Return job IDs that are already registered or reserved for live tasks."""
    return set(STATE.jobs) | RESERVED_JOB_IDS


def _reserve_job_id() -> str:
    """Reserve a readable unique job ID until it is registered or released."""
    job_id = generate_readable_id(_reserved_or_registered_job_ids())
    RESERVED_JOB_IDS.add(job_id)
    return job_id


def _release_reserved_job_id(job_id: str) -> None:
    """Release a previously reserved job ID if it was never registered."""
    RESERVED_JOB_IDS.discard(job_id)


def initialize_state(persistence_dir: Path | None = None) -> None:
    """
    Initialize the global state with optional custom persistence directory.

    Args:
        persistence_dir: Optional custom directory for job persistence
    """
    if persistence_dir:
        STATE.persistence_dir = persistence_dir
    STATE.persistence_dir.mkdir(parents=True, exist_ok=True)
    _load_jobs()


def _save_jobs() -> None:
    """Persist jobs to disk."""
    jobs_path = STATE.persistence_dir / "meta" / "jobs.json"
    jobs_path.parent.mkdir(parents=True, exist_ok=True)

    # Filter out task references (not serializable)
    serializable = [
        {
            "id": j.id,
            "label": j.label,
            "status": j.status,
            "created_at": j.created_at,
            "started_at": j.started_at,
            "completed_at": j.completed_at,
            "error": j.error,
            "result": j.result,
            "progress": j.progress,
            "partial_stdout": j.partial_stdout if j.status == "running" else "",
            "partial_stderr": j.partial_stderr if j.status == "running" else "",
        }
        for j in STATE.jobs.values()
    ]

    # Publish a complete snapshot atomically. Multiple stdio MCP processes can
    # read this registry concurrently; writing directly to jobs.json briefly
    # truncates it and can make a job appear missing until the next refresh.
    fd, temporary_path = tempfile.mkstemp(
        dir=jobs_path.parent,
        prefix=f".{jobs_path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temporary_file:
            json.dump(serializable, temporary_file, indent=2)
        os.replace(temporary_path, jobs_path)
    except BaseException:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise
    logger.debug(f"Saved {len(serializable)} jobs to {jobs_path}")


def _load_jobs() -> None:
    """Load jobs from disk on startup."""
    jobs_path = STATE.persistence_dir / "meta" / "jobs.json"
    if not jobs_path.exists():
        return

    try:
        data = json.loads(jobs_path.read_text())
        updated = False
        for job_data in data:
            # Skip running jobs from previous sessions (mark as stale)
            if job_data["status"] == "running":
                job_data["status"] = "failed"
                job_data["error"] = "Server restarted while job was running"
                job_data["completed_at"] = datetime.now().isoformat()
                updated = True

            STATE.jobs[job_data["id"]] = JobMeta(**job_data)
        logger.info(f"Loaded {len(STATE.jobs)} jobs from {jobs_path}")

        # Save updated statuses back to disk
        if updated:
            _save_jobs()
    except Exception as e:
        logger.warning(f"Failed to load jobs: {e}")


def _is_live_local_job(job: JobMeta | None) -> bool:
    """Return True when a job is still backed by a live in-process asyncio task."""
    return job is not None and job.task is not None and not job.task.done()


def _refresh_jobs_from_disk() -> None:
    """
    Refresh jobs from disk to sync with other processes.

    This ensures that jobs created in subprocesses (e.g., via py_run_script_*)
    are visible in the main MCP server process.

    Always syncs the latest state from disk to ensure cross-process consistency.
    """
    jobs_path = STATE.persistence_dir / "meta" / "jobs.json"
    if not jobs_path.exists():
        return

    try:
        data = json.loads(jobs_path.read_text())
        for job_data in data:
            job_id = job_data["id"]
            existing = STATE.jobs.get(job_id)

            # Preserve in-process jobs that are still running. Replacing them would
            # detach the live asyncio task from STATE.jobs and break progress/status updates.
            if _is_live_local_job(existing):
                continue

            if existing is None:
                STATE.jobs[job_id] = JobMeta(**job_data)
                continue

            existing.label = job_data.get("label", existing.label)
            existing.status = job_data.get("status", existing.status)
            existing.created_at = job_data.get("created_at", existing.created_at)
            existing.started_at = job_data.get("started_at")
            existing.completed_at = job_data.get("completed_at")
            existing.error = job_data.get("error")
            existing.result = job_data.get("result")
            existing.progress = job_data.get("progress")
            existing.partial_stdout = job_data.get("partial_stdout", "")
            existing.partial_stderr = job_data.get("partial_stderr", "")
        logger.debug(f"Refreshed {len(data)} jobs from disk")
    except Exception as e:
        logger.warning(f"Failed to refresh jobs from disk: {e}")


def _update_job_progress(
    job_id: str, current: int, total: int, message: str | None = None
) -> None:
    """
    Update progress for a running job.

    Args:
        job_id: Job identifier
        current: Current progress count
        total: Total items to process
        message: Optional progress message
    """
    job = STATE.jobs.get(job_id)
    if not job:
        return

    job.progress = {"current": current, "total": total}
    if message:
        job.progress["message"] = message

    _save_jobs()


def _job_public(job: JobMeta, include_partial: bool = True) -> dict[str, Any]:
    """
    Convert JobMeta to public dict for API responses.

    Args:
        job: Job metadata
        include_partial: Include partial stdout/stderr for running jobs

    Returns:
        Public job representation
    """
    response: dict[str, Any] = {
        "id": job.id,
        "label": job.label,
        "status": job.status,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "error": job.error,
        "result": job.result,
        "progress": job.progress,
    }

    # Include partial output for running jobs
    if include_partial and job.status == "running":
        response["partial_stdout"] = job.partial_stdout
        response["partial_stderr"] = job.partial_stderr

    return response


def _launch_background_job(
    label: str, coro_factory: Callable[[], Coroutine[Any, Any, Any]]
) -> dict[str, Any]:
    """
    Launch a background job and return job_id immediately.

    Args:
        label: Human-readable job label
        coro_factory: Factory function that creates the coroutine to run

    Returns:
        Job metadata with job_id and status
    """
    job_id = _reserve_job_id()
    job = JobMeta(
        id=job_id,
        label=label,
        status="pending",
        created_at=datetime.now().isoformat(),
    )
    STATE.jobs[job_id] = job
    _release_reserved_job_id(job_id)

    async def _run_job():
        # Set job_id in context for progress tracking
        current_job_id.set(job_id)

        job.status = "running"
        job.started_at = datetime.now().isoformat()
        _save_jobs()

        try:
            result = await coro_factory()
            result = to_jsonable_python(result)
            job.status = "completed"
            job.result = result
            job.completed_at = datetime.now().isoformat()

            # Store final output from result if it's a dict with stdout/stderr
            if isinstance(result, dict):
                if "stdout" in result:
                    job.partial_stdout = result.get("stdout", "")
                if "stderr" in result:
                    job.partial_stderr = result.get("stderr", "")

            # Clear partial output on completion (result has full output)
            job.partial_stdout = ""
            job.partial_stderr = ""
        except Exception as e:
            job.status = "failed"
            job.error = str(e)
            job.completed_at = datetime.now().isoformat()
            logger.exception(f"Job {job_id} ({label}) failed")
        finally:
            _save_jobs()

    job.task = asyncio.create_task(_run_job())
    _save_jobs()
    return {"job_id": job_id, "status": "pending"}


async def _run_with_time_budget(
    label: str, timeout_seconds: float, coro_factory: Callable[[], Coroutine[Any, Any, Any]]
) -> Any:
    """
    Run a coroutine with a time budget. If it exceeds the budget, launch as background job.

    The task is shielded to prevent cancellation when switching to background mode,
    ensuring the work continues even after the timeout.

    Args:
        label: Human-readable label for the operation
        timeout_seconds: Maximum time to wait before switching to background
        coro_factory: Factory function that creates the coroutine to run

    Returns:
        Either the direct result (if completed in time) or job metadata (if switched to background)
    """
    job_id = _reserve_job_id()
    try:
        coro = coro_factory()
    except Exception:
        _release_reserved_job_id(job_id)
        raise
    token = current_job_id.set(job_id)
    try:
        task = asyncio.create_task(coro)
    finally:
        current_job_id.reset(token)
    shielded = asyncio.shield(task)

    try:
        result = await asyncio.wait_for(shielded, timeout=timeout_seconds)
    except asyncio.TimeoutError:
        logger.info(
            f"Task '{label}' exceeded {timeout_seconds}s budget, switching to background"
        )
        # Task continues running; wrap it in a job using the ID already present in its context.
        job = JobMeta(
            id=job_id,
            label=label,
            status="running",
            created_at=datetime.now().isoformat(),
            started_at=datetime.now().isoformat(),
        )
        STATE.jobs[job_id] = job
        _release_reserved_job_id(job_id)
        job.task = task

        async def _finalize():
            try:
                result = await task
                result = to_jsonable_python(result)
                job.status = "completed"
                job.result = result
                job.completed_at = datetime.now().isoformat()

                # Store final output from result if it's a dict with stdout/stderr
                if isinstance(result, dict):
                    if "stdout" in result:
                        job.partial_stdout = result.get("stdout", "")
                    if "stderr" in result:
                        job.partial_stderr = result.get("stderr", "")

                # Clear partial output on completion
                job.partial_stdout = ""
                job.partial_stderr = ""
            except Exception as e:
                job.status = "failed"
                job.error = str(e)
                job.completed_at = datetime.now().isoformat()
                logger.exception(f"Background job {job_id} ({label}) failed")
            finally:
                _save_jobs()

        job.monitor_task = asyncio.create_task(_finalize())
        return {
            "job_id": job_id,
            "status": "running",
            "message": f"Task exceeded {timeout_seconds}s time budget; running in background",
        }
    except asyncio.CancelledError:
        # A caller disconnecting before the timeout budget expires must not
        # leave the shielded work running without a registered job ID.
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        _release_reserved_job_id(job_id)
        raise
    except BaseException:
        _release_reserved_job_id(job_id)
        raise
    else:
        _release_reserved_job_id(job_id)
        return result


def smart_async(
    timeout_env: str = "SMART_ASYNC_TIMEOUT_SECONDS", default_timeout: float = 20.0
):
    """
    Decorator to apply shielded time-threshold smart async to long-running MCP tools.

    Features:
    - If async_mode=True, launch the job in background immediately
    - Otherwise, attempt synchronous completion within the configured time budget
    - On timeout, switch to background without cancelling the underlying task
    - Preserve original function signature for FastMCP compatibility

    Args:
        timeout_env: Environment variable name for timeout configuration
        default_timeout: Default timeout in seconds if env var not set (default: 20.0)

    Returns:
        Decorated function that handles both sync and async execution

    Example:
        @smart_async(timeout_env="MY_TOOL_TIMEOUT", default_timeout=30.0)
        async def my_tool(
            param: str,
            async_mode: bool = False,
            job_label: str | None = None
        ) -> dict[str, Any]:
            # Your tool implementation
            result = await do_work(param)
            return {"result": result}
    """
    import functools

    def _decorator(func):
        signature = inspect.signature(func)

        @functools.wraps(func)
        async def _wrapper(*args, **kwargs):
            bound = signature.bind_partial(*args, **kwargs)
            bound.apply_defaults()

            # Extract control parameters while still honoring function defaults.
            async_mode = bound.arguments.get("async_mode", False)
            job_label = bound.arguments.get("job_label", None)
            markdown_output = bound.arguments.get("markdown_output", False)

            call_kwargs = dict(kwargs)
            call_kwargs.pop("async_mode", None)
            call_kwargs.pop("job_label", None)
            # Avoid double-rendering markdown. Smart-async renders exactly once after
            # the synchronous/background decision, so underlying tools that support
            # markdown_output should return structured data to the wrapper.
            if "markdown_output" in signature.parameters:
                call_kwargs["markdown_output"] = False

            label = job_label or func.__name__
            try:
                timeout_seconds = float(os.getenv(timeout_env, str(default_timeout)))
            except Exception:
                timeout_seconds = default_timeout

            if async_mode:
                result = _launch_background_job(
                    label=label,
                    coro_factory=lambda: func(*args, **call_kwargs),
                )
                return maybe_render_markdown(
                    result, markdown_output=markdown_output, title=label
                )

            result = await _run_with_time_budget(
                label=label,
                timeout_seconds=timeout_seconds,
                coro_factory=lambda: func(*args, **call_kwargs),
            )
            return maybe_render_markdown(
                result, markdown_output=markdown_output, title=label
            )

        return _wrapper

    return _decorator


def get_job_status(job_id: str, incremental: bool = False) -> dict[str, Any]:
    """
    Get status of a background job.

    Args:
        job_id: Job identifier
        incremental: If True, return only new output since last check

    Returns:
        Job status including progress, result, or error
    """
    # Refresh from disk to get jobs from other processes
    _refresh_jobs_from_disk()

    job = STATE.jobs.get(job_id)
    if not job:
        return {"error": f"Job {job_id} not found"}

    response = {"job": _job_public(job, include_partial=True)}

    # Handle incremental output
    if incremental and job.status == "running":
        # Return only new output since last read
        new_stdout = job.partial_stdout[job.stdout_offset :]
        new_stderr = job.partial_stderr[job.stderr_offset :]

        # Update offsets for next incremental read
        job.stdout_offset = len(job.partial_stdout)
        job.stderr_offset = len(job.partial_stderr)

        response["job"]["new_stdout"] = new_stdout
        response["job"]["new_stderr"] = new_stderr
        response = {**response, "incremental": True}

    return response


def list_jobs(status_filter: str | None = None, limit: int = 50) -> dict[str, Any]:
    """
    List all jobs with optional status filtering.

    Args:
        status_filter: Optional status to filter by (pending, running, completed, failed)
        limit: Maximum number of jobs to return

    Returns:
        List of jobs with metadata
    """
    # Refresh from disk to get jobs from other processes
    _refresh_jobs_from_disk()

    jobs = list(STATE.jobs.values())

    if status_filter:
        jobs = [j for j in jobs if j.status == status_filter]

    # Sort by created_at descending (newest first)
    jobs.sort(key=lambda j: j.created_at or "", reverse=True)

    # Apply limit
    jobs = jobs[:limit]

    return {"jobs": [_job_public(j) for j in jobs], "total": len(jobs)}


async def cancel_job(job_id: str) -> dict[str, Any]:
    """
    Cancel a running job.

    Args:
        job_id: Job identifier

    Returns:
        Status of the cancellation
    """
    # Refresh from disk to get jobs from other processes
    _refresh_jobs_from_disk()

    job = STATE.jobs.get(job_id)
    if not job:
        return {"error": f"Job {job_id} not found"}

    if job.status not in ("pending", "running"):
        return {
            "error": f"Job {job_id} is not running (status: {job.status})",
            "job_id": job_id,
        }

    if job.task is None:
        return {
            "error": (
                f"Job {job_id} is not owned by this server process; "
                "it cannot be safely cancelled here"
            ),
            "job_id": job_id,
        }

    if job.task.done():
        if job.monitor_task is not None and not job.monitor_task.done():
            await asyncio.gather(job.monitor_task, return_exceptions=True)
        if job.status not in ("pending", "running"):
            return {
                "error": f"Job {job_id} is not running (status: {job.status})",
                "job_id": job_id,
            }
    else:
        job.task.cancel()
        await asyncio.gather(job.task, return_exceptions=True)
        if job.monitor_task is not None:
            await asyncio.gather(job.monitor_task, return_exceptions=True)

    job.status = "cancelled"
    job.completed_at = datetime.now().isoformat()
    _save_jobs()

    return {"job_id": job_id, "status": "cancelled", "message": "Job cancelled"}


def prune_jobs(
    keep_completed: bool = True,
    keep_failed: bool = True,
    max_age_hours: int = 24,
) -> dict[str, Any]:
    """
    Prune old jobs from the registry.

    Args:
        keep_completed: If False, remove completed jobs
        keep_failed: If False, remove failed jobs
        max_age_hours: Remove jobs older than this many hours

    Returns:
        Number of jobs removed
    """
    # Refresh from disk to get jobs from other processes
    _refresh_jobs_from_disk()

    from datetime import timedelta

    now = datetime.now()
    cutoff = now - timedelta(hours=max_age_hours)
    removed = 0

    jobs_to_remove = []
    for job_id, job in STATE.jobs.items():
        # Parse created_at
        try:
            created_at = datetime.fromisoformat(job.created_at)
        except Exception:
            continue

        # Check age
        if created_at < cutoff:
            # Check status filters
            if job.status == "completed" and not keep_completed:
                jobs_to_remove.append(job_id)
            elif job.status == "failed" and not keep_failed:
                jobs_to_remove.append(job_id)

    for job_id in jobs_to_remove:
        del STATE.jobs[job_id]
        removed += 1

    if removed > 0:
        _save_jobs()

    return {"removed": removed, "remaining": len(STATE.jobs)}


def create_progress_callback() -> Callable[[int, int, str | None], None]:
    """
    Create a progress callback that uses the current job context.

    This is a convenience function for tools that want to report progress.
    The callback will automatically update the job progress based on the
    current job_id from the context.

    Returns:
        Progress callback function

    Example:
        @smart_async()
        async def my_tool(...) -> dict:
            progress_callback = create_progress_callback()

            for i, item in enumerate(items):
                await process_item(item)
                progress_callback(i + 1, len(items), f"Processed {i + 1} items")

            return {"result": "done"}
    """

    def progress_callback(current: int, total: int, message: str | None = None):
        job_id = current_job_id.get()
        if job_id:
            _update_job_progress(job_id, current, total, message)

    return progress_callback


def update_job_output(job_id: str, stdout: str = "", stderr: str = "") -> None:
    """
    Update partial output for a running job.

    This allows tools to stream output incrementally while running.
    Output is appended to existing partial output.

    Args:
        job_id: Job identifier
        stdout: Stdout to append
        stderr: Stderr to append
    """
    job = STATE.jobs.get(job_id)
    if not job or job.status != "running":
        return

    if stdout:
        job.partial_stdout += stdout
    if stderr:
        job.partial_stderr += stderr

    _save_jobs()


def create_output_callback() -> Callable[[str, str], None]:
    """
    Create an output callback that uses the current job context.

    This allows tools to stream output incrementally while running.

    Returns:
        Output callback function

    Example:
        @smart_async()
        async def my_tool(...) -> dict:
            output_callback = create_output_callback()

            for line in process_lines():
                output_callback(stdout=line, stderr="")

            return {"result": "done"}
    """

    def output_callback(stdout: str = "", stderr: str = ""):
        job_id = current_job_id.get()
        if job_id:
            update_job_output(job_id, stdout, stderr)

    return output_callback
