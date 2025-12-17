# Smart Async Usage Guide

## Overview

The python-mcp server includes production-tested smart async infrastructure based on the [mcp-builder skill](https://github.com/clinebot/mcp-builder) pattern. This enables MCP tools to intelligently handle long-running operations with automatic timeout-based background switching.

## Key Features

1. **Synchronous Completion** - Tasks completing within timeout return directly (no overhead)
2. **Automatic Background Switch** - Tasks exceeding timeout seamlessly move to background
3. **Shielded Execution** - Uses `asyncio.shield()` to prevent task cancellation
4. **Explicit Async Mode** - `async_mode=True` parameter for deterministic background launch
5. **Job Tracking** - Full job lifecycle tracking with persistence to disk
6. **Progress Tracking** - Real-time progress updates for long-running operations
7. **Cross-Process Job Sync** - Jobs created in subprocesses are visible in main server

## Configuration

### Default Timeout

The default timeout is **20 seconds**. This can be overridden:

```bash
# Set global timeout via environment variable
export SMART_ASYNC_TIMEOUT_SECONDS=30

# Or per-tool timeout
export MY_TOOL_TIMEOUT=60
```

### Persistence Directory

Jobs are persisted to disk at:
- Default: `~/.python_mcp/meta/jobs.json`
- Custom: Set via `initialize_state(persistence_dir=Path("/custom/path"))`

## Current Architecture

### Existing Async Tools

The server provides separate sync and async versions of tools:

**Synchronous Tools** (blocking, with timeout):
- `py_run_script_in_dir` - Execute script in directory (sync)
- `py_run_script_with_dependencies` - Execute with dependencies (sync)
- `py_benchmark_script` - Benchmark script execution (sync)
- `py_run_saved_script` - Run saved script (sync)

**Asynchronous Tools** (non-blocking, manual job management):
- `py_run_script_in_dir_async` - Execute script in directory (async with streaming)
- `py_run_script_with_dependencies_async` - Execute with dependencies (async with streaming)

**Job Management Tools** (quick operations):
- `py_job_status` - Get job status and progress
- `py_list_jobs` - List all jobs with filtering
- `py_cancel_job` - Cancel a running job
- `py_prune_jobs` - Clean up old jobs

### Why Separate Sync/Async Tools?

The current architecture keeps sync and async tools separate because:
1. **Blocking I/O** - Sync tools use `subprocess.Popen` (blocking)
2. **Simpler Use Cases** - Many scripts complete quickly (<20s) and don't need async overhead
3. **Explicit Choice** - Users can choose sync (simple) or async (streaming) based on needs

## Using Smart Async in New Tools

To create a new async tool with smart async support:

```python
from python_mcp_server.smart_async import smart_async

@mcp.tool()
@smart_async(timeout_env="MY_TOOL_TIMEOUT", default_timeout=20.0)
async def my_async_tool(
    param: str,
    async_mode: bool = False,
    job_label: str | None = None
) -> dict[str, Any]:
    """
    Example async tool with smart timeout handling.
    
    Args:
        param: Your tool parameter
        async_mode: If True, launch in background immediately
        job_label: Optional label for job tracking
    
    Returns:
        Result dict (or job metadata if async)
    """
    # Your async implementation using await
    result = await do_async_work(param)
    return {"result": result}
```

### Execution Modes

#### 1. Synchronous (Default)
```python
# Fast task - completes within 20s
result = await my_async_tool(param="data")
# Returns: {"result": ...}
```

#### 2. Automatic Background (on timeout)
```python
# Slow task - takes > 20s
result = await my_async_tool(param="large_dataset")
# After 20s: {"job_id": "...", "status": "running", "message": "..."}
```

#### 3. Explicit Async
```python
# Launch immediately in background
result = await my_async_tool(param="data", async_mode=True)
# Returns immediately: {"job_id": "...", "status": "pending"}
```

### Checking Job Status

```python
# Get job status
status = await py_job_status(job_id=result["job_id"])
# {
#   "job": {
#     "id": "...",
#     "status": "running",
#     "progress": {"current": 25, "total": 100, "message": "Processing..."},
#     ...
#   }
# }
```

## Progress Tracking

Add progress updates to long-running operations:

```python
from python_mcp_server.smart_async import smart_async, create_progress_callback

@mcp.tool()
@smart_async(default_timeout=20.0)
async def process_items(
    items: list[str],
    async_mode: bool = False,
    job_label: str | None = None
) -> dict[str, Any]:
    """Process items with progress tracking."""
    
    # Create progress callback (automatically uses job context)
    progress_callback = create_progress_callback()
    
    results = []
    total = len(items)
    
    # Initial progress
    if progress_callback:
        progress_callback(0, total, "Starting processing...")
    
    for i, item in enumerate(items):
        result = await process_single_item(item)
        results.append(result)
        
        # Update progress
        if progress_callback:
            progress_callback(i + 1, total, f"Processed {i + 1}/{total} items")
    
    return {"results": results, "total": len(results)}
```

## Job Management

### List Jobs

```python
# List all jobs
all_jobs = await py_list_jobs()

# Filter by status
running_jobs = await py_list_jobs(status_filter="running")
completed_jobs = await py_list_jobs(status_filter="completed")

# Limit results
recent_jobs = await py_list_jobs(limit=10)
```

### Cancel Job

```python
cancel_result = await py_cancel_job(job_id="abc-123")
# {"job_id": "abc-123", "status": "cancelled", "message": "Job cancelled"}
```

### Prune Old Jobs

```python
# Remove completed jobs older than 24 hours
prune_result = await py_prune_jobs(
    keep_completed=False,
    keep_failed=True,
    max_age_hours=24
)
# {"removed": 5, "remaining": 3}
```

## Error Handling

Jobs capture errors and persist them:

```python
status = await py_job_status(job_id="failed-job-id")
# {
#   "job": {
#     "status": "failed",
#     "error": "Division by zero",
#     "completed_at": "2025-12-17T14:30:00"
#   }
# }
```

## Best Practices

### 1. Choose the Right Tool

- **Use sync tools** for quick operations (<20s expected)
- **Use async tools** for long-running operations or when streaming is needed
- **Use explicit async_mode=True** when you know the operation will be long

### 2. Set Appropriate Timeouts

```python
# For tools that usually take 5-10s
@smart_async(default_timeout=15.0)

# For tools that might take several minutes
@smart_async(default_timeout=60.0)

# Or use environment variables for flexibility
@smart_async(timeout_env="MY_TOOL_TIMEOUT", default_timeout=30.0)
```

### 3. Add Progress Tracking

For operations that process multiple items:

```python
progress_callback = create_progress_callback()
for i, item in enumerate(items):
    await process(item)
    progress_callback(i + 1, len(items), f"Step {i + 1}")
```

### 4. Clean Up Old Jobs

Periodically prune completed jobs:

```python
# In a maintenance task or cron job
await py_prune_jobs(keep_completed=False, max_age_hours=24)
```

### 5. Handle Job Not Found

```python
status = await py_job_status(job_id=job_id)
if "error" in status:
    # Job not found or error
    print(f"Error: {status['error']}")
else:
    # Job found
    job = status["job"]
    print(f"Status: {job['status']}")
```

## Response Examples

### Synchronous Completion
```json
{
  "result": {
    "processed": 100,
    "status": "success"
  }
}
```

### Background Job (Timeout)
```json
{
  "job_id": "9c0af4c2-2a74-430e-bc1d-0f419b6bd503",
  "status": "running",
  "message": "Task exceeded 20.0s time budget; running in background"
}
```

### Job Status with Progress
```json
{
  "job": {
    "id": "9c0af4c2-2a74-430e-bc1d-0f419b6bd503",
    "label": "process_items",
    "status": "running",
    "created_at": "2025-12-17T14:25:00",
    "started_at": "2025-12-17T14:25:01",
    "progress": {
      "current": 52,
      "total": 100,
      "message": "Processed 52/100 items"
    }
  }
}
```

### Completed Job
```json
{
  "job": {
    "id": "9c0af4c2-2a74-430e-bc1d-0f419b6bd503",
    "status": "completed",
    "completed_at": "2025-12-17T14:30:15",
    "result": {
      "processed": 100,
      "status": "success"
    }
  }
}
```

## Implementation Notes

### Shielded Tasks

The decorator uses `asyncio.shield()` to prevent task cancellation when switching to background mode. This ensures work continues even after the timeout.

```python
# Inside smart_async decorator
shielded = asyncio.shield(task)
try:
    return await asyncio.wait_for(shielded, timeout=timeout_seconds)
except asyncio.TimeoutError:
    # Task continues running in background
    # Wrap in job for tracking
```

### Job Persistence

Jobs are automatically saved to disk on status changes:
- Job created → saved
- Job started → saved
- Progress updated → saved
- Job completed/failed → saved

This ensures job state survives across API calls and enables cross-process visibility.

### Context Variables

Progress tracking uses `contextvars.ContextVar` to track the current job_id:

```python
current_job_id.set(job_id)  # Set in job runner
job_id = current_job_id.get()  # Get in progress callback
```

This allows progress updates without explicitly passing job_id through every function.

## Migrating Sync Tools to Async

If you need to convert a blocking sync tool to async with smart_async:

1. **Change to async def**:
   ```python
   async def my_tool(...):  # was: def my_tool(...)
   ```

2. **Convert blocking I/O to async**:
   ```python
   # Before (blocking)
   proc = subprocess.Popen(...)
   stdout, stderr = proc.communicate()
   
   # After (async)
   proc = await asyncio.create_subprocess_exec(
       *command,
       stdout=asyncio.subprocess.PIPE,
       stderr=asyncio.subprocess.PIPE
   )
   stdout, stderr = await proc.communicate()
   ```

3. **Add decorator and control parameters**:
   ```python
   @smart_async(default_timeout=20.0)
   async def my_tool(
       param: str,
       async_mode: bool = False,
       job_label: str | None = None
   ):
   ```

4. **Add progress tracking** (optional):
   ```python
   progress_callback = create_progress_callback()
   for i, item in enumerate(items):
       await process(item)
       progress_callback(i + 1, len(items))
   ```

## Related Resources

- **mcp-builder skill**: Production-tested smart async patterns
- **smart_async.py**: Implementation details and job tracking
- **SMART_ASYNC_SUMMARY.md**: Architecture and design decisions
- **SMART_ASYNC_CONSIDERATION.md**: Performance considerations

## Support

For issues or questions:
1. Check job logs: `~/.python_mcp/meta/jobs.json`
2. Check server logs: `src/python_mcp_server/python_mcp_server.log`
3. Enable debug mode: `export SMART_ASYNC_DEBUG=true`
