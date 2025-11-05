from __future__ import annotations

import asyncio
import subprocess
import sys
import textwrap
import time
import uuid
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Literal, List

import psutil
from fastmcp import FastMCP
from pydantic import BaseModel, Field

# Configure logging (file + stderr console)
LOG_PATH = Path(__file__).resolve().parent / "python_mcp_server.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stderr),
    ],
)
logger = logging.getLogger("python_mcp_server")
logger.info("Logger initialized; log file at %s", LOG_PATH)

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
    finalized_elapsed: Optional[float] = None  # Frozen elapsed time set at finalization
    is_inline_temp: bool = False  # Mark if script_path points to a temp inline file for cleanup


# ---------------------------
# Pydantic output models
# ---------------------------
class RunScriptResult(BaseModel):
    stdout: str = Field(description="Full captured stdout.")
    stderr: str = Field(description="Full captured stderr.")
    exit_code: int = Field(description="Process exit code.")
    execution_strategy: Literal["uv-run", "system-python"] = Field(description="Interpreter strategy chosen.")
    elapsed_seconds: float = Field(description="Wall time in seconds.")

class AsyncJobStart(BaseModel):
    job_id: str = Field(description="Opaque job identifier.")
    status: Literal["started"] = Field(description="Always 'started' for a newly created job.")
    execution_strategy: Literal["uv-run", "system-python"] = Field(description="Interpreter strategy chosen.")

class RunWithDepsResult(RunScriptResult):
    resolved_dependencies: List[str] = Field(description="Dependencies requested (after normalization).")
    python_version_used: str = Field(description="Interpreter version used (exact minor).")

class BenchmarkResult(RunWithDepsResult):
    wall_time_seconds: float = Field(description="Wall clock time.")
    cpu_time_seconds: float = Field(description="User+system CPU time.")
    peak_rss_mb: float = Field(description="Peak resident memory usage in MB.")

class AsyncDepsJobStart(BaseModel):
    job_id: str = Field(description="Opaque job identifier.")
    status: Literal["started"] = Field(description="Always 'started' for a newly created job.")
    python_version_used: str = Field(description="Interpreter version used (exact minor).")
    resolved_dependencies: List[str] = Field(description="Dependencies requested (after normalization).")

JOBS: dict[str, JobRecord] = {}
STREAM_POLL_INTERVAL = 0.2  # seconds

def _infer_python_version_from_pyproject(workdir: Path) -> str | None:
    """
    Infer an exact minor python version (e.g. '3.13') from the project's pyproject.toml
    requires-python field if present. Returns None if not found or parsing fails.

    Strategy:
      - Look for project.requires-python (PEP 621)
      - Extract first occurrence of \d+.\d+ from the version spec (e.g. '>=3.13' -> '3.13')
    """
    pyproject = workdir / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        import tomllib
        import re
        data = tomllib.loads(pyproject.read_text())
        requires = data.get("project", {}).get("requires-python")
        if not requires:
            return None
        match = re.search(r"(\\d+\\.\\d+)", requires)
        if match:
            return match.group(1)
    except Exception:
        return None
    return None

# ---------------------------
# Internal execution helpers
# ---------------------------

def _exec_script_in_dir_sync(
    directory: Path,
    script_path: Path | None,
    script_content: str | None,
    args: list[str] | None,
    use_uv: bool,
    python_version: str | None,
    timeout_seconds: int,
) -> RunScriptResult:
    workdir = Path(directory).expanduser().resolve()
    if not workdir.is_dir():
        raise FileNotFoundError(f"Directory not found: {workdir}")
    if script_content:
        tmp_name = f"inline_{uuid.uuid4().hex}.py"
        script_path_local = workdir / tmp_name
        script_path_local.write_text(script_content)
        is_inline = True
    else:
        if not script_path:
            raise ValueError("Either 'script_path' or 'script_content' must be provided.")
        candidate = script_path.expanduser()
        if not candidate.is_absolute():
            candidate = (workdir / candidate).resolve()
        script_path_local = candidate.resolve()
        if not script_path_local.is_file():
            raise FileNotFoundError(f"Script file not found: {script_path_local}")
        is_inline = False
    command: list[str]
    execution_strategy = "system-python"
    if use_uv:
        command = ["uv", "run"]
        # Infer python version from pyproject.toml if not explicitly provided.
        if not python_version:
            python_version = _infer_python_version_from_pyproject(workdir)
        if python_version:
            command += ["--python", python_version]
        execution_strategy = "uv-run"
    else:
        command = ["python"]
    command.append(str(script_path_local))
    if args:
        command.extend(args)
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
            stderr += "\n[TIMEOUT]"
    finally:
        if is_inline and script_path_local.exists():
            try:
                script_path_local.unlink()
            except Exception:
                pass
    return RunScriptResult(
        stdout=stdout,
        stderr=stderr,
        exit_code=proc.returncode,
        execution_strategy=execution_strategy,
        elapsed_seconds=time.time() - start,
    )

def _exec_with_dependencies_sync(
    script_content: str | None,
    script_path: Path | None,
    python_version: str,
    dependencies: list[str] | None,
    args: list[str] | None,
    timeout_seconds: int,
) -> RunWithDepsResult:
    if not script_content and not script_path:
        raise ValueError("Provide either 'script_content' or 'script_path'.")
    if script_content and script_path:
        raise ValueError("Provide only one of 'script_content' or 'script_path'.")
    if script_path:
        spath = Path(script_path).expanduser().resolve()
        if not spath.is_file():
            raise FileNotFoundError(f"Script not found: {spath}")
        is_inline = False
    else:
        spath = Path(f"inline_dep_{uuid.uuid4().hex}.py").resolve()
        spath.write_text(script_content or "")
        is_inline = True
    command: list[str] = ["uv", "run", "--python", python_version]
    resolved_dependencies = dependencies[:] if dependencies else []
    for dep in resolved_dependencies:
        command += ["--with", dep]
    command.append(str(spath))
    if args:
        command.extend(args)
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
        if is_inline and spath.exists():
            try:
                spath.unlink()
            except Exception:
                pass
    return RunWithDepsResult(
        stdout=stdout,
        stderr=stderr,
        exit_code=proc.returncode,
        execution_strategy="uv-run",
        elapsed_seconds=time.time() - start,
        resolved_dependencies=resolved_dependencies,
        python_version_used=python_version,
    )


@mcp.tool(tags=["execution", "sync"])
def run_script_in_dir(
    directory: Path,
    script_path: Path | None = None,
    script_content: str | None = None,
    args: list[str] | None = None,
    use_uv: bool = True,
    python_version: str | None = None,
    timeout_seconds: int = 300,
) -> RunScriptResult:
    """
    Execute a Python script (existing file or inline content) inside a target directory using uv or system Python.

    This is the synchronous variant. For asynchronous/background execution with optional streaming,
    use run_script_in_dir_async.

    Parameters:
        directory: Base directory; must exist.
        script_path: Absolute or relative path to script within directory. Mutually exclusive with script_content.
        script_content: Inline Python source. When provided, a temporary file is created (script_path must be None).
        args: Optional argument list appended after the script path.
        use_uv: When True use 'uv run'; otherwise system 'python'.
        python_version: Exact minor version (e.g. '3.12') – honored only when use_uv=True.
        timeout_seconds: Max wall time; 0 disables timeout (unbounded).

    Returns (RunScriptResult):
        stdout: Captured stdout.
        stderr: Captured stderr.
        exit_code: Process exit code.
        execution_strategy: 'uv-run' or 'system-python'.
        elapsed_seconds: Wall time in seconds.

    Errors:
        FileNotFoundError: directory or script_path missing.
        ValueError: Neither or both of script_path and script_content provided.
        Timeout: On timeout, process killed; stderr is suffixed with '[TIMEOUT]'.

    Path Resolution:
        Relative script_path values are resolved against 'directory'.

    Notes:
        - No sandboxing; full filesystem/network access (per project requirements).
        - Temporary inline file removed after synchronous completion.
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
        if not python_version:
            python_version = _infer_python_version_from_pyproject(workdir)
        if python_version:
            command += ["--python", python_version]
        execution_strategy = "uv-run"
    else:
        command = ["python"]

    command.append(str(script_path_local))
    if args:
        command.extend(args)

    # (Async mode removed from this function; see run_script_in_dir_async.)

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

    return RunScriptResult(
        stdout=stdout,
        stderr=stderr,
        exit_code=proc.returncode,
        execution_strategy=execution_strategy,
        elapsed_seconds=time.time() - start,
    )


@mcp.tool(tags=["execution", "dependencies", "sync"])
def run_script_with_dependencies(
    script_content: str | None = None,
    script_path: Path | None = None,
    python_version: str = "3.12",
    dependencies: list[str] | None = None,
    args: list[str] | None = None,
    timeout_seconds: int = 300,
    auto_parse_imports: bool = True,
) -> RunWithDepsResult:
    """
    Execute transient inline code or an existing script inside an ephemeral uv environment with explicit dependencies.

    This is the synchronous variant. For asynchronous/background execution use run_script_with_dependencies_async.

    Parameters:
        script_content: Inline code (mutually exclusive with script_path).
        script_path: Existing script file path.
        python_version: Exact minor version (e.g. '3.12').
        dependencies: List[str] | None - explicit package specifiers (PEP 440). May be supplemented automatically.
        args: Optional CLI arguments.
        timeout_seconds: 0 disables timeout.
        auto_parse_imports: When True, parse script source for import statements and append missing top-level modules
                           to dependency list (best-effort heuristic; does not guarantee install success).

    Returns (RunWithDepsResult):
        Extends RunScriptResult with:
          resolved_dependencies: list[str]
          python_version_used: str

    Errors:
        FileNotFoundError: script_path missing.
        ValueError: Exclusivity violated.
        Timeout: Same semantics as run_script_in_dir.

    Notes:
        - Uses 'uv run --with <dep>' for each dependency.
        - Auto-import parsing is heuristic: it treats 'from pkg.sub import X' and 'import pkg.sub' both as 'pkg'.
        - Built-in / stdlib modules are not filtered exhaustively; a small skip list is applied.
        - Future: environment caching keyed by hash(deps+python_version).
    """

    if not script_content and not script_path:
        raise ValueError("Provide either 'script_content' or 'script_path'.")
    if script_content and script_path:
        raise ValueError("Provide only one of 'script_content' or 'script_path'.")

    if script_path:
        spath = Path(script_path).expanduser().resolve()
        if not spath.is_file():
            raise FileNotFoundError(f"Script not found: {spath}")
        source_text = spath.read_text()
    else:
        # Inline code -> temp file in cwd of process (use current working directory)
        spath = Path(f"inline_dep_{uuid.uuid4().hex}.py").resolve()
        source_text = script_content or ""
        spath.write_text(source_text)

    # Build initial dependency list
    resolved_dependencies = dependencies[:] if dependencies else []

    if auto_parse_imports:
        # Simple heuristic import parser
        import re
        lines = source_text.splitlines()
        detected: set[str] = set()
        import_pattern = re.compile(r"^(?:from|import)\\s+([a-zA-Z0-9_\\.]+)")
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = import_pattern.match(line)
            if not m:
                continue
            raw = m.group(1)
            # Take top-level package name (segment before dot)
            top = raw.split(".")[0]
            detected.add(top)

        # Basic skip list for common stdlib / internal modules to reduce noise
        skip = {
            "sys", "os", "re", "time", "uuid", "json", "math", "pathlib", "subprocess",
            "typing", "dataclasses", "logging", "asyncio", "shutil", "itertools",
            "functools", "collections", "statistics", "pprint", "enum"
        }

        for pkg in sorted(detected):
            if pkg in skip:
                continue
            if pkg not in resolved_dependencies:
                resolved_dependencies.append(pkg)

    command: list[str] = ["uv", "run", "--python", python_version]
    for dep in resolved_dependencies:
        command += ["--with", dep]
    command.append(str(spath))
    if args:
        command.extend(args)

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

    return RunWithDepsResult(
        stdout=stdout,
        stderr=stderr,
        exit_code=proc.returncode,
        execution_strategy="uv-run",
        elapsed_seconds=time.time() - start,
        resolved_dependencies=resolved_dependencies,
        python_version_used=python_version,
    )


@mcp.tool(tags=["execution", "async", "stream"])
def run_script_in_dir_async(
    directory: Path,
    script_path: Path | None = None,
    script_content: str | None = None,
    args: list[str] | None = None,
    use_uv: bool = True,
    python_version: str | None = None,
    stream: bool = False,
) -> AsyncJobStart:
    """
    Asynchronously execute a Python script (existing file or inline content) inside a target directory.

    Streaming:
        If stream=True, incremental stdout/stderr snapshots can be polled via resource job-stream://{job_id}
        or consolidated output via get_job_output.

    Parameters:
        directory: Base directory; must exist.
        script_path: Absolute or relative path to a script within directory (mutually exclusive with script_content).
        script_content: Inline Python source (mutually exclusive with script_path). A temporary file is created.
        args: Optional argument list appended after the script path.
        use_uv: When True uses 'uv run'; otherwise system 'python'.
        python_version: Exact minor (e.g. '3.12') honored only when use_uv=True.
        stream: Enable periodic harvesting of stdout/stderr.

    Returns (AsyncJobStart):
        job_id: Identifier to use with get_job_output / job-stream resource.
        status: Always 'started'.
        execution_strategy: 'uv-run' or 'system-python'.

    Errors:
        FileNotFoundError: directory or script_path missing.
        ValueError: Neither or both of script_path and script_content provided.

    Notes:
        - No timeout applied at launch; external tooling (future) may enforce or kill.
        - Temporary inline file is retained until job finalization (to allow inspection if needed).
    """
    workdir = Path(directory).expanduser().resolve()
    if not workdir.is_dir():
        raise FileNotFoundError(f"Directory not found: {workdir}")

    if script_content:
        tmp_name = f"inline_{uuid.uuid4().hex}.py"
        script_path_local = workdir / tmp_name
        script_path_local.write_text(script_content)
    else:
        if not script_path:
            raise ValueError("Either 'script_path' or 'script_content' must be provided.")
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
        is_inline_temp=bool(script_content),
    )
    JOBS[job_id] = rec
    logger.info(
        "run_script_in_dir_async started job_id=%s strategy=%s cwd=%s stream=%s",
        job_id,
        execution_strategy,
        workdir,
        stream,
    )
    if stream:
        asyncio.get_event_loop().create_task(_poll_stream(job_id))
        logger.info("Streaming poller launched for job_id=%s", job_id)

    return AsyncJobStart(
        job_id=job_id,
        status="started",
        execution_strategy=execution_strategy,
    )


@mcp.tool(tags=["execution", "dependencies", "async", "stream"])
def run_script_with_dependencies_async(
    script_content: str | None = None,
    script_path: Path | None = None,
    python_version: str = "3.12",
    dependencies: list[str] | None = None,
    args: list[str] | None = None,
    stream: bool = False,
    auto_parse_imports: bool = True,
) -> AsyncDepsJobStart:
    """
    Asynchronously execute inline code or an existing script in a transient uv environment with dependencies.

    Mutual Exclusivity:
        Provide exactly one of script_content or script_path.

    Streaming:
        When stream=True, incremental output available via job-stream://{job_id}.

    Parameters:
        script_content: Inline Python source.
        script_path: Existing script path.
        python_version: Exact minor version (e.g. '3.12').
        dependencies: List of package specifiers (PEP 440).
        args: Optional CLI arguments.
        stream: Enable periodic harvesting of stdout/stderr.
        auto_parse_imports: When True, parse script source for import statements and append missing top-level modules
                            to dependency list (heuristic; applies skip list of common stdlib modules).

    Returns (AsyncDepsJobStart):
        job_id, status, python_version_used, resolved_dependencies.

    Errors:
        FileNotFoundError: script_path missing.
        ValueError: Exclusivity violated.

    Notes:
        - Uses 'uv run --with <dep>' for each dependency (explicit + inferred).
        - Auto-import parsing heuristics:
            * Extracts top-level package from lines matching ^(from|import) <name>
            * Skip list filters common stdlib modules
            * Does not resolve version pins automatically
        - Future enhancement: environment caching to reduce cold start latency.
    """
    if not script_content and not script_path:
        raise ValueError("Provide either 'script_content' or 'script_path'.")
    if script_content and script_path:
        raise ValueError("Provide only one of 'script_content' or 'script_path'.")

    if script_path:
        spath = Path(script_path).expanduser().resolve()
        if not spath.is_file():
            raise FileNotFoundError(f"Script not found: {spath}")
        source_text = spath.read_text()
    else:
        spath = Path(f"inline_dep_{uuid.uuid4().hex}.py").resolve()
        spath.write_text(script_content or "")
        source_text = script_content or ""

    resolved_dependencies = dependencies[:] if dependencies else []

    if auto_parse_imports:
        import re
        lines = source_text.splitlines()
        detected: set[str] = set()
        pattern = re.compile(r"^(?:from|import)\s+([a-zA-Z0-9_\.]+)")
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = pattern.match(line)
            if not m:
                continue
            raw = m.group(1)
            top = raw.split(".")[0]
            detected.add(top)
        skip = {
            "sys", "os", "re", "time", "uuid", "json", "math", "pathlib", "subprocess",
            "typing", "dataclasses", "logging", "asyncio", "shutil", "itertools",
            "functools", "collections", "statistics", "pprint", "enum"
        }
        for pkg in sorted(detected):
            if pkg in skip:
                continue
            if pkg not in resolved_dependencies:
                resolved_dependencies.append(pkg)

    command: list[str] = ["uv", "run", "--python", python_version]
    for dep in resolved_dependencies:
        command += ["--with", dep]
    command.append(str(spath))
    if args:
        command.extend(args)

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
    logger.info(
        "run_script_with_dependencies_async started job_id=%s python=%s deps=%s stream=%s auto_parse_imports=%s",
        job_id,
        python_version,
        resolved_dependencies,
        stream,
        auto_parse_imports,
    )
    if stream:
        asyncio.get_event_loop().create_task(_poll_stream(job_id))
        logger.info("Streaming poller launched for job_id=%s", job_id)

    return AsyncDepsJobStart(
        job_id=job_id,
        status="started",
        python_version_used=python_version,
        resolved_dependencies=resolved_dependencies,
    )


@mcp.tool(tags=["jobs", "introspection"])
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


@mcp.tool(tags=["jobs", "introspection"])
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


@mcp.tool(tags=["jobs", "control"])
def kill_job(job_id: str) -> dict[str, str]:
    """
    Terminate a running job. If already finished, returns status 'already-finished' without modifying output.

    Parameters:
        job_id: Job identifier.
    """
    rec = JOBS.get(job_id)
    if not rec:
        logger.warning("kill_job requested for missing job_id=%s", job_id)
        raise ValueError(f"No such job: {job_id}")
    if rec.process.poll() is None:
        logger.info("Killing running job_id=%s pid=%s", job_id, rec.process.pid)
        rec.process.kill()
        time.sleep(0.05)
        _finalize_capture(rec)
        status = "killed"
    else:
        # Already finished
        if not rec.finished:
            _finalize_capture(rec)
        status = "already-finished"
    logger.info(
        "Job kill request processed job_id=%s status=%s exit_code=%s frozen_elapsed=%.2fs",
        job_id,
        status,
        rec.process.returncode,
        rec.finalized_elapsed if rec.finalized_elapsed is not None else (time.time() - rec.start_time),
    )
    return {
        "job_id": job_id,
        "status": status,
        "exit_code": str(rec.process.returncode),
    }

@mcp.tool(tags=["jobs", "maintenance"])
def cleanup_jobs(
    remove_inline: bool = True,
    only_finished: bool = True,
) -> dict[str, int]:
    """
    Remove jobs from the registry and optionally delete their temporary inline script files.

    Parameters:
        remove_inline: When True, delete temp inline script files created from script_content.
        only_finished: When True, only remove finished jobs; when False remove all jobs.

    Returns:
        {
          "removed": int,          # Number of jobs removed
          "remaining": int,        # Jobs still in registry
          "inline_deleted": int    # Temp inline script files deleted
        }

    Notes:
        - Inline temp scripts are marked via JobRecord.is_inline_temp.
        - Safe to call repeatedly; missing files ignored.
    """
    removed = 0
    inline_deleted = 0
    for jid in list(JOBS.keys()):
        rec = JOBS[jid]
        if only_finished and not rec.finished:
            continue
        if remove_inline and rec.is_inline_temp and rec.script_path and rec.script_path.exists():
            try:
                rec.script_path.unlink()
                inline_deleted += 1
            except Exception:
                pass
        del JOBS[jid]
        removed += 1
    return {
        "removed": removed,
        "remaining": len(JOBS),
        "inline_deleted": inline_deleted,
    }


@mcp.tool(tags=["benchmark", "performance", "sync"])
def benchmark_script(
    script_content: str | None = None,
    script_path: Path | None = None,
    python_version: str = "3.12",
    dependencies: list[str] | None = None,
    args: list[str] | None = None,
    timeout_seconds: int = 300,
    sample_interval: float = 0.05,
) -> BenchmarkResult:
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
    # Use internal helper to start process manually (avoid calling decorated tool object)
    if not script_content and not script_path:
        raise ValueError("Provide either 'script_content' or 'script_path'.")
    if script_content and script_path:
        raise ValueError("Provide only one of 'script_content' or 'script_path'.")
    if script_path:
        spath = Path(script_path).expanduser().resolve()
        if not spath.is_file():
            raise FileNotFoundError(f"Script not found: {spath}")
        is_inline = False
    else:
        spath = Path(f"inline_bench_{uuid.uuid4().hex}.py").resolve()
        spath.write_text(script_content or "")
        is_inline = True
    command: list[str] = ["uv", "run", "--python", python_version]
    resolved_dependencies = dependencies[:] if dependencies else []
    for dep in resolved_dependencies:
        command += ["--with", dep]
    command.append(str(spath))
    if args:
        command.extend(args)
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    start_time = time.time()
    ps_proc = psutil.Process(proc.pid)
    peak_rss = 0
    start_cpu = ps_proc.cpu_times()
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    while proc.poll() is None:
        try:
            rss = ps_proc.memory_info().rss
            if rss > peak_rss:
                peak_rss = rss
        except psutil.Error:
            break
        # Non-blocking reads
        if proc.stdout:
            line = proc.stdout.readline()
            while line:
                stdout_chunks.append(line)
                line = proc.stdout.readline()
        if proc.stderr:
            line = proc.stderr.readline()
            while line:
                stderr_chunks.append(line)
                line = proc.stderr.readline()
        time.sleep(sample_interval)
    # Capture any trailing output
    if proc.stdout:
        for line in proc.stdout.readlines():
            stdout_chunks.append(line)
    if proc.stderr:
        for line in proc.stderr.readlines():
            stderr_chunks.append(line)
    end_cpu = ps_proc.cpu_times()
    wall = time.time() - start_time
    if is_inline and spath.exists():
        try:
            spath.unlink()
        except Exception:
            pass
    return BenchmarkResult(
        stdout="".join(stdout_chunks),
        stderr="".join(stderr_chunks),
        exit_code=proc.returncode,
        execution_strategy="uv-run",
        elapsed_seconds=wall,
        resolved_dependencies=list(resolved_dependencies),
        python_version_used=python_version,
        wall_time_seconds=wall,
        cpu_time_seconds=(end_cpu.user - start_cpu.user) + (end_cpu.system - start_cpu.system),
        peak_rss_mb=peak_rss / (1024 * 1024),
    )


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
    Capture any remaining output and mark job finished. Freeze elapsed time.
    """
    if rec.finished:
        return
    _nonblocking_capture(rec)
    rec.exit_code = rec.process.returncode
    rec.finished = True
    rec.finalized_elapsed = time.time() - rec.start_time
    logger.info(
        "Job finalized job_id=%s exit_code=%s frozen_elapsed=%.2fs stdout_len=%d stderr_len=%d",
        rec.job_id,
        rec.exit_code,
        rec.finalized_elapsed,
        sum(len(c) for c in rec.stdout_chunks),
        sum(len(c) for c in rec.stderr_chunks),
    )


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
    # Startup diagnostics
    cwd = Path.cwd()
    py_exec = sys.executable
    py_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    logger.info("Starting MCP server (transport=stdio)")
    logger.info("Diagnostics: cwd=%s executable=%s python_version=%s uv_present=%s",
                cwd,
                py_exec,
                py_version,
                shutil.which("uv") is not None)
    # List key environment variables that might affect execution
    interesting_env = {k: v for k, v in os.environ.items() if k.startswith(("PYTHON", "UV", "FASTMCP"))}
    if interesting_env:
        logger.info("Environment (filtered): %s", interesting_env)
    else:
        logger.info("No filtered environment variables detected.")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
