# Incremental Output Support

## Overview

Yes! The python-mcp server **now supports incremental output** for streaming jobs. This allows you to check back on a running job and see partial output as it's being produced, rather than waiting for the job to complete.

## Quick Answer

**Before this feature:** Jobs only returned output when completed.  
**After this feature:** Jobs can stream output line-by-line, and you can read it incrementally while the job is running.

## How It Works

### 1. Enable Streaming on Job Launch

Add `enable_streaming=True` when starting a long-running job with `async_mode=True`:

```python
result = await py_run_script_in_dir(
    directory=Path("."),
    script_content="""
import time
for i in range(10):
    print(f"Processing item {i+1}")
    time.sleep(1)
""",
    async_mode=True,
    enable_streaming=True,  # Enable incremental output capture
    job_label="Processing job"
)
# Returns: {"job_id": "...", "status": "pending"}
```

### 2. Check Partial Output While Running

Use `py_job_status` to check the job while it's running:

```python
# Get current output (full)
status = await py_job_status(job_id=result["job_id"])
print(status["job"]["partial_stdout"])
# Output: "Processing item 1\nProcessing item 2\nProcessing item 3\n..."
```

### 3. Incremental Reads (Only New Output)

Use `incremental=True` to get only new output since your last check:

```python
# First incremental check
status1 = await py_job_status(job_id=result["job_id"], incremental=True)
print(status1["job"]["new_stdout"])  # All output so far
# Output: "Processing item 1\nProcessing item 2\n..."

# Wait a bit...
await asyncio.sleep(3)

# Second incremental check - only new lines
status2 = await py_job_status(job_id=result["job_id"], incremental=True)
print(status2["job"]["new_stdout"])  # Only new output since status1
# Output: "Processing item 3\nProcessing item 4\n..."
```

## API Reference

### Tool Parameters

#### `enable_streaming` (bool, default: False)

Available on:
- `py_run_script_in_dir`
- `py_run_script_with_dependencies`
- `py_run_saved_script`

When `True`, captures output line-by-line during execution and makes it available via `partial_stdout`/`partial_stderr` fields.

**Important:** Only works when combined with `async_mode=True` (background jobs).

#### `incremental` (bool, default: False)

Available on:
- `py_job_status`

When `True`, returns only new output since the last incremental check via `new_stdout`/`new_stderr` fields.

### Response Fields

#### Full Status Response

```json
{
  "job": {
    "id": "...",
    "status": "running",
    "partial_stdout": "Output line 1\nOutput line 2\n...",
    "partial_stderr": "",
    ...
  }
}
```

#### Incremental Status Response

```json
{
  "job": {
    "id": "...",
    "status": "running",
    "partial_stdout": "Output line 1\nOutput line 2\n...",
    "partial_stderr": "",
    "new_stdout": "Output line 2\n",  // Only new since last check
    "new_stderr": ""
  },
  "incremental": true
}
```

## Use Cases

### 1. Progress Monitoring

Monitor long-running jobs to see progress:

```python
# Start a long job
result = await py_run_script_in_dir(
    directory=Path("."),
    script_content="""
import time
for i in range(100):
    print(f"Progress: {i+1}/100")
    time.sleep(0.5)
""",
    async_mode=True,
    enable_streaming=True
)

# Poll for updates
while True:
    status = await py_job_status(result["job_id"])
    if status["job"]["status"] != "running":
        break
    
    # Show current progress
    print(status["job"]["partial_stdout"])
    await asyncio.sleep(2)
```

### 2. Real-Time Log Tailing

Get only new log lines like `tail -f`:

```python
result = await py_run_script_in_dir(
    directory=Path("."),
    script_content="./long_running_process.sh",
    async_mode=True,
    enable_streaming=True
)

# Tail-like behavior
while True:
    status = await py_job_status(result["job_id"], incremental=True)
    
    # Print only new lines
    if status["job"].get("new_stdout"):
        print(status["job"]["new_stdout"], end="")
    
    if status["job"]["status"] != "running":
        break
    
    await asyncio.sleep(1)
```

### 3. Early Error Detection

Detect errors early without waiting for completion:

```python
result = await py_run_script_in_dir(
    directory=Path("."),
    script_content=long_script,
    async_mode=True,
    enable_streaming=True
)

# Check for errors while running
for i in range(60):
    status = await py_job_status(result["job_id"], incremental=True)
    
    # Check stderr for errors
    if "ERROR" in status["job"].get("partial_stderr", ""):
        print("Error detected early!")
        await py_cancel_job(result["job_id"])
        break
    
    if status["job"]["status"] != "running":
        break
    
    await asyncio.sleep(1)
```

## Implementation Details

### How Output is Captured

1. **Line-by-line reading:** Output is read line by line as it's produced
2. **Callback mechanism:** Uses `create_output_callback()` to append output to job
3. **Persistent storage:** Partial output is saved to `~/.python_mcp/meta/jobs.json`
4. **Offset tracking:** Tracks read position for incremental access

### Performance Considerations

- **Overhead:** Minimal (<1ms per line)
- **Memory:** Stores full output in memory and disk
- **I/O:** One disk write per line (can be optimized with batching)
- **Recommended:** Enable streaming only when needed

### Limitations

1. **Background jobs only:** Streaming requires `async_mode=True`
2. **Line-buffered:** Output is captured per line, not per character
3. **No partial lines:** Incomplete lines (without `\n`) may not appear until flushed
4. **Memory limit:** Very large outputs consume memory

## Comparison with Old System

### Before (Removed `_async` Tools)

```python
# Old system had streaming but with separate tools
result = await py_run_script_in_dir_async(
    directory=Path("."),
    script_content="...",
    stream=True  # Old parameter
)

# Had to use different job status tool
output = await py_get_job_output(job_id=result.job_id)
```

### After (Unified Smart Async)

```python
# New system: single tool with streaming parameter
result = await py_run_script_in_dir(
    directory=Path("."),
    script_content="...",
    async_mode=True,
    enable_streaming=True  # New parameter
)

# Same job status tool with incremental support
status = await py_job_status(job_id=result["job_id"], incremental=True)
```

## Best Practices

### 1. Enable Streaming Selectively

Only enable for jobs where you need real-time feedback:

```python
# Good: Long-running job with progress output
enable_streaming=True

# Not needed: Quick job that completes in seconds
enable_streaming=False  # Default
```

### 2. Use Incremental Reads for Efficiency

Reduces response size and processing:

```python
# Efficient: Only get new data
status = await py_job_status(job_id, incremental=True)

# Wasteful: Get all data every time
status = await py_job_status(job_id, incremental=False)
```

### 3. Flush Output in Scripts

Ensure output appears immediately:

```python
import sys

print("Progress update", flush=True)  # Flush immediately
sys.stdout.flush()  # Or flush explicitly
```

### 4. Handle Completion

Always check for completion:

```python
while True:
    status = await py_job_status(job_id, incremental=True)
    
    # Process new output
    if status["job"].get("new_stdout"):
        process_output(status["job"]["new_stdout"])
    
    # Check completion
    if status["job"]["status"] in ("completed", "failed", "cancelled"):
        break
    
    await asyncio.sleep(1)
```

## Testing Example

```python
# Test script that outputs incrementally
test_script = """
import time
import sys

for i in range(5):
    print(f"Line {i+1}")
    sys.stdout.flush()
    time.sleep(1)
"""

# Launch with streaming
result = await py_run_script_in_dir(
    directory=Path("."),
    script_content=test_script,
    async_mode=True,
    enable_streaming=True,
    job_label="Streaming test"
)

# Check after 2 seconds (should see ~2 lines)
await asyncio.sleep(2)
status1 = await py_job_status(result["job_id"])
print("Partial output:", status1["job"]["partial_stdout"])
# Expected: "Line 1\nLine 2\n"

# Check again after 2 more seconds (should see ~4 lines)
await asyncio.sleep(2)
status2 = await py_job_status(result["job_id"], incremental=True)
print("New output:", status2["job"]["new_stdout"])
# Expected: "Line 3\nLine 4\n"

# Wait for completion
await asyncio.sleep(2)
status3 = await py_job_status(result["job_id"])
print("Final status:", status3["job"]["status"])
# Expected: "completed"
print("Final output:", status3["job"]["result"]["stdout"])
# Expected: "Line 1\nLine 2\nLine 3\nLine 4\nLine 5\n"
```

## Troubleshooting

### Output Not Appearing

**Problem:** Partial output stays empty  
**Solution:** 
1. Ensure `enable_streaming=True` is set
2. Ensure `async_mode=True` is set
3. Flush output in your script: `sys.stdout.flush()`

### Getting All Output Every Time

**Problem:** Incremental reads return all output  
**Solution:** This is expected on the first incremental read. Subsequent reads will only return new output.

### Job Never Completes

**Problem:** Job status stays "running" forever  
**Solution:** 
1. Check server logs: `src/python_mcp_server/python_mcp_server.log`
2. Check job persistence: `~/.python_mcp/meta/jobs.json`
3. Try cancelling: `await py_cancel_job(job_id)`

### Large Memory Usage

**Problem:** Server uses too much memory  
**Solution:**
1. Disable streaming for jobs with huge output
2. Use `py_prune_jobs` to clean up completed jobs
3. Consider output size limits (future feature)

## Future Enhancements

Potential improvements being considered:

- [ ] Batched output writes (reduce I/O overhead)
- [ ] Output size limits with truncation
- [ ] Streaming compression for large outputs
- [ ] WebSocket/SSE streaming for real-time updates
- [ ] Progress percentage extraction from output

## Related Documentation

- **SMART_ASYNC_USAGE.md** - Complete smart async guide
- **SMART_ASYNC_MIGRATION.md** - Migration from old system
- **TEST_RESULTS.md** - Test results and examples

## Summary

**Yes, incremental output is fully supported!** 

Enable it with `enable_streaming=True` when launching background jobs, then use `py_job_status(..., incremental=True)` to read output as it's produced. This gives you real-time visibility into long-running jobs without waiting for completion.