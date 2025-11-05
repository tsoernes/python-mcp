# Python Script Executor MCP Server

An MCP (Model Context Protocol) server for executing Python scripts and inline code with:
- uv-managed transient environments
- explicit dependency resolution
- synchronous or asynchronous (background) execution
- optional streaming of stdout/stderr
- benchmarking (wall time, CPU time, peak memory)
- job lifecycle introspection and control

This README documents the server’s tools, resource patterns, data models, usage examples, and operational guidelines.

---

## 1. Features at a Glance

| Capability | Sync | Async | Streaming | Dependencies | Benchmarking |
|------------|------|-------|-----------|--------------|--------------|
| Run existing script in directory | ✅ | ✅ (async variant) | ✅ (when stream=True) | ❌ | ❌ |
| Run inline script in directory | ✅ | ✅ | ✅ | ❌ | ❌ |
| Run code with dependencies | ✅ | ✅ | ✅ | ✅ | ✅ (benchmark_script) |
| Benchmark transient execution | ✅ | ❌ | ❌ | ✅ | ✅ |
| List running jobs | ✅ | N/A | N/A | N/A | N/A |
| Get job output (partial / final) | ✅ | N/A | Works with async jobs | N/A | N/A |
| Kill running job | ✅ | N/A | N/A | N/A | N/A |
| Stream snapshot via resource | ✅ | N/A | Works with async jobs | N/A | N/A |

---

## 2. Requirements

- Python >= 3.13
- uv (for environment resolution)
- fastmcp (server framework)
- psutil (benchmark metrics)
- pydantic (response models)

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

Returns (AsyncJobStart):
```
{
  "job_id": str,
  "status": "started",
  "execution_strategy": "uv-run" | "system-python"
}
```

#### run_script_with_dependencies_async (tags: execution, dependencies, async, stream)
Parameters similar to run_script_with_dependencies plus stream.

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

## 6. Usage Examples

### 6.1 Synchronous Run (Existing Script)

```
run_script_in_dir(
  directory="/work/project",
  script_path="main.py",
  use_uv=True,
  python_version="3.13",
  timeout_seconds=300
)
```

### 6.2 Synchronous Inline Code

```
run_script_in_dir(
  directory="/tmp",
  script_content="print('Hello world')",
  use_uv=False
)
```

### 6.3 Dependencies (Sync)

```
run_script_with_dependencies(
  script_content="import requests; print(requests.__version__)",
  python_version="3.13",
  dependencies=["requests"]
)
```

### 6.4 Benchmark

```
benchmark_script(
  script_content="print(sum(i*i for i in range(500000)))",
  python_version="3.13",
  dependencies=[]
)
```

### 6.5 Async + Streaming

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

### 6.6 Async With Dependencies

```
async_start = run_script_with_dependencies_async(
  script_content="import time; [print(i) or time.sleep(0.2) for i in range(5)]",
  python_version="3.13",
  dependencies=[],
  stream=True
)
```

### 6.7 Killing a Job

```
kill_job(job_id="abc123")
```

---

## 7. Error Handling

Common errors:
| Error | Cause | Recovery |
|-------|-------|----------|
| FileNotFoundError | directory or script_path missing | Check path resolution / permissions |
| ValueError | Both or neither of script_path & script_content provided | Provide exactly one |
| Timeout (sync tools) | Execution exceeded timeout_seconds | Retry with larger timeout or optimize script |
| OSError/Subprocess errors | Interpreter or uv not available | Ensure uv installed and in PATH |
| psutil.Error (benchmark) | Process ended before sampling or access denied | Safe to ignore; metrics partially available |

Timeout behavior:
- Process terminated.
- stderr suffixed with `[TIMEOUT]`.
- exit_code reflects termination code from OS.

---

## 8. Streaming Semantics

- stream=True activates periodic polling via an internal background task.
- job-stream resource returns full accumulated output each poll.
- For delta processing, track previous lengths externally.
- After process completion, snapshots stabilize.

Future enhancements (planned):
- Cursor-based streaming (return only new bytes).
- SSE/HTTP transport variant for push streaming.
- Log truncation thresholds to avoid huge payloads.

---

## 9. Performance Notes

- Cold uv runs with many dependencies can add latency due to resolution; consider caching future ephemeral environments.
- STREAM_POLL_INTERVAL currently 0.2s (balance between responsiveness and CPU overhead).
- psutil sampling interval configurable (sample_interval in benchmark_script).
- Inline scripts create transient files; sync mode cleans them immediately, async mode retains until finalization.

---

## 10. Security Considerations

Per project directives: No sandboxing. Executed code has:
- Full filesystem access under server user
- Network access
- Ability to spawn subprocesses

If you later require restriction:
- Add optional flags to disable network (iptables / firewalls / wrappers)
- Use resource limits (ulimit or cgroups)
- Add code length and argument validation

---

## 11. Extensibility Roadmap

Potential future tools:
- format_code (black / ruff)
- static_analysis (ruff + bandit)
- environment_cache_list / environment_create_persistent
- job_cleanup (remove finished jobs & temp files)
- stream_deltas (cursor-based)
- run_script_profile (cProfile stats)

---

## 12. Troubleshooting

| Symptom | Cause | Solution |
|---------|-------|----------|
| Editor times out starting server | Mixed paths / incorrect working directory | Ensure server launched in project root without conflicting --directory flags |
| Validation errors (types mismatch) | Using old schema / previous dict[str,str] design | Use updated Pydantic schema aware client |
| No output captured in streaming | stream=False in async start | Set stream=True or use sync variant |
| Benchmark returns zero peak_rss_mb | Process too short-lived for sampling | Increase workload or reduce sample_interval |

---

## 13. Design Principles Summary

- Mutual exclusivity of script_path/script_content reduces ambiguity.
- Synchronous and asynchronous tools separated for clearer schema contracts.
- Structured Pydantic models prevent implicit type coercion mistakes.
- Resource-centric streaming (job-stream) keeps data retrieval simple.
- Logging records lifecycle events (startup, async start, kill, finalize).

---

## 14. Example Combined Workflow

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

## 15. License / Attribution

Refer to project-level license (if added). FastMCP framework documentation: https://gofastmcp.com

---

## 16. Maintenance Checklist

Before merging changes:
- Verify docstrings match async/sync segmentation.
- Confirm Pydantic models reflect actual runtime payload.
- Test timeout edge case (timeout_seconds=1).
- Exercise benchmark_script with small and larger workloads.

---

## 17. FAQ

Q: Why separate async from sync tools?
A: The return schema differs fundamentally (job descriptor vs final output). Separation avoids overloaded outputs and schema ambiguity.

Q: Can I stream synchronous runs?
A: No—sync waits for completion. Use async variant + stream=True.

Q: How do I truncate giant stdout?
A: Currently not built in; implement a wrapper or add an enhancement to limit stored chunk size.

Q: Why exact minor Python versions only?
A: Simplifies resolution logic and avoids ambiguous interpreter selection; uv handles installation if missing.

---

## 18. Contributing

Proposed extension guidelines:
1. Add new Pydantic model for any new output type.
2. Provide full docstring with: Summary, Parameters, Returns, Errors, Examples.
3. Tag tools appropriately to aid discovery.
4. Maintain consistent naming (script_path/script_content).

---

Happy building. Use the structured tools for reliable, observable script execution workflows in MCP environments.