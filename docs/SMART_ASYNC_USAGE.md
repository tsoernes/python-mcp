# Smart-async job usage

This guide describes the current MCP tools. For the normative job lifecycle and requirements, see [Job management contract](JOB_MANAGEMENT.md).

## Start a job

The execution tools `py_run_script_in_dir`, `py_run_script_with_dependencies`, `py_benchmark_script`, and `py_run_saved_script` use smart-async behavior. They normally return a result directly if work finishes within the configured time budget (20 seconds by default). For longer work, the call returns a job identifier; `async_mode=True` requests background execution immediately.

Example tool call:

```text
py_run_script_in_dir(
  directory="/path/to/work",
  script_content="import time; print('started', flush=True); time.sleep(30); print('done')",
  async_mode=true,
  markdown_output=false
)
```

A background response contains `job_id` and a status. Save that exact ID for all follow-up calls. Do not use the removed `_async` variants or the legacy `py_get_job_output` tool.

## Check status and output

```text
py_job_status(job_id="<job_id>", incremental=false, markdown_output=false)
py_list_jobs(status_filter="running", limit=20, markdown_output=false)
```

`py_job_status` returns a `job` object with status, timestamps, progress, and either a final `result` or an `error`. While the job is running, partial stdout/stderr may be available. With `incremental=true`, the response also includes newly captured output since the preceding incremental read. A missing job returns an error object; it should not be treated as a completed result.

Recommended Codex workflow:

1. Start the execution tool and capture `job_id` only if a background response was returned.
2. Call `py_job_status` with that exact ID.
3. If status is `pending` or `running`, wait briefly and query the same ID again.
4. Use `py_list_jobs` as a separate discovery/check tool; it does not replace status for the selected ID.
5. When complete, read the final payload from `job.result`.

## Cancel and prune

```text
py_cancel_job(job_id="<job_id>", markdown_output=false)
py_prune_jobs(keep_completed=false, keep_failed=true, max_age_hours=24, markdown_output=false)
```

Cancellation applies to pending/running smart-async tasks. Pruning removes old eligible completed or failed records; by default it retains both. Neither tool is a replacement for saving a needed result elsewhere.

## Configuration and persistence

- `SMART_ASYNC_TIMEOUT_SECONDS` overrides the default 20-second switch-to-background budget.
- Job metadata is stored in `~/.python_mcp/meta/jobs.json` by default.
- A server restart marks jobs that were running at shutdown as failed; it cannot resume their work.
- `markdown_output=False` is recommended when a caller needs structured fields.

The MCP server executes submitted code with the server user's filesystem and network permissions. Smart-async changes scheduling, not sandboxing or isolation.
