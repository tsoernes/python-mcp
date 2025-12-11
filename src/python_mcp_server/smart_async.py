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
import json
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

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
    task: asyncio.Task | None = None
    progress: dict[str, Any] | None = (
        None  # {"current": int, "total": int, "message": str}
    )


@dataclass
class AppState:
    """Global application state for job tracking."""

    jobs: dict[str, JobMeta] = field(default_factory=dict)
    persistence_dir: Path = field(default_factory=lambda: Path.home() / ".python_mcp")


# Global state instance
STATE = AppState()


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
        }
        for j in STATE.jobs.values()
    ]

    jobs_path.write_text(json.dumps(serializable, indent=2))
    logger.debug(f"Saved {len(serializable)} jobs to {jobs_path}")


def _load_jobs() -> None:
    """Load jobs from disk on startup."""
    jobs_path = STATE.persistence_dir / "meta" / "jobs.json"
    if not jobs_path.exists():
        return

    try:
        data = json.loads(jobs_path.read_text())
        for job_data in data:
            # Skip running jobs from previous sessions (mark as stale)
            if job_data["status"] == "running":
                job_data["status"] = "failed"
                job_data["error"] = "Server restarted while job was running"
                job_data["completed_at"] = datetime.now().isoformat()

            STATE.jobs[job_data["id"]] = JobMeta(**job_data)
        logger.info(f"Loaded {len(STATE.jobs)} jobs from {jobs_path}")
    except Exception as e:
        logger.warning(f"Failed to load jobs: {e}")


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


def _job_public(job: JobMeta) -> dict[str, Any]:
    """
    Convert JobMeta to public dict for API responses.

    Args:
        job: Job metadata

    Returns:
        Public job representation
    """
    return {
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


def _launch_background_job(
    label: str, coro_factory: Callable[[], Awaitable[Any]]
) -> dict[str, Any]:
    """
    Launch a background job and return job_id immediately.

    Args:
        label: Human-readable job label
        coro_factory: Factory function that creates the coroutine to run

    Returns:
        Job metadata with job_id and status
    """
    job_id = str(uuid.uuid4())
    job = JobMeta(
        id=job_id,
        label=label,
        status="pending",
        created_at=datetime.now().isoformat(),
    )
    STATE.jobs[job_id] = job

    async def _run_job():
        # Set job_id in context for progress tracking
        current_job_id.set(job_id)

        job.status = "running"
        job.started_at = datetime.now().isoformat()
        _save_jobs()

        try:
            result = await coro_factory()
            job.status = "completed"
            job.result = result
            job.completed_at = datetime.now().isoformat()
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
    label: str, timeout_seconds: float, coro_factory: Callable[[], Awaitable[Any]]
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
    coro = coro_factory()
    task = asyncio.create_task(coro)
    shielded = asyncio.shield(task)

    try:
        return await asyncio.wait_for(shielded, timeout=timeout_seconds)
    except asyncio.TimeoutError:
        logger.info(
            f"Task '{label}' exceeded {timeout_seconds}s budget, switching to background"
        )
        # Task continues running; wrap it in a job
        job_id = str(uuid.uuid4())
        job = JobMeta(
            id=job_id,
            label=label,
            status="running",
            created_at=datetime.now().isoformat(),
            started_at=datetime.now().isoformat(),
        )
        STATE.jobs[job_id] = job
        job.task = task

        async def _finalize():
            try:
                result = await task
                job.status = "completed"
                job.result = result
                job.completed_at = datetime.now().isoformat()
            except Exception as e:
                job.status = "failed"
                job.error = str(e)
                job.completed_at = datetime.now().isoformat()
                logger.exception(f"Background job {job_id} ({label}) failed")
            finally:
                _save_jobs()

        asyncio.create_task(_finalize())
        return {
            "job_id": job_id,
            "status": "running",
            "message": f"Task exceeded {timeout_seconds}s time budget; running in background",
        }


def smart_async(
    timeout_env: str = "SMART_ASYNC_TIMEOUT_SECONDS", default_timeout: float = 50.0
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
        default_timeout: Default timeout in seconds if env var not set

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
        @functools.wraps(func)
        async def _wrapper(*args, **kwargs):
            # Extract control parameters
            async_mode = kwargs.pop("async_mode", False)
            job_label = kwargs.pop("job_label", None)

            label = job_label or func.__name__
            try:
                timeout_seconds = float(os.getenv(timeout_env, str(default_timeout)))
            except Exception:
                timeout_seconds = default_timeout

            if async_mode:
                return _launch_background_job(
                    label=label,
                    coro_factory=lambda: func(*args, **kwargs),
                )
            return await _run_with_time_budget(
                label=label,
                timeout_seconds=timeout_seconds,
                coro_factory=lambda: func(*args, **kwargs),
            )

        return _wrapper

    return _decorator


def get_job_status(job_id: str) -> dict[str, Any]:
    """
    Get status of a background job.

    Args:
        job_id: Job identifier

    Returns:
        Job status including progress, result, or error
    """
    job = STATE.jobs.get(job_id)
    if not job:
        return {"error": f"Job {job_id} not found"}

    return {"job": _job_public(job)}


def list_jobs(status_filter: str | None = None, limit: int = 50) -> dict[str, Any]:
    """
    List all jobs with optional status filtering.

    Args:
        status_filter: Optional status to filter by (pending, running, completed, failed)
        limit: Maximum number of jobs to return

    Returns:
        List of jobs with metadata
    """
    jobs = list(STATE.jobs.values())

    if status_filter:
        jobs = [j for j in jobs if j.status == status_filter]

    # Sort by created_at descending (newest first)
    jobs.sort(key=lambda j: j.created_at or "", reverse=True)

    # Apply limit
    jobs = jobs[:limit]

    return {"jobs": [_job_public(j) for j in jobs], "total": len(jobs)}


def cancel_job(job_id: str) -> dict[str, Any]:
    """
    Cancel a running job.

    Args:
        job_id: Job identifier

    Returns:
        Status of the cancellation
    """
    job = STATE.jobs.get(job_id)
    if not job:
        return {"error": f"Job {job_id} not found"}

    if job.status not in ("pending", "running"):
        return {
            "error": f"Job {job_id} is not running (status: {job.status})",
            "job_id": job_id,
        }

    if job.task and not job.task.done():
        job.task.cancel()

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
