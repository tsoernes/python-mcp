from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import textwrap
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Literal, Optional

import psutil
from dotenv import dotenv_values
from fastmcp import FastMCP
from pydantic import BaseModel, Field

# Import smart_async components
from .smart_async import (
    STATE as ASYNC_STATE,
)
from .smart_async import (
    cancel_job,
    create_progress_callback,
    get_job_status,
    initialize_state,
    list_jobs,
    prune_jobs,
    smart_async,
)

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
    is_inline_temp: bool = (
        False  # Mark if script_path points to a temp inline file for cleanup
    )


# ---------------------------
# Pydantic output models
# ---------------------------
class RunScriptResult(BaseModel):
    stdout: str = Field(description="Full captured stdout.")
    stderr: str = Field(description="Full captured stderr.")
    exit_code: int = Field(description="Process exit code.")
    execution_strategy: Literal["uv-run", "system-python"] = Field(
        description="Interpreter strategy chosen."
    )
    elapsed_seconds: float = Field(description="Wall time in seconds.")


class AsyncJobStart(BaseModel):
    job_id: str = Field(description="Opaque job identifier.")
    status: Literal["started"] = Field(
        description="Always 'started' for a newly created job."
    )
    execution_strategy: Literal["uv-run", "system-python"] = Field(
        description="Interpreter strategy chosen."
    )


class RunWithDepsResult(RunScriptResult):
    resolved_dependencies: List[str] = Field(
        description="Dependencies requested (after normalization)."
    )
    python_version_used: str = Field(
        description="Interpreter version used (exact minor)."
    )


class BenchmarkResult(RunWithDepsResult):
    wall_time_seconds: float = Field(description="Wall clock time.")
    cpu_time_seconds: float = Field(description="User+system CPU time.")
    peak_rss_mb: float = Field(description="Peak resident memory usage in MB.")


class AsyncDepsJobStart(BaseModel):
    job_id: str = Field(description="Opaque job identifier.")
    status: Literal["started"] = Field(
        description="Always 'started' for a newly created job."
    )
    python_version_used: str = Field(
        description="Interpreter version used (exact minor)."
    )
    resolved_dependencies: List[str] = Field(
        description="Dependencies requested (after normalization)."
    )


JOBS: dict[str, JobRecord] = {}
STREAM_POLL_INTERVAL = 0.2  # seconds


def _ensure_python_version(version: str) -> bool:
    """
    Ensure the specified Python version is installed via uv.

    Args:
        version: Python version string (e.g., '3.13', '3.12')

    Returns:
        True if version is available or was installed successfully
    """
    try:
        # Check if version is already available
        result = subprocess.run(
            ["uv", "python", "list"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and version in result.stdout:
            logger.info(f"Python {version} already available")
            return True

        # Install the version
        logger.info(f"Installing Python {version} via uv...")
        result = subprocess.run(
            ["uv", "python", "install", version],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode == 0:
            logger.info(f"Successfully installed Python {version}")
            return True
        else:
            logger.warning(f"Failed to install Python {version}: {result.stderr}")
            return False
    except Exception as e:
        logger.warning(f"Error ensuring Python {version}: {e}")
        return False


def _parse_imports(source_text: str) -> list[str]:
    """
    Parse import statements from Python source and return top-level package names.

    Args:
        source_text: Python source code

    Returns:
        List of top-level package names (excluding stdlib)
    """
    import re

    lines = source_text.splitlines()
    detected: set[str] = set()
    import_pattern = re.compile(r"^(?:from|import)\s+([a-zA-Z0-9_\.]+)")

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

    # Basic skip list for common stdlib / internal modules
    skip = {
        "sys",
        "os",
        "re",
        "time",
        "uuid",
        "json",
        "math",
        "pathlib",
        "subprocess",
        "typing",
        "dataclasses",
        "logging",
        "asyncio",
        "shutil",
        "itertools",
        "functools",
        "collections",
        "statistics",
        "pprint",
        "enum",
        "io",
        "datetime",
        "copy",
        "abc",
        "warnings",
        "contextlib",
        "weakref",
        "gc",
        "pickle",
        "struct",
        "array",
        "socket",
        "ssl",
        "http",
        "urllib",
        "email",
        "html",
        "xml",
        "threading",
        "multiprocessing",
        "concurrent",
        "queue",
    }

    return [pkg for pkg in sorted(detected) if pkg not in skip]


# ---------------------------
# Environment variable helpers
# ---------------------------


def _load_env_file(env_file: Path) -> dict[str, str]:
    """
    Load environment variables from a .env file using python-dotenv.

    Args:
        env_file: Path to .env file

    Returns:
        Dictionary of environment variables

    Raises:
        FileNotFoundError: If the env_file does not exist
    """
    if not env_file.exists():
        raise FileNotFoundError(f"Environment file not found: {env_file}")

    # dotenv_values returns a dict with all values from the .env file
    # It handles comments, quotes, multiline values, etc.
    env_dict = dotenv_values(env_file)

    # Filter out None values and convert to dict[str, str]
    return {k: v for k, v in env_dict.items() if v is not None}


def _build_process_env(
    env_vars: dict[str, str] | None = None,
    env_file: Path | None = None,
) -> dict[str, str]:
    """
    Build the environment dictionary for subprocess execution.

    Merges in this order (later overrides earlier):
    1. Current process environment (os.environ)
    2. Variables from env_file (if provided)
    3. Variables from env_vars dict (if provided)

    Args:
        env_vars: Optional dictionary of environment variables
        env_file: Optional path to .env file

    Returns:
        Merged environment dictionary
    """
    # Start with inherited environment
    proc_env = os.environ.copy()

    # Load and merge .env file if provided
    if env_file:
        file_vars = _load_env_file(env_file)
        proc_env.update(file_vars)

    # Merge explicit env_vars (highest priority)
    if env_vars:
        proc_env.update(env_vars)

    return proc_env


def _infer_python_version_from_pyproject(workdir: Path) -> str | None:
    """
    Infer an exact minor python version (e.g. '3.13') from the project's pyproject.toml
    requires-python field if present. Returns None if not found or parsing fails.

    Strategy:
      - Look for project.requires-python (PEP 621)
      - Extract first occurrence of \\d+.\\d+ from the version spec (e.g. '>=3.13' -> '3.13')
    """
    pyproject = workdir / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        import re
        import tomllib

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
    env_vars: dict[str, str] | None = None,
    env_file: Path | None = None,
) -> RunScriptResult:
    workdir = Path(directory).expanduser().resolve()
    if not workdir.is_dir():
        raise FileNotFoundError(f"Directory not found: {workdir}")
    if script_content:
        inline_dir = workdir / "inline_scripts"
        inline_dir.mkdir(parents=True, exist_ok=True)
        tmp_name = f"inline_{uuid.uuid4().hex}.py"
        script_path_local = inline_dir / tmp_name
        script_path_local.write_text(script_content)
        is_inline = True
    else:
        if not script_path:
            raise ValueError(
                "Either 'script_path' or 'script_content' must be provided."
            )
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
    # Build environment
    proc_env = _build_process_env(env_vars=env_vars, env_file=env_file)

    start = time.time()
    try:
        proc = subprocess.Popen(
            command,
            cwd=str(workdir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=proc_env,
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
    env_vars: dict[str, str] | None = None,
    env_file: Path | None = None,
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

    # Build environment
    proc_env = _build_process_env(env_vars=env_vars, env_file=env_file)

    start = time.time()
    try:
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=proc_env,
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


@mcp.tool(tags=["execution"])
@smart_async(default_timeout=20.0)
async def py_run_script_in_dir(
    directory: Path,
    script_path: Path | None = None,
    script_content: str | None = None,
    args: list[str] | None = None,
    use_uv: bool = True,
    python_version: str | None = None,
    timeout_seconds: int = 300,
    env_vars: dict[str, str] | None = None,
    env_file: Path | None = None,
    async_mode: bool = False,
    job_label: str | None = None,
    auto_install_deps: bool = True,
) -> RunScriptResult | dict[str, Any]:
    """
    Execute a Python script (existing file or inline content) inside a target directory using uv or system Python.

    Uses smart async: completes synchronously if under 20s, switches to background if longer.

    Parameters:
        directory: Base directory; must exist.
        script_path: Absolute or relative path to script within directory. Mutually exclusive with script_content.
        script_content: Inline Python source. When provided, a temporary file is created (script_path must be None).
        args: Optional argument list appended after the script path.
        use_uv: When True use 'uv run'; otherwise system 'python'.
        python_version: Exact minor version (e.g. '3.12') – honored only when use_uv=True.
        timeout_seconds: Max wall time for subprocess; 0 disables timeout (unbounded).
        env_vars: Optional dictionary of environment variables to set for the script.
        env_file: Optional path to .env file to load environment variables from.
        async_mode: If True, launch in background immediately (default: False).
        job_label: Optional label for job tracking.
        auto_install_deps: If True, auto-detect and install missing dependencies (default: True).

    Returns:
        RunScriptResult if completed synchronously, or job metadata if switched to background.

    Errors:
        FileNotFoundError: directory or script_path missing.
        ValueError: Neither or both of script_path and script_content provided.

    Path Resolution:
        Relative script_path values are resolved against 'directory'.

    Notes:
        - No sandboxing; full filesystem/network access (per project requirements).
        - Temporary inline file removed after completion.
    """

    workdir = Path(directory).expanduser().resolve()
    if not workdir.is_dir():
        raise FileNotFoundError(f"Directory not found: {workdir}")

    if script_content:
        inline_dir = workdir / "inline_scripts"
        inline_dir.mkdir(parents=True, exist_ok=True)
        tmp_name = f"inline_{uuid.uuid4().hex}.py"
        script_path_local = inline_dir / tmp_name
        script_path_local.write_text(script_content)
        source_text = script_content
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
        source_text = script_path_local.read_text()

    # Auto-detect dependencies if enabled
    detected_deps: list[str] = []
    if auto_install_deps and use_uv:
        detected_deps = _parse_imports(source_text)
        if detected_deps:
            logger.info(f"Auto-detected dependencies: {detected_deps}")

    command: list[str]
    execution_strategy = "system-python"
    if use_uv:
        command = ["uv", "run"]

        # Use --isolated to ignore pyproject.toml when running standalone scripts with auto-deps
        if detected_deps:
            command.append("--isolated")

        if not python_version:
            python_version = _infer_python_version_from_pyproject(workdir)

        # If we have a version requirement, ensure it's installed
        if python_version:
            _ensure_python_version(python_version)
            command += ["--python", python_version]

        # Add auto-detected dependencies
        for dep in detected_deps:
            command += ["--with", dep]

        execution_strategy = "uv-run"
    else:
        command = ["python"]

    command.append(str(script_path_local))
    if args:
        command.extend(args)

    # Build environment
    proc_env = _build_process_env(env_vars=env_vars, env_file=env_file)

    # Async execution using asyncio subprocess
    start = time.time()

    # Get output callback for streaming (always enabled for background jobs)
    from .smart_async import create_output_callback, current_job_id

    output_callback = create_output_callback()

    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(workdir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=proc_env,
        )

        # If we have a job context (background job), read line by line for streaming
        if current_job_id.get() and output_callback:
            stdout_lines = []
            stderr_lines = []

            async def read_stdout():
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    text = line.decode("utf-8")
                    stdout_lines.append(text)
                    output_callback(stdout=text, stderr="")

            async def read_stderr():
                while True:
                    line = await proc.stderr.readline()
                    if not line:
                        break
                    text = line.decode("utf-8")
                    stderr_lines.append(text)
                    output_callback(stdout="", stderr=text)

            try:
                # Read both streams concurrently with timeout
                await asyncio.wait_for(
                    asyncio.gather(read_stdout(), read_stderr(), proc.wait()),
                    timeout=None if timeout_seconds == 0 else timeout_seconds,
                )
                stdout = "".join(stdout_lines)
                stderr = "".join(stderr_lines)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                stdout = "".join(stdout_lines)
                stderr = "".join(stderr_lines) + "\n[TIMEOUT]"
        else:
            # Non-streaming: wait for completion
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=None if timeout_seconds == 0 else timeout_seconds,
                )
                stdout = stdout_bytes.decode("utf-8")
                stderr = stderr_bytes.decode("utf-8")
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                result = RunScriptResult(
                    stdout="",
                    stderr="[TIMEOUT]",
                    exit_code=-1,
                    execution_strategy=execution_strategy,
                    elapsed_seconds=time.time() - start,
                )
                return result.model_dump()
    finally:
        if script_content:
            # Clean up temp script file
            if script_path_local.exists():
                try:
                    script_path_local.unlink()
                except Exception:
                    pass

    result = RunScriptResult(
        stdout=stdout,
        stderr=stderr,
        exit_code=proc.returncode or 0,
        execution_strategy=execution_strategy,
        elapsed_seconds=time.time() - start,
    )
    return result.model_dump()


@mcp.tool(tags=["execution", "dependencies"])
@smart_async(default_timeout=20.0)
async def py_run_script_with_dependencies(
    script_content: str | None = None,
    script_path: Path | None = None,
    python_version: str = "3.12",
    dependencies: list[str] | None = None,
    args: list[str] | None = None,
    timeout_seconds: int = 300,
    auto_parse_imports: bool = True,
    env_vars: dict[str, str] | None = None,
    env_file: Path | None = None,
    async_mode: bool = False,
    job_label: str | None = None,
    ignore_project_requirements: bool = True,
) -> RunWithDepsResult | dict[str, Any]:
    """
    Execute transient inline code or an existing script inside an ephemeral uv environment with explicit dependencies.

    Uses smart async: completes synchronously if under 20s, switches to background if longer.

    Parameters:
        script_content: Inline code (mutually exclusive with script_path).
        script_path: Existing script file path.
        python_version: Exact minor version (e.g. '3.12').
        dependencies: List[str] | None - explicit package specifiers (PEP 440). May be supplemented automatically.
        args: Optional CLI arguments.
        timeout_seconds: 0 disables timeout.
        auto_parse_imports: When True, parse script source for import statements and append missing top-level modules
                           to dependency list (best-effort heuristic; does not guarantee install success).
        env_vars: Optional dictionary of environment variables to set for the script.
        env_file: Optional path to .env file to load environment variables from.
        async_mode: If True, launch in background immediately (default: False).
        job_label: Optional label for job tracking.
        ignore_project_requirements: If True, use --isolated to ignore pyproject.toml (default: True).

    Returns:
        RunWithDepsResult if completed synchronously, or job metadata if switched to background.

    Errors:
        FileNotFoundError: script_path missing.
        ValueError: Exclusivity violated.

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
        inline_dir = Path.cwd() / "inline_scripts"
        inline_dir.mkdir(parents=True, exist_ok=True)
        spath = (inline_dir / f"inline_dep_{uuid.uuid4().hex}.py").resolve()
        source_text = script_content or ""
        spath.write_text(source_text)

    # Build initial dependency list
    resolved_dependencies = dependencies[:] if dependencies else []

    if auto_parse_imports:
        detected = _parse_imports(source_text)
        for pkg in detected:
            if pkg not in resolved_dependencies:
                resolved_dependencies.append(pkg)
        logger.info(
            "auto_parse_imports detected=%s final_deps=%s",
            sorted(detected),
            resolved_dependencies,
        )

    command: list[str] = ["uv", "run"]

    # Use --isolated to ignore pyproject.toml requirements when running standalone scripts
    if ignore_project_requirements:
        command.append("--isolated")

    # Ensure Python version is installed
    _ensure_python_version(python_version)

    command += ["--python", python_version]
    for dep in resolved_dependencies:
        command += ["--with", dep]
    command.append(str(spath))
    if args:
        command.extend(args)

    # Build environment
    proc_env = _build_process_env(env_vars=env_vars, env_file=env_file)

    start = time.time()
    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=proc_env,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=None if timeout_seconds == 0 else timeout_seconds,
            )
            stdout = stdout_bytes.decode("utf-8")
            stderr = stderr_bytes.decode("utf-8")
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            stdout = ""
            stderr = "[TIMEOUT]"
    finally:
        if script_content and spath.exists():
            try:
                spath.unlink()
            except Exception:
                pass

    result = RunWithDepsResult(
        stdout=stdout,
        stderr=stderr,
        exit_code=proc.returncode or 0,
        execution_strategy="uv-run",
        elapsed_seconds=time.time() - start,
        resolved_dependencies=resolved_dependencies,
        python_version_used=python_version,
    )
    return result.model_dump()


@mcp.tool(tags=["jobs", "introspection"])
def py_list_running_jobs() -> list[dict[str, str]]:
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
def py_get_job_output(job_id: str) -> dict[str, str]:
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
def py_kill_job(job_id: str) -> dict[str, str]:
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
        rec.finalized_elapsed
        if rec.finalized_elapsed is not None
        else (time.time() - rec.start_time),
    )
    return {
        "job_id": job_id,
        "status": status,
        "exit_code": str(rec.process.returncode),
    }


@mcp.tool(tags=["jobs", "maintenance"])
def py_cleanup_jobs(
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
        if (
            remove_inline
            and rec.is_inline_temp
            and rec.script_path
            and rec.script_path.exists()
        ):
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


@mcp.tool(tags=["benchmark", "performance"])
@smart_async(default_timeout=20.0)
async def py_benchmark_script(
    script_content: str | None = None,
    script_path: Path | None = None,
    python_version: str = "3.12",
    dependencies: list[str] | None = None,
    args: list[str] | None = None,
    timeout_seconds: int = 300,
    sample_interval: float = 0.05,
    env_vars: dict[str, str] | None = None,
    env_file: Path | None = None,
    async_mode: bool = False,
    job_label: str | None = None,
) -> BenchmarkResult | dict[str, Any]:
    """
    Execute code or script with dependency resolution (uv) while collecting basic benchmark metrics.

    Uses smart async: completes synchronously if under 20s, switches to background if longer.

    (py_benchmark_script) Metrics:
        - wall_time_seconds
        - peak_rss_mb
        - cpu_time_seconds (user+system)
        - exit_code

    Parameters mirror run_script_with_dependencies plus:
        sample_interval: Polling interval for memory usage sampling.
        env_vars: Optional dictionary of environment variables to set for the script.
        env_file: Optional path to .env file to load environment variables from.
        async_mode: If True, launch in background immediately (default: False).
        job_label: Optional label for job tracking.
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
        inline_dir = Path.cwd() / "inline_scripts"
        inline_dir.mkdir(parents=True, exist_ok=True)
        spath = (inline_dir / f"inline_bench_{uuid.uuid4().hex}.py").resolve()
        spath.write_text(script_content or "")
        is_inline = True
    # Ensure Python version is installed
    _ensure_python_version(python_version)

    command: list[str] = ["uv", "run", "--python", python_version]
    resolved_dependencies = dependencies[:] if dependencies else []
    for dep in resolved_dependencies:
        command += ["--with", dep]
    command.append(str(spath))
    if args:
        command.extend(args)

    # Build environment
    proc_env = _build_process_env(env_vars=env_vars, env_file=env_file)

    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=proc_env,
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
    cpu = (end_cpu.user - start_cpu.user) + (end_cpu.system - start_cpu.system)
    if is_inline and spath.exists():
        try:
            spath.unlink()
        except Exception:
            pass
    result = BenchmarkResult(
        stdout="".join(stdout_chunks),
        stderr="".join(stderr_chunks),
        exit_code=proc.returncode,
        execution_strategy="uv-run",
        elapsed_seconds=wall,
        resolved_dependencies=resolved_dependencies,
        python_version_used=python_version,
        wall_time_seconds=wall,
        cpu_time_seconds=cpu,
        peak_rss_mb=peak_rss / (1024 * 1024),
    )
    return result.model_dump()


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
    py_version = (
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    )
    logger.info("Starting MCP server (transport=stdio)")
    logger.info(
        "Diagnostics: cwd=%s executable=%s python_version=%s uv_present=%s",
        cwd,
        py_exec,
        py_version,
        shutil.which("uv") is not None,
    )
    # List key environment variables that might affect execution
    interesting_env = {
        k: v for k, v in os.environ.items() if k.startswith(("PYTHON", "UV", "FASTMCP"))
    }
    if interesting_env:
        logger.info("Environment (filtered): %s", interesting_env)
    else:
        logger.info("No filtered environment variables detected.")

    # Initialize smart async state
    initialize_state()
    logger.info("Smart async job tracking initialized")

    # Global exception hook: capture uncaught exceptions to a file for post-mortem
    def _write_exception(exc_type, exc_value, exc_tb):
        try:
            import traceback

            path = Path("/tmp/python_mcp_uncaught.log")
            with path.open("a", encoding="utf-8") as f:
                f.write("\n=== Uncaught exception ===\n")
                traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
        except Exception:
            # Best-effort only; avoid raising in excepthook
            pass

    sys.excepthook = _write_exception

    # Asyncio exception handler: capture loop exceptions
    def _asyncio_exc_handler(loop, context):
        try:
            path = Path("/tmp/python_mcp_async_exc.log")
            with path.open("a", encoding="utf-8") as f:
                f.write("\n=== Asyncio exception ===\n")
                f.write(str(context))
                f.write("\n")
        except Exception:
            pass

    try:
        loop = asyncio.get_event_loop()
        loop.set_exception_handler(_asyncio_exc_handler)
    except Exception:
        # If event loop not available at startup, ignore
        pass

    # Run the MCP server and capture top-level exceptions to a file as well
    try:
        mcp.run(transport="stdio")
    except Exception:
        try:
            import traceback

            path = Path("/tmp/python_mcp_run_exception.log")
            with path.open("a", encoding="utf-8") as f:
                f.write("\n=== MCP server top-level exception ===\n")
                traceback.print_exc(file=f)
        except Exception:
            pass
        # Re-raise after logging so any supervising process can act accordingly
        raise


@mcp.tool(tags=["scripts", "save"])
def py_save_script(
    script_name: str,
    source: str,
    dependencies: list[str] | None = None,
    requires_python: str | None = None,
    overwrite: bool = False,
) -> dict[str, str]:
    """
    Save a Python script under the 'scripts/' folder.

    Guidance:
    - Prefer saving useful snippets and reusable automation scripts here.
    - Include a top-level docstring explaining what the script does and how to use it.
    - Optionally include a uv-style TOML script header to declare dependencies and Python version.

    Header format example:
        # /// script
        # dependencies = ["requests<3", "rich"]
        # requires-python = ">=3.12"
        # ///
    """
    scripts_dir = Path.cwd() / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)

    # Prevent path traversal; ensure .py suffix.
    base = Path(script_name).name
    if not base.endswith(".py"):
        base += ".py"
    target = scripts_dir / base

    if target.exists() and not overwrite:
        return {
            "written": "false",
            "path": str(target),
            "message": "File exists; set overwrite=True to replace",
        }

    head_present = source.lstrip().startswith("# /// script")
    header_lines: list[str] = []
    if (dependencies or requires_python) and not head_present:
        header_lines.append("# /// script")
        if dependencies is not None:
            header_lines.append("# dependencies = [")
            for dep in dependencies:
                header_lines.append(f'#   "{dep}",')
            header_lines.append("# ]")
        if requires_python is not None:
            header_lines.append(f'# requires-python = "{requires_python}"')
        header_lines.append("# ///")
        header_lines.append("")

    final_text = ("\n".join(header_lines) + source) if header_lines else source
    target.write_text(final_text, encoding="utf-8")
    return {"written": "true", "path": str(target), "message": "Script saved"}


@mcp.tool(tags=["scripts", "run"])
@smart_async(default_timeout=20.0)
async def py_run_saved_script(
    script_name: str,
    args: list[str] | None = None,
    timeout_seconds: int = 300,
    env_vars: dict[str, str] | None = None,
    env_file: Path | None = None,
    async_mode: bool = False,
    job_label: str | None = None,
    auto_install_deps: bool = True,
) -> RunScriptResult | dict[str, Any]:
    """
    Run a saved script from the 'scripts/' folder via 'uv run --script', which respects the TOML script header.

    Uses smart async: completes synchronously if under 20s, switches to background if longer.

    Parameters:
        script_name: Name of the script in the scripts/ folder
        args: Optional command-line arguments
        timeout_seconds: Max wall time; 0 disables timeout (unbounded)
        env_vars: Optional dictionary of environment variables to set for the script
        env_file: Optional path to .env file to load environment variables from
        async_mode: If True, launch in background immediately (default: False).
        job_label: Optional label for job tracking.
        auto_install_deps: If True, auto-detect and install missing dependencies (default: True).
    """
    scripts_dir = Path.cwd() / "scripts"
    name = Path(script_name).name
    spath = scripts_dir / (name if name.endswith(".py") else f"{name}.py")
    if not spath.is_file():
        raise FileNotFoundError(f"Script not found: {spath}")

    # Auto-detect dependencies if enabled and no header exists
    detected_deps: list[str] = []
    if auto_install_deps:
        source_text = spath.read_text()
        # Check if script has TOML header
        if not source_text.startswith("# /// script"):
            detected_deps = _parse_imports(source_text)
            if detected_deps:
                logger.info(
                    f"Auto-detected dependencies for {script_name}: {detected_deps}"
                )

    command: list[str] = ["uv", "run"]

    # If auto-detected deps, use isolated mode to avoid pyproject.toml conflicts
    if detected_deps:
        command.append("--isolated")

    # Add auto-detected dependencies
    for dep in detected_deps:
        command += ["--with", dep]

    command += ["--script", str(spath)]
    if args:
        command.extend(args)

    # Build environment
    proc_env = _build_process_env(env_vars=env_vars, env_file=env_file)

    start = time.time()
    proc = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(scripts_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=proc_env,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=None if timeout_seconds == 0 else timeout_seconds,
        )
        stdout = stdout_bytes.decode("utf-8")
        stderr = stderr_bytes.decode("utf-8")
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        stdout = ""
        stderr = "[TIMEOUT]"
    except Exception as e:
        raise RuntimeError(f"Execution failed: {e}")  # noqa: TRY003
    result = RunScriptResult(
        stdout=stdout,
        stderr=stderr,
        exit_code=proc.returncode or 0,
        execution_strategy="uv-run",
        elapsed_seconds=time.time() - start,
    )
    return result.model_dump()


@mcp.tool(tags=["scripts", "introspection"])
def py_list_scripts() -> list[dict[str, str]]:
    """
    List scripts in 'scripts/' and show their name, path, top docstring preview, and script header metadata.
    """
    scripts_dir = Path.cwd() / "scripts"
    out: list[dict[str, str]] = []
    if not scripts_dir.is_dir():
        return out

    for p in sorted(scripts_dir.glob("*.py")):
        text = p.read_text(encoding="utf-8", errors="replace")

        # Extract top docstring preview (first triple-quoted block at file start).
        doc = ""
        t = text.lstrip()
        if t.startswith('"""') or t.startswith("'''"):
            q = t[:3]
            end = t.find(q, 3)
            if end != -1:
                doc = t[3:end].strip()

        # Extract uv script header metadata
        header_meta: dict[str, str] = {}
        lines = text.splitlines()
        start_idx: int | None = None
        end_idx: int | None = None
        for i, line in enumerate(lines):
            if line.startswith("# /// script"):
                start_idx = i
                break
        if start_idx is not None:
            for j in range(start_idx + 1, len(lines)):
                if lines[j].startswith("# ///"):
                    end_idx = j
                    break
            body_lines: list[str] = []
            for k in range(start_idx + 1, (end_idx or start_idx + 1)):
                line = lines[k]
                if line.startswith("#"):
                    body_lines.append(line.lstrip("# ").rstrip())
            toml_text = "\n".join(body_lines)
            if toml_text:
                try:
                    import tomllib

                    data = tomllib.loads(toml_text)
                    if "requires-python" in data:
                        header_meta["requires-python"] = str(
                            data.get("requires-python")
                        )
                    if "dependencies" in data:
                        deps = data.get("dependencies") or []
                        header_meta["dependencies"] = ", ".join(deps)
                except Exception:
                    header_meta["parse_error"] = "true"

        out.append(
            {
                "name": p.name,
                "path": str(p),
                "docstring": doc[:300],
                "header": "; ".join(f"{k}={v}" for k, v in header_meta.items())
                if header_meta
                else "",
            }
        )
    return out


# ---------------------------
# Smart Async Job Management Tools
# ---------------------------


@mcp.tool(tags=["jobs", "async"])
def py_job_status(job_id: str, incremental: bool = True) -> dict[str, Any]:
    """
    Get status and progress of a background job.

    Args:
        job_id: Job identifier returned from async execution
        incremental: If True, return only new output since last check (default: True, set False for full output)

    Returns:
        Job status including:
        - status: pending, running, completed, failed, cancelled
        - progress: {current, total, message} if available
        - result: Job result if completed
        - error: Error message if failed
    """
    return get_job_status(job_id, incremental=incremental)


@mcp.tool(tags=["jobs", "async"])
def py_list_jobs(status_filter: str | None = None, limit: int = 50) -> dict[str, Any]:
    """
    List background jobs with optional filtering.

    Args:
        status_filter: Optional status filter (pending, running, completed, failed, cancelled)
        limit: Maximum number of jobs to return (default: 50)

    Returns:
        List of jobs with metadata and total count
    """
    return list_jobs(status_filter=status_filter, limit=limit)


@mcp.tool(tags=["jobs", "async"])
def py_cancel_job(job_id: str) -> dict[str, Any]:
    """
    Cancel a running background job.

    Args:
        job_id: Job identifier to cancel

    Returns:
        Cancellation status
    """
    return cancel_job(job_id)


@mcp.tool(tags=["jobs", "async"])
def py_prune_jobs(
    keep_completed: bool = True,
    keep_failed: bool = True,
    max_age_hours: int = 24,
) -> dict[str, Any]:
    """
    Prune old jobs from the job registry.

    Args:
        keep_completed: If False, remove completed jobs (default: True)
        keep_failed: If False, remove failed jobs (default: True)
        max_age_hours: Remove jobs older than this many hours (default: 24)

    Returns:
        Number of jobs removed and remaining
    """
    return prune_jobs(
        keep_completed=keep_completed,
        keep_failed=keep_failed,
        max_age_hours=max_age_hours,
    )


if __name__ == "__main__":
    main()
