# Python Script Executor MCP Server

An MCP (Model Context Protocol) server for executing Python scripts and inline code with:
- uv-managed transient environments
- explicit dependency resolution
- environment variable support (dict or .env file)
- **smart async pattern** - automatic timeout switching to background
- synchronous or asynchronous (background) execution
- optional streaming of stdout/stderr
- job tracking with progress updates
- benchmarking (wall time, CPU time, peak memory)
- job lifecycle introspection and control

This README documents the server’s tools, resource patterns, data models, usage examples, and operational guidelines.

---

## 1. Features at a Glance

| Capability | Sync | Async | Smart Async | Streaming | Dependencies | Benchmarking | Env Vars | Progress |
|------------|------|-------|-------------|-----------|--------------|--------------|----------|----------|
| Run existing script in directory | ✅ | ✅ (async variant) | ❌ | ✅ (when stream=True) | ❌ | ❌ | ✅ | ❌ |
| Run inline script in directory | ✅ | ✅ | ❌ | ✅ | ❌ | ❌ | ✅ | ❌ |
| Run code with dependencies | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ (benchmark_script) | ✅ | ❌ |
| Benchmark transient execution | ✅ | ❌ | ❌ | ❌ | ✅ | ✅ | ✅ | ❌ |
| Smart async job management | N/A | N/A | ✅ | N/A | N/A | N/A | N/A | ✅ |
| List jobs with filtering | ✅ | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| Get job status & progress | ✅ | N/A | Works with smart async | N/A | N/A | N/A | N/A | ✅ |
| Cancel running job | ✅ | N/A | Works with smart async | N/A | N/A | N/A | N/A | N/A |
| Prune old jobs | ✅ | N/A | N/A | N/A | N/A | N/A | N/A | N/A |

---

## 2. Requirements

- Python >= 3.13
- uv (for environment resolution)
- fastmcp (server framework)
- psutil (benchmark metrics)
- pydantic (response models)
- python-dotenv (environment variable loading)

Install and sync environment:

```
uv sync
```

---

## 3. Running the Server

StdIO transport is explicitly selected (default for MCP console integrations):

```
uv run python-mcp-server
```

Or with the module:

```
uv run python -m python_mcp_server
```

If invoking from an editor (e.g., Zed) and needing a fixed working directory, create a launcher script:

```
#!/usr/bin/env sh
cd /home/torstein.sornes/code/python-mcp
exec uv run python-mcp-server
```

---

## 4. Tool Reference

### 4.1 Synchronous Execution

#### run_script_in_dir (tags: execution, sync)
Execute a Python script (existing file or inline content) inside a target directory using either uv or system Python.

Parameters:
- directory: Path (must exist)
- script_path: Path | None (absolute or relative to directory; mutually exclusive with script_content)
- script_content: str | None (inline source; mutually exclusive with script_path)
- args: list[str] | None
- use_uv: bool (True uses `uv run`)
- python_version: str | None (exact minor; honored only if use_uv=True)
- timeout_seconds: int (0 = unlimited)
- env_vars: dict[str, str] | None (environment variables to set)
- env_file: Path | None (path to .env file to load)

Returns (RunScriptResult):
```
{
  "stdout": str,
  "stderr": str,
  "exit_code": int,
  "execution_strategy": "uv-run" | "system-python",
  "elapsed_seconds": float
}
```

#### run_script_with_dependencies (tags: execution, dependencies, sync)
Execute inline code or an existing script in a transient uv environment with explicit dependencies.

Parameters:
- script_content OR script_path (mutually exclusive)
- python_version: str (exact minor)
- dependencies: list[str] | None (PEP 440 specifiers)
- args: list[str] | None
- timeout_seconds: int (0 = unlimited)
- env_vars: dict[str, str] | None (environment variables to set)
- env_file: Path | None (path to .env file to load)

Returns (RunWithDepsResult):
```
{
  "stdout": str,
  "stderr": str,
  "exit_code": int,
  "execution_strategy": "uv-run",
  "elapsed_seconds": float,
  "resolved_dependencies": list[str],
  "python_version_used": str
}
```

#### benchmark_script (tags: benchmark, performance, sync)
Execute code or script with dependency resolution while collecting metrics.

Parameters:
- script_content OR script_path
- python_version: str
- dependencies: list[str] | None
- args: list[str] | None
- timeout_seconds: int
- sample_interval: float (memory polling interval in seconds; default 0.05)
- env_vars: dict[str, str] | None (environment variables to set)
- env_file: Path | None (path to .env file to load)

Returns (BenchmarkResult):
```
{
  "stdout": str,
  "stderr": str,
  "exit_code": int,
  "execution_strategy": "uv-run",
  "elapsed_seconds": float,
  "resolved_dependencies": list[str],
  "python_version_used": str,
  "wall_time_seconds": float,
  "cpu_time_seconds": float,
  "peak_rss_mb": float
}
```

### 4.2 Asynchronous Execution

Async variants separate concerns and return a job descriptor immediately.

#### run_script_in_dir_async (tags: execution, async, stream)
Parameters similar to run_script_in_dir except:
- Omits timeout_seconds (no built-in timeout; external kill_job recommended if needed)
- stream: bool (enable periodic stdout/stderr harvesting)
- env_vars: dict[str, str] | None (environment variables to set)
- env_file: Path | None (path to .env file to load)

Returns (AsyncJobStart):
```
{
  "job_id": str,
  "status": "started",
  "execution_strategy": "uv-run" | "system-python"
}
```

#### run_script_with_dependencies_async (tags: execution, dependencies, async, stream)
Parameters similar to run_script_with_dependencies plus:
- stream: bool (enable periodic stdout/stderr harvesting)
- env_vars: dict[str, str] | None (environment variables to set)
- env_file: Path | None (path to .env file to load)

Returns (AsyncDepsJobStart):
```
{
  "job_id": str,
  "status": "started",
  "python_version_used": str,
  "resolved_dependencies": list[str]
}
```

### 4.3 Job Management and Introspection

#### list_running_jobs (tags: jobs, introspection)
Returns a list of job dictionaries:
```
[
  {
    "job_id": str,
    "running": "True" | "False",
    "exit_code": str | "None",
    "pid": str,
    "elapsed_seconds": str,
    "stream": "True" | "False",
    "stdout_chunks": str,
    "stderr_chunks": str
  },
  ...
]
```

#### get_job_output (tags: jobs, introspection)
Retrieve current or finalized job output. If job finished and not yet finalized, finalization occurs here.

Return payload:
```
{
  "status": "running" | "finished",
  "stdout": str,
  "stderr": str,
  "exit_code": str | "None",
  "elapsed_seconds": str
}
```

#### kill_job (tags: jobs, control)
Terminate a running process; finalizes output.

Returns:
```
{
  "job_id": str,
  "status": "killed",
  "exit_code": str
}
```

### 4.4 Streaming Resource

#### job-stream://{job_id}
Snapshot resource providing incremental combined output. Format:
```
---STDOUT---
<current stdout>
---STDERR---
<current stderr>
```
- Does not finalize the job.
- For delta processing, track previous lengths client-side.

---

## 5. Data Models

Pydantic models (simplified):
- RunScriptResult
- AsyncJobStart
- RunWithDepsResult
- AsyncDepsJobStart
- BenchmarkResult

These models allow clients to infer precise JSON schema (names, types, optionality) and reduce mismatches (e.g., integers vs strings).

---

## 6. Smart Async Pattern

The server implements the **smart async pattern** from the mcp-builder skill for intelligent background job execution with automatic timeout handling.

### 6.1 How It Works

**Automatic Timeout Switching:**
- Tools decorated with `@smart_async` attempt synchronous completion within a timeout budget
- If the operation exceeds the timeout, it automatically switches to background execution
- The underlying task is **shielded** to prevent cancellation - it continues running seamlessly
- Fast operations complete inline with no overhead
- Slow operations return immediately with a job_id for tracking

**Explicit Async Mode:**
- Use `async_mode=True` parameter to launch jobs in background immediately
- Useful for known long-running operations
- Skips timeout attempt and returns job_id instantly

**Progress Tracking:**
- Background jobs can report progress updates
- Progress stored in job metadata and persisted to disk
- Use `create_progress_callback()` in smart async tools

### 6.2 Job Management Tools

**Get Job Status:**
```python
py_job_status(job_id="abc-123")
# Returns: {"job": {"status": "running", "progress": {"current": 5, "total": 10}}}
```

**List Jobs:**
```python
py_list_jobs(status_filter="running", limit=50)
# Returns: {"jobs": [...], "total": 5}
```

**Cancel Job:**
```python
py_cancel_job(job_id="abc-123")
# Returns: {"job_id": "abc-123", "status": "cancelled"}
```

**Prune Old Jobs:**
```python
py_prune_jobs(keep_completed=False, max_age_hours=24)
# Returns: {"removed": 10, "remaining": 5}
```

### 6.3 Job Lifecycle

1. **Pending** - Job created but not started
2. **Running** - Job executing
3. **Completed** - Job finished successfully
4. **Failed** - Job raised an exception
5. **Cancelled** - Job was cancelled by user

### 6.4 Job Persistence

- Jobs are saved to `~/.python_mcp/meta/jobs.json`
- Survives server restarts
- Jobs running during restart marked as "failed" on next startup
- Progress updates automatically persisted

### 6.5 Example: Creating a Smart Async Tool

```python
from python_mcp_server.smart_async import smart_async, create_progress_callback

@smart_async(timeout_env="MY_TIMEOUT", default_timeout=30.0)
async def my_long_task(
    items: list[str],
    async_mode: bool = False,
    job_label: str | None = None
) -> dict:
    """Process items with automatic background switching."""
    progress = create_progress_callback()
    
    results = []
    for i, item in enumerate(items):
        result = await process_item(item)
        results.append(result)
        progress(i + 1, len(items), f"Processed {i + 1} items")
    
    return {"results": results, "total": len(results)}
```

**Usage:**
```python
# Fast - completes synchronously
result = await my_long_task(items=["a", "b", "c"])
# Returns: {"results": [...], "total": 3}

# Slow - switches to background automatically
result = await my_long_task(items=long_list)
# Returns: {"job_id": "...", "status": "running", "message": "..."}

# Explicit async - launches immediately
result = await my_long_task(items=items, async_mode=True)
# Returns: {"job_id": "...", "status": "pending"}
```

---

## 7. Environment Variables

All execution tools support setting environment variables via two methods:

### 6.1 Using env_vars Dictionary

Pass environment variables directly as a dictionary:

```python
run_script_in_dir(
  directory="/work/project",
  script_path="main.py",
  env_vars={
    "DATABASE_URL": "postgresql://localhost/db",
    "API_KEY": "secret123",
    "DEBUG": "true"
  }
)
```

### 6.2 Using .env File

Load environment variables from a .env file:

```python
run_script_with_dependencies(
  script_content="import os; print(os.getenv('MY_VAR'))",
  python_version="3.13",
  env_file="/path/to/.env"
)
```

**.env file format:**

Uses standard [python-dotenv](https://github.com/theskumar/python-dotenv) format:

```
# Comments are supported
DATABASE_URL=postgresql://localhost/testdb
API_KEY="secret123"
DEBUG=true
QUOTED='single quotes work too'
MULTILINE_VAR="line1
line2
line3"
```

Supports:
- Comments (`#`)
- Single and double quotes
- Multiline values
- Variable expansion
- Empty lines

### 6.3 Override Precedence

When both `env_file` and `env_vars` are provided, they are merged with this precedence (later overrides earlier):
1. Current process environment (inherited from MCP server)
2. Variables from `env_file`
3. Variables from `env_vars` dict (highest priority)

Example:
```python
# .env contains: MY_VAR=from_file
run_script_in_dir(
  directory="/work",
  script_path="test.py",
  env_file="/work/.env",
  env_vars={"MY_VAR": "from_dict"}  # This overrides the .env value
)
# Result: MY_VAR=from_dict
```

---

## 8. Usage Examples

### 8.1 Synchronous Run (Existing Script)

```
run_script_in_dir(
  directory="/work/project",
  script_path="main.py",
  use_uv=True,
  python_version="3.13",
  timeout_seconds=300
)
```

### 8.2 Synchronous Inline Code

```
run_script_in_dir(
  directory="/tmp",
  script_content="print('Hello world')",
  use_uv=False
)
```

### 8.3 Dependencies (Sync)

```
run_script_with_dependencies(
  script_content="import requests; print(requests.__version__)",
  python_version="3.13",
  dependencies=["requests"]
)
```

### 8.4 Benchmark

```
benchmark_script(
  script_content="print(sum(i*i for i in range(500000)))",
  python_version="3.13",
  dependencies=[]
)
```

### 8.5 Async + Streaming

```
start = run_script_in_dir_async(
  directory="/opt/jobs",
  script_path="long_task.py",
  use_uv=True,
  python_version="3.13",
  stream=True
)

job_id = start.job_id

# Poll for partial output
snapshot = get_resource("job-stream://{job_id}")

# Final consolidated output
final = get_job_output(job_id)
```

### 8.6 Async With Dependencies

```
async_start = run_script_with_dependencies_async(
  script_content="import time; [print(i) or time.sleep(0.2) for i in range(5)]",
  python_version="3.13",
  dependencies=[],
  stream=True
)
```

### 8.7 With Environment Variables

```
run_script_with_dependencies(
  script_content="""
import os
print(f"DB: {os.getenv('DATABASE_URL')}")
print(f"Key: {os.getenv('API_KEY')}")
""",
  python_version="3.13",
  dependencies=[],
  env_vars={
    "DATABASE_URL": "postgresql://localhost/mydb",
    "API_KEY": "sk-test-123"
  }
)
```

### 8.8 Smart Async Job Tracking

```
# Tool returns job_id when it switches to background
result = await some_long_operation()

if "job_id" in result:
    # Poll for status
    status = py_job_status(job_id=result["job_id"])
    print(status["job"]["status"])  # "running"
    print(status["job"]["progress"])  # {"current": 50, "total": 100}
    
    # Wait for completion
    while status["job"]["status"] == "running":
        await asyncio.sleep(1)
        status = py_job_status(job_id=result["job_id"])
    
    # Get final result
    print(status["job"]["result"])
```

### 8.9 Killing a Job

```
kill_job(job_id="abc123")
```

---

## 9. Error Handling

Common errors:
| Error | Cause | Recovery |
|-------|-------|----------|
| FileNotFoundError | directory, script_path, or env_file missing | Check path resolution / permissions |
| ValueError | Both or neither of script_path & script_content provided | Provide exactly one |
| Timeout (sync tools) | Execution exceeded timeout_seconds | Retry with larger timeout or optimize script |
| OSError/Subprocess errors | Interpreter or uv not available | Ensure uv installed and in PATH |
| psutil.Error (benchmark) | Process ended before sampling or access denied | Safe to ignore; metrics partially available |

Timeout behavior:
- Process terminated.
- stderr suffixed with `[TIMEOUT]`.
- exit_code reflects termination code from OS.

---

## 10. Streaming Semantics

- stream=True activates periodic polling via an internal background task.
- job-stream resource returns full accumulated output each poll.
- For delta processing, track previous lengths externally.
- After process completion, snapshots stabilize.

Future enhancements (planned):
- Cursor-based streaming (return only new bytes).
- SSE/HTTP transport variant for push streaming.
- Log truncation thresholds to avoid huge payloads.

---

## 11. Performance Notes

- Cold uv runs with many dependencies can add latency due to resolution; consider caching future ephemeral environments.
- STREAM_POLL_INTERVAL currently 0.2s (balance between responsiveness and CPU overhead).
- psutil sampling interval configurable (sample_interval in benchmark_script).
- Inline scripts create transient files; sync mode cleans them immediately, async mode retains until finalization.

---

## 12. Security Considerations

Per project directives: No sandboxing. Executed code has:
- Full filesystem access under server user
- Network access
- Ability to spawn subprocesses

If you later require restriction:
- Add optional flags to disable network (iptables / firewalls / wrappers)
- Use resource limits (ulimit or cgroups)
- Add code length and argument validation

**Environment Variables:**
- Environment variables are passed directly to subprocesses
- .env files are parsed client-side (not secure for secrets in shared filesystems)
- Consider using secret management tools for sensitive credentials

---

## 13. Extensibility Roadmap

Potential future tools:
- format_code (black / ruff)
- static_analysis (ruff + bandit)
- environment_cache_list / environment_create_persistent
- job_cleanup (remove finished jobs & temp files)
- stream_deltas (cursor-based)
- run_script_profile (cProfile stats)

---

## 14. Troubleshooting

| Symptom | Cause | Solution |
|---------|-------|----------|
| Editor times out starting server | Mixed paths / incorrect working directory | Ensure server launched in project root without conflicting --directory flags |
| Validation errors (types mismatch) | Using old schema / previous dict[str,str] design | Use updated Pydantic schema aware client |
| No output captured in streaming | stream=False in async start | Set stream=True or use sync variant |
| Benchmark returns zero peak_rss_mb | Process too short-lived for sampling | Increase workload or reduce sample_interval |

| Environment variable not visible in script | Typo in key name or .env not loaded | Check env_vars dict keys match usage; verify env_file path |

---

## 15. Design Principles Summary

- Mutual exclusivity of script_path/script_content reduces ambiguity.
- Synchronous and asynchronous tools separated for clearer schema contracts.
- Structured Pydantic models prevent implicit type coercion mistakes.
- Resource-centric streaming (job-stream) keeps data retrieval simple.
- Logging records lifecycle events (startup, async start, kill, finalize).

- Smart async pattern for optimal performance (fast tasks inline, slow tasks background)

---

## 16. Example Combined Workflow

```
# 1. Fire off a dependency-heavy async script
start = run_script_with_dependencies_async(
  script_content="import numpy as np; import time; [print(np.sqrt(i)) or time.sleep(0.3) for i in range(10)]",
  python_version="3.13",
  dependencies=["numpy"],
  stream=True
)

# 2. Periodically poll streaming output
for _ in range(5):
    chunk = get_resource(f"job-stream://{start.job_id}")
    print(chunk)
    status = get_job_output(start.job_id)
    if status["status"] == "finished":
        break
    time.sleep(1.0)

# 3. Final result
final = get_job_output(start.job_id)
print(final["stdout"])
```

---

## 17. License / Attribution

Refer to project-level license (if added). FastMCP framework documentation: https://gofastmcp.com

---

## 18. Maintenance Checklist

Before merging changes:
- Verify docstrings match async/sync segmentation.
- Confirm Pydantic models reflect actual runtime payload.
- Test timeout edge case (timeout_seconds=1).
- Exercise benchmark_script with small and larger workloads.

---

## 19. FAQ

Q: Why separate async from sync tools?
A: The return schema differs fundamentally (job descriptor vs final output). Separation avoids overloaded outputs and schema ambiguity.

Q: Can I stream synchronous runs?
A: No—sync waits for completion. Use async variant + stream=True.

Q: How do I truncate giant stdout?
A: Currently not built in; implement a wrapper or add an enhancement to limit stored chunk size.

Q: Why exact minor Python versions only?
A: Simplifies resolution logic and avoids ambiguous interpreter selection; uv handles installation if missing.

Q: Can I use both env_vars and env_file?
A: Yes—they merge with env_vars taking precedence over env_file values.

Q: Are environment variables visible to spawned subprocesses?
A: Yes—they are passed to the subprocess environment and inherited by any child processes.

Q: What is the smart async pattern?
A: A decorator-based approach where tools attempt synchronous completion within a timeout, then automatically switch to background execution if exceeded. Tasks are shielded to prevent cancellation.

Q: How do I use smart async in my tools?
A: Decorate your async function with `@smart_async()`, add `async_mode` and `job_label` parameters, and use `create_progress_callback()` to report progress.

Q: Can I track progress of background jobs?
A: Yes - use `create_progress_callback()` in your tool and call `py_job_status()` to check progress.

---

## 20. Contributing

Proposed extension guidelines:
1. Add new Pydantic model for any new output type.
2. Provide full docstring with: Summary, Parameters, Returns, Errors, Examples.
3. Tag tools appropriately to aid discovery.
4. Maintain consistent naming (script_path/script_content).

---

Happy building. Use the structured tools for reliable, observable script execution workflows in MCP environments.