# Smart Async Migration Guide

## Overview

The python-mcp server has been refactored to use the `@smart_async` decorator pattern across all long-running tools, replacing the previous dual sync/async architecture with a unified intelligent async approach.

## What Changed

### Before: Dual Sync/Async Architecture

Previously, the server had **separate** sync and async versions of tools:

```python
# Synchronous version (blocking)
@mcp.tool(tags=["execution", "sync"])
def py_run_script_in_dir(...) -> RunScriptResult:
    # Blocking subprocess.Popen
    proc = subprocess.Popen(...)
    stdout, stderr = proc.communicate(timeout=timeout_seconds)
    return RunScriptResult(...)

# Separate async version (manual job management)
@mcp.tool(tags=["execution", "async"])
def py_run_script_in_dir_async(...) -> AsyncJobStart:
    # Still blocking subprocess.Popen, just launched in background
    proc = subprocess.Popen(...)
    job_id = uuid.uuid4().hex
    JOBS[job_id] = JobRecord(...)
    return AsyncJobStart(job_id=job_id, status="started")
```

**Problems with this approach:**
- Code duplication (two implementations per tool)
- User confusion (which version to use?)
- No intelligent timeout handling
- Async versions still used blocking I/O

### After: Unified Smart Async Architecture

Now, there is **one** version of each tool using `@smart_async`:

```python
@mcp.tool(tags=["execution"])
@smart_async(default_timeout=20.0)
async def py_run_script_in_dir(
    ...,
    async_mode: bool = False,
    job_label: str | None = None
) -> RunScriptResult | dict[str, Any]:
    # True async with asyncio.create_subprocess_exec
    proc = await asyncio.create_subprocess_exec(...)
    stdout_bytes, stderr_bytes = await proc.communicate()
    return RunScriptResult(...)
```

**Benefits:**
- Single implementation per tool (DRY principle)
- Intelligent timeout handling (automatic background switching)
- True async I/O using `asyncio.create_subprocess_exec`
- Explicit control via `async_mode` parameter
- Job tracking built-in via decorator

## Breaking Changes

### Removed Tools

The following `_async` suffixed tools have been **removed**:

- ❌ `py_run_script_in_dir_async` → Use `py_run_script_in_dir` with `async_mode=True`
- ❌ `py_run_script_with_dependencies_async` → Use `py_run_script_with_dependencies` with `async_mode=True`

### Modified Tools

The following tools are now `async def` with `@smart_async`:

- ✅ `py_run_script_in_dir` - Now async with smart timeout
- ✅ `py_run_script_with_dependencies` - Now async with smart timeout
- ✅ `py_benchmark_script` - Now async with smart timeout
- ✅ `py_run_saved_script` - Now async with smart timeout

### New Parameters

All smart async tools now accept:

- `async_mode: bool = False` - Explicitly launch in background
- `job_label: str | None = None` - Custom label for job tracking

## Migration Examples

### Example 1: Quick Script Execution

**Before:**
```python
# User wanted quick execution
result = await py_run_script_in_dir(
    directory=Path("."),
    script_content="print('hello')"
)
# Returned: RunScriptResult
```

**After:**
```python
# Same usage, same behavior
result = await py_run_script_in_dir(
    directory=Path("."),
    script_content="print('hello')"
)
# Still returns: RunScriptResult (if completes in <20s)
```

**No change needed!** Fast operations still complete synchronously.

### Example 2: Long-Running Script (Old Async Version)

**Before:**
```python
# User wanted background execution
result = await py_run_script_in_dir_async(
    directory=Path("."),
    script_content="import time; time.sleep(60); print('done')",
    stream=True
)
# Returned: AsyncJobStart(job_id="...", status="started")

# Then poll for status
status = await py_get_job_output(job_id=result.job_id)
```

**After:**
```python
# Option 1: Automatic background switch (if takes >20s)
result = await py_run_script_in_dir(
    directory=Path("."),
    script_content="import time; time.sleep(60); print('done')"
)
# After 20s: Returns job metadata {"job_id": "...", "status": "running"}

# Option 2: Explicit background launch
result = await py_run_script_in_dir(
    directory=Path("."),
    script_content="import time; time.sleep(60); print('done')",
    async_mode=True  # NEW: Explicit background
)
# Returns immediately: {"job_id": "...", "status": "pending"}

# Then check status using smart_async job tools
status = await py_job_status(job_id=result["job_id"])
```

**Migration:** Replace `_async` calls with `async_mode=True`.

### Example 3: Streaming Not Supported

**Before:**
```python
# Old async version supported streaming
result = await py_run_script_in_dir_async(
    directory=Path("."),
    script_content="...",
    stream=True  # Enabled incremental output
)
```

**After:**
```python
# Streaming parameter removed
# Job output is captured fully, but can be queried progressively
result = await py_run_script_in_dir(
    directory=Path("."),
    script_content="...",
    async_mode=True
)

# Poll for status to check progress
status = await py_job_status(job_id=result["job_id"])
# Returns: {"job": {"status": "running", ...}}
```

**Migration:** Remove `stream=True` parameter. Job tracking is automatic.

## Behavior Changes

### Timeout Handling

**Before:**
```python
# Sync version had subprocess timeout
result = await py_run_script_in_dir(
    directory=Path("."),
    script_content="...",
    timeout_seconds=30  # Killed after 30s
)
# If timeout: stderr ends with "[TIMEOUT]"
```

**After:**
```python
# timeout_seconds is subprocess timeout (inner)
# @smart_async has 20s smart timeout (outer)
result = await py_run_script_in_dir(
    directory=Path("."),
    script_content="...",
    timeout_seconds=300  # Subprocess timeout
)
# If takes >20s: Switches to background (subprocess continues)
# If subprocess times out: Same behavior, stderr="[TIMEOUT]"
```

**Two-tier timeout system:**
1. **Smart async timeout (20s)**: When to switch to background
2. **Subprocess timeout (300s)**: When to kill the subprocess

### Return Type Variance

**Before:**
```python
# Sync version always returned RunScriptResult
result: RunScriptResult = await py_run_script_in_dir(...)

# Async version always returned AsyncJobStart
result: AsyncJobStart = await py_run_script_in_dir_async(...)
```

**After:**
```python
# New version returns EITHER type
result: RunScriptResult | dict[str, Any] = await py_run_script_in_dir(...)

if isinstance(result, RunScriptResult):
    # Completed synchronously
    print(result.stdout)
elif "job_id" in result:
    # Switched to background
    job_id = result["job_id"]
    status = await py_job_status(job_id=job_id)
```

**Migration:** Handle both return types.

## Job Management Changes

### Old Job Tools (REMOVED)

The following tools used the old `JobRecord` system:

- ❌ `py_list_running_jobs` - Used old JOBS dict
- ❌ `py_get_job_output` - Used old JobRecord
- ❌ `py_kill_job` - Used old JobRecord
- ❌ `py_cleanup_jobs` - Used old JobRecord

### New Job Tools (SMART ASYNC)

New tools use the `smart_async` job tracking system:

- ✅ `py_job_status(job_id)` - Get job status with progress
- ✅ `py_list_jobs(status_filter, limit)` - List jobs with filtering
- ✅ `py_cancel_job(job_id)` - Cancel a running job
- ✅ `py_prune_jobs(keep_completed, keep_failed, max_age_hours)` - Clean up old jobs

### Job Status Differences

**Before (Old System):**
```python
status = await py_get_job_output(job_id="...")
# {
#   "status": "running" | "finished",
#   "stdout": "...",
#   "stderr": "...",
#   "exit_code": 0,
#   "elapsed_seconds": 45.2
# }
```

**After (Smart Async):**
```python
status = await py_job_status(job_id="...")
# {
#   "job": {
#     "id": "...",
#     "label": "py_run_script_in_dir",
#     "status": "pending" | "running" | "completed" | "failed" | "cancelled",
#     "created_at": "2025-12-17T14:30:00",
#     "started_at": "2025-12-17T14:30:01",
#     "completed_at": "2025-12-17T14:31:15",
#     "result": {...},  # RunScriptResult when completed
#     "error": null,
#     "progress": {"current": 50, "total": 100, "message": "Processing..."}
#   }
# }
```

**Migration:** Update job status parsing to use nested `job` object.

## Configuration Changes

### Environment Variables

**New:**
```bash
# Set smart async timeout globally (default: 20s)
export SMART_ASYNC_TIMEOUT_SECONDS=30

# Job persistence location (default: ~/.python_mcp/meta/jobs.json)
# Configured via initialize_state() in code
```

## Testing Migration

### Update Test Scripts

**Before:**
```python
# Test async execution
result = await py_run_script_in_dir_async(
    directory=test_dir,
    script_content="print('test')"
)
assert result.status == "started"
assert result.job_id

# Check output
output = await py_get_job_output(job_id=result.job_id)
while output["status"] == "running":
    await asyncio.sleep(0.5)
    output = await py_get_job_output(job_id=result.job_id)
assert output["exit_code"] == 0
```

**After:**
```python
# Test with explicit async
result = await py_run_script_in_dir(
    directory=test_dir,
    script_content="print('test')",
    async_mode=True
)
assert "job_id" in result
assert result["status"] in ("pending", "running")

# Check status
status = await py_job_status(job_id=result["job_id"])
while status["job"]["status"] == "running":
    await asyncio.sleep(0.5)
    status = await py_job_status(job_id=result["job_id"])
assert status["job"]["status"] == "completed"
assert status["job"]["result"]["exit_code"] == 0
```

### Test Fast Operations

```python
# Fast operations should complete synchronously
result = await py_run_script_in_dir(
    directory=test_dir,
    script_content="print('fast')"  # <20s
)
assert isinstance(result, RunScriptResult)  # Direct result
assert result.exit_code == 0
```

## Rollback Plan

If issues arise, you can:

1. **Revert to previous commit** before smart_async migration
2. **Use git bisect** to identify problematic changes
3. **Check logs** at `~/.python_mcp/meta/jobs.json` and `src/python_mcp_server/python_mcp_server.log`

## FAQ

### Q: Why remove the _async versions?

A: Code duplication and user confusion. The `@smart_async` decorator provides the same functionality with better ergonomics.

### Q: What if I need streaming output?

A: The smart_async system captures full output. For true streaming, you can add progress callbacks (see `SMART_ASYNC_USAGE.md`).

### Q: How do I force background execution?

A: Use `async_mode=True`:
```python
result = await py_run_script_in_dir(..., async_mode=True)
```

### Q: Can I change the 20s timeout?

A: Yes, set `SMART_ASYNC_TIMEOUT_SECONDS` environment variable:
```bash
export SMART_ASYNC_TIMEOUT_SECONDS=60
```

### Q: What about the benchmark tool?

A: `py_benchmark_script` uses `@smart_async` but still uses blocking subprocess internally for memory sampling. This will be improved in a future update.

### Q: Where are jobs persisted?

A: Jobs are saved to `~/.python_mcp/meta/jobs.json` automatically.

### Q: How do I clean up old jobs?

A: Use `py_prune_jobs`:
```python
await py_prune_jobs(keep_completed=False, max_age_hours=24)
```

## Additional Resources

- **SMART_ASYNC_USAGE.md** - Complete usage guide
- **SMART_ASYNC_SUMMARY.md** - Architecture details
- **SMART_ASYNC_CONSIDERATION.md** - Design decisions
- **mcp-builder skill** - Original pattern source

## Support

For issues:
1. Check server logs: `src/python_mcp_server/python_mcp_server.log`
2. Check job state: `~/.python_mcp/meta/jobs.json`
3. Enable debug: `export MCP_DEBUG=true`
