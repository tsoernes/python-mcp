from __future__ import annotations

import asyncio
import subprocess
import sys
import textwrap
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import psutil
from fastmcp import FastMCP

mcp = FastMCP(name="Python Script Executor")


# Simple in-memory job registry
@dataclass
class JobRecord:
    job_id: str
    command: list[str]
    start_time: float
    process: subprocess.Popen
    directory: Path
    stdout_chunks: list[str] = field(default_factory=list)
    stderr_chunks: list[str] = field(default_factory=list)
    finished: bool = False
    exit_code: Optional[int] = None
    benchmark: dict[str, float] | None = None
    script_path: Optional[Path] = None
    stream: bool = False


JOBS: dict[str, JobRecord] = {}
STREAM_POLL_INTERVAL = 0.2  # seconds


@mcp.tool
def run_script_in_dir(
    directory: Path,
    script_path: Path | None = None,
    script_content: str | None = None,
    args: list[str] | None = None,
    use_uv: bool = True,
    python_version: str | None = None,
    async_run: bool = False,
    stream: bool = False,
    timeout_seconds: int = 300,
) -> dict[str, str]:
    """
    Execute a Python script located in (or created within) a given directory.

    Parameters:
        directory: Directory path where the script resides (or will be written).
        script_path: Path to an existing script (absolute or relative to 'directory') (ignored if script_content provided).
        script_content: Optional inline Python source; if provided a temp file is created.
        args: Optional list of arguments passed to the script.
        use_uv: Prefer invoking via `uv run` if True; else fall back to system python.
        python_version: Exact minor version (e.g. '3.12'). If provided and using uv, enforce interpreter.
        async_run: When True return immediately with job_id.
        stream: When True (and async_run=True) enable incremental output collection (polling).
        timeout_seconds: Maximum runtime; 0 means no enforced timeout (run arbitrarily long).
    Returns (sync):
        {
          stdout, stderr, exit_code, execution_strategy, elapsed_seconds
        }
    Returns (async):
        {
          job_id, status, execution_strategy
        }

    Environment Resolution Strategy:
        - If use_uv True: base command starts with ['uv', 'run']
          - Append '--python', python_version if provided.
        - Else: ['python']
        - Append script path then args.

    Timeout Handling:
        - If timeout_seconds > 0 and process exceeds limit, it is terminated and marked timed_out.

    Streaming:
        - When stream=True and async_run=True, stdout/stderr chunks are accumulated and retrievable via list_running_jobs
          (future improvement: dedicated get_job_output tool).

    Notes:
        - No security constraints (per user instruction). Full filesystem & network access.
        - Exact minor Python version required if specified (no range parsing).
    """
    workdir = Path(directory).expanduser().resolve()
    if not workdir.is_dir():
        raise FileNotFoundError(f"Directory not found: {workdir}")

    if script_content:
        # Create a temp script file
        tmp_name = f"inline_{uuid.uuid4().hex}.py"
        script_path_local = workdir / tmp_name
        script_path_local.write_text(script_content)
    else:
        if not script_path:
            raise ValueError(
                "Either 'script_path' or 'script_content' must be provided."
            )
        # Allow relative paths (to the provided directory) as well as absolute.
        candidate = script_path.expanduser()
        if not candidate.is_absolute():
            candidate = (workdir / candidate).resolve()
        script_path_local = candidate.resolve()
        if not script_path_local.is_file():
            raise FileNotFoundError(f"Script file not found: {script_path_local}")

    command: list[str]
    execution_strategy = "system-python"
    if use_uv:
        command = ["uv", "run"]
        if python_version:
            command += ["--python", python_version]
        execution_strategy = "uv-run"
    else:
        command = ["python"]

    command.append(str(script_path_local))
    if args:
        command.extend(args)

    if async_run:
        proc = subprocess.Popen(
            command,
            cwd=str(workdir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        job_id = uuid.uuid4().hex
        rec = JobRecord(
            job_id=job_id,
            command=command,
            start_time=time.time(),
            process=proc,
            directory=workdir,
            script_path=script_path_local,
            stream=stream,
        )
        JOBS[job_id] = rec

        if stream:
            # Launch background poller
            asyncio.get_event_loop().create_task(_poll_stream(job_id))

        return {
            "job_id": job_id,
            "status": "started",
            "execution_strategy": execution_strategy,
        }

    # Synchronous execution
    start = time.time()
    try:
        proc = subprocess.Popen(
            command,
            cwd=str(workdir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            stdout, stderr = proc.communicate(
                timeout=None if timeout_seconds == 0 else timeout_seconds
            )
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            return {
                "stdout": stdout,
                "stderr": stderr + "\n[TIMEOUT]",
                "exit_code": str(proc.returncode),
                "execution_strategy": execution_strategy,
                "elapsed_seconds": time.time() - start,
            }
    finally:
        if script_content:
            # Clean up temp script file for sync mode
            if script_path_local.exists():
                try:
                    script_path_local.unlink()
                except Exception:
                    pass

    return {
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": str(proc.returncode),
        "execution_strategy": execution_strategy,
        "elapsed_seconds": time.time() - start,
    }


@mcp.tool
def run_script_with_dependencies(
    script_content: str | None = None,
    script_path: Path | None = None,
    python_version: str = "3.12",
    dependencies: list[str] | None = None,
    args: list[str] | None = None,
    async_run: bool = False,
    stream: bool = False,
    timeout_seconds: int = 300,
) -> dict[str, str]:
    """
    Execute code or a script path inside a transient uv-managed environment with dependency resolution.

    Parameters:
        script_content: Inline Python source (ignored if script_path provided).
        script_path: Path to an existing script file.
        python_version: Exact minor version (e.g. '3.12').
        dependencies: List of package specifiers.
        args: Optional CLI arguments.
        async_run: Return immediately with job_id.
        stream: Enable streaming polling (async only).
        timeout_seconds: Max runtime; 0 disables timeout.

    Returns (sync):
        {
          stdout, stderr, exit_code, resolved_dependencies, python_version_used, elapsed_seconds
        }
    Returns (async):
        {
          job_id, status, python_version_used, resolved_dependencies
        }

    Implementation Notes:
        - Uses `uv run` with optional `--python` and repeated '--with' specifiers for dependencies.
        - Writes inline code to a temp file when 'code' provided.
        - Future improvement: caching environments by dependency hash.
    """
    if not script_content and not script_path:
        raise ValueError("Provide either 'script_content' or 'script_path'.")
    if script_content and script_path:
        raise ValueError("Provide only one of 'script_content' or 'script_path'.")

    if script_path:
        spath = Path(script_path).expanduser().resolve()
        if not spath.is_file():
            raise FileNotFoundError(f"Script not found: {spath}")
    else:
        # Inline code -> temp file in cwd of process (use current working directory)
        spath = Path(f"inline_dep_{uuid.uuid4().hex}.py").resolve()
        spath.write_text(script_content or "")

    command: list[str] = ["uv", "run", "--python", python_version]
    resolved_dependencies = dependencies[:] if dependencies else []
    for dep in resolved_dependencies:
        command += ["--with", dep]
    command.append(str(spath))
    if args:
        command.extend(args)

    if async_run:
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        job_id = uuid.uuid4().hex
        rec = JobRecord(
            job_id=job_id,
            command=command,
            start_time=time.time(),
            process=proc,
            directory=Path(".").resolve(),
            script_path=spath,
            stream=stream,
        )
        JOBS[job_id] = rec
        if stream:
            asyncio.get_event_loop().create_task(_poll_stream(job_id))
        return {
            "job_id": job_id,
            "status": "started",
            "python_version_used": python_version,
            "resolved_dependencies": resolved_dependencies,
        }

    start = time.time()
    try:
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            stdout, stderr = proc.communicate(
                timeout=None if timeout_seconds == 0 else timeout_seconds
            )
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            stderr += "\n[TIMEOUT]"
    finally:
        if script_content and spath.exists():
            try:
                spath.unlink()
            except Exception:
                pass

    return {
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": str(proc.returncode),
        "resolved_dependencies": resolved_dependencies,
        "python_version_used": python_version,
        "elapsed_seconds": time.time() - start,
    }


@mcp.tool
def list_running_jobs() -> list[dict[str, str]]:
    """
    List currently registered jobs (running or finished but not yet cleaned).

    Returns:
        List of dicts containing: job_id, running (bool str), exit_code (may be None), pid, elapsed_seconds.
        If streaming is enabled, includes partial stdout/stderr lengths.
    """
    now = time.time()
    out: list[dict[str, str]] = []
    for jid, rec in JOBS.items():
        running = not rec.finished and rec.process.poll() is None
        elapsed = now - rec.start_time
        exit_code = (
            str(rec.process.returncode) if rec.process.poll() is not None else ("None")
        )
        if running and rec.stream:
            # Update chunks before reporting (non-blocking read)
            _nonblocking_capture(rec)
        out.append(
            {
                "job_id": jid,
                "running": str(running),
                "exit_code": exit_code,
                "pid": str(rec.process.pid),
                "elapsed_seconds": f"{elapsed:.2f}",
                "stream": str(rec.stream),
                "stdout_chunks": str(len(rec.stdout_chunks)),
                "stderr_chunks": str(len(rec.stderr_chunks)),
            }
        )
    return out


@mcp.tool
def get_job_output(job_id: str) -> dict[str, str]:
    """
    Retrieve (and finalize if completed) a job's output.

    Parameters:
        job_id: Identifier returned from async execution.

    Returns:
        {
          status: running|finished,
          stdout: concatenated (may be partial if running),
          stderr: concatenated,
          exit_code: int or None,
          elapsed_seconds: float
        }
    """
    rec = JOBS.get(job_id)
    if not rec:
        raise ValueError(f"No such job: {job_id}")
    if rec.stream:
        _nonblocking_capture(rec)

    running = rec.process.poll() is None
    if not running and not rec.finished:
        # Final capture
        _finalize_capture(rec)

    return {
        "status": "running" if running else "finished",
        "stdout": "".join(rec.stdout_chunks),
        "stderr": "".join(rec.stderr_chunks),
        "exit_code": str(rec.process.returncode)
        if rec.process.returncode is not None
        else "None",
        "elapsed_seconds": f"{time.time() - rec.start_time:.2f}",
    }


@mcp.tool
def kill_job(job_id: str) -> dict[str, str]:
    """
    Terminate a running job.

    Parameters:
        job_id: Job identifier.
    """
    rec = JOBS.get(job_id)
    if not rec:
        raise ValueError(f"No such job: {job_id}")
    if rec.process.poll() is None:
        rec.process.kill()
        time.sleep(0.05)
    _finalize_capture(rec)
    return {
        "job_id": job_id,
        "status": "killed",
        "exit_code": str(rec.process.returncode),
    }


@mcp.tool
def benchmark_script(
    script_content: str | None = None,
    script_path: Path | None = None,
    python_version: str = "3.12",
    dependencies: list[str] | None = None,
    args: list[str] | None = None,
    timeout_seconds: int = 300,
    sample_interval: float = 0.05,
) -> dict[str, str]:
    """
    Execute code or script with dependency resolution (uv) while collecting basic benchmark metrics.

    Metrics:
        - wall_time_seconds
        - peak_rss_mb
        - cpu_time_seconds (user+system)
        - exit_code

    Parameters mirror run_script_with_dependencies plus:
        sample_interval: Polling interval for memory usage sampling.
    """
    result = run_script_with_dependencies(
        script_content=script_content,
        script_path=script_path,
        python_version=python_version,
        dependencies=dependencies,
        args=args,
        async_run=True,
        stream=False,
        timeout_seconds=timeout_seconds,
    )
    job_id = result["job_id"]
    rec = JOBS[job_id]
    proc = rec.process
    ps_proc = psutil.Process(proc.pid)
    peak_rss = 0
    start_cpu = ps_proc.cpu_times()
    while proc.poll() is None:
        try:
            rss = ps_proc.memory_info().rss
            if rss > peak_rss:
                peak_rss = rss
        except psutil.Error:
            break
        time.sleep(sample_interval)
    end_cpu = ps_proc.cpu_times()
    wall = time.time() - rec.start_time
    _finalize_capture(rec)
    return {
        "stdout": "".join(rec.stdout_chunks),
        "stderr": "".join(rec.stderr_chunks),
        "exit_code": str(proc.returncode),
        "wall_time_seconds": f"{wall:.4f}",
        "cpu_time_seconds": f"{(end_cpu.user - start_cpu.user) + (end_cpu.system - start_cpu.system):.4f}",
        "peak_rss_mb": f"{peak_rss / (1024 * 1024):.2f}",
        "python_version_used": python_version,
        "resolved_dependencies": str(dependencies or []),
    }


async def _poll_stream(job_id: str) -> None:
    """
    Background coroutine that periodically harvests stdout/stderr for a streaming job.
    """
    rec = JOBS.get(job_id)
    if not rec:
        return
    while rec.process.poll() is None:
        _nonblocking_capture(rec)
        await asyncio.sleep(STREAM_POLL_INTERVAL)
    _finalize_capture(rec)


def _nonblocking_capture(rec: JobRecord) -> None:
    """
    Read available data without blocking and append to chunks.
    """
    proc = rec.process
    if proc.stdout:
        while True:
            line = proc.stdout.readline()
            if not line:
                break
            rec.stdout_chunks.append(line)
    if proc.stderr:
        while True:
            line = proc.stderr.readline()
            if not line:
                break
            rec.stderr_chunks.append(line)


def _finalize_capture(rec: JobRecord) -> None:
    """
    Capture any remaining output and mark job finished.
    """
    if rec.finished:
        return
    _nonblocking_capture(rec)
    rec.exit_code = rec.process.returncode
    rec.finished = True


@mcp.resource("job-stream://{job_id}")
def get_job_output_stream(job_id: str) -> str:
    """
    Incremental retrieval of a job's current stdout/stderr snapshot as a single concatenated text blob.
    Intended for polling; returns a streaming-friendly chunk WITHOUT marking the job finished.

    Format:
        ---STDOUT---
        <current stdout>
        ---STDERR---
        <current stderr>

    If the job was started with streaming enabled, this will harvest any new output before returning.
    It does not finalize or remove the job even if the underlying process has exited, allowing
    repeated polling until an external cleanup step is performed.

    Returns:
        A text block separating stdout and stderr for easy parsing.
    """
    rec = JOBS.get(job_id)
    if not rec:
        return f"[error] job not found: {job_id}"
    if rec.stream:
        _nonblocking_capture(rec)

    # Do not finalize capture here; allow caller to decide lifecycle.
    return (
        "---STDOUT---\n"
        + "".join(rec.stdout_chunks)
        + "\n---STDERR---\n"
        + "".join(rec.stderr_chunks)
    )


def main() -> None:
    # Entry point kept for script invocation compatibility; prefer mcp.run() when used as an MCP server.
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
