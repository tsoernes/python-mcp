# Job management contract

This document is the current, authoritative reference for background jobs exposed by the MCP server. Keep the MCP registration, implementation, tests, and this contract aligned.

## User-specified requirements

Source: user request, 2026-11-05.

- Fix the job-lookup inconsistency shown in the Codex transcript, and fix related inconsistencies in this repository.
- Commit and push the completed change.
- Treat Codex as the primary MCP client when reviewing discoverability and workflow clarity.

## Implementation decision (not a separate user requirement)

Use the smart-async job registry as the sole job lifecycle API. Remove the unused legacy process registry/tools/resource rather than maintain two incompatible registries. Regression coverage should verify the exposed MCP surface and the status/list/status lookup sequence.

## Current API

Execution tools decorated with `@smart_async` may finish in the original call, switch to a background job after the time budget, or start in the background when passed `async_mode=True`. Background responses contain a readable `job_id`; pass that exact value to the job tools.

| Tool | Purpose |
| --- | --- |
| `py_job_status(job_id, incremental=True, markdown_output=True)` | Read one job's status, progress, result/error, and running output. |
| `py_list_jobs(status_filter=None, limit=50, markdown_output=True)` | List jobs, optionally filtered by status. |
| `py_cancel_job(job_id, markdown_output=True)` | Cancel a pending/running task and wait for its subprocess tree to stop and be reaped. |
| `py_prune_jobs(keep_completed=True, keep_failed=True, max_age_hours=24, markdown_output=True)` | Remove eligible old completed/failed records. |

For machine-readable tool responses, use `markdown_output=False`. A missing identifier passed to `py_job_status` is returned as `{"error": "Job <id> not found"}`; it is not a successful status. `py_cancel_job` similarly returns an error object if the ID is unknown or not cancellable.

When `py_job_status` is called with `incremental=True` while a job is running, its nested `job` payload includes `new_stdout` and `new_stderr` for output since the previous incremental read in that server process. The regular `partial_stdout` and `partial_stderr` fields are running snapshots. On completion, inspect `job.result` for the final execution result, including full `stdout` and `stderr` where provided. `incremental=False` returns the full current status without advancing output offsets.

Job records are persisted under `~/.python_mcp/meta/jobs.json` by default. Jobs that were still running when the server stopped are marked failed during startup recovery. Tool results may be Markdown-rendered by default; clients should not parse rendered text as if it were the structured object.

Cancellation targets only tasks owned by the current server process. A persisted
`running` record without a live local task is not reported as successfully
cancelled. Script subprocesses are launched in their own POSIX session; a cancel
or execution timeout sends TERM to the process group, then KILL after a short
grace period if descendants remain, and waits for the direct child and its output
readers to finish. The shared HTTP daemon uses its own state directory
(`~/.local/state/python-mcp-server`) so older, independently running stdio
instances cannot overwrite the daemon's job registry.

## Regression/acceptance criteria

- The four smart-async lifecycle tools are registered.
- `py_get_job_output`, `py_list_running_jobs`, `py_kill_job`, and `py_cleanup_jobs` are not registered; they used a disconnected in-memory registry.
- No `job-stream://` resource template is registered.
- A background job can be queried by `py_job_status`, listed by `py_list_jobs`, and queried again by the same ID without losing its status.
- Completed Pydantic execution results are converted to JSON-safe values before persistence, so successful background jobs remain completed and readable after a refresh/restart.
- Cancelling a script job terminates its direct process and spawned descendants, waits for cleanup, and records the final state as `cancelled`.
- Cancelling a synchronous tool request before it transitions to a background job also cancels and reaps its shielded subprocess task.
- Active README and usage docs describe the current parameters and return shape; historical notes are labeled as such.

## Change history

- 2026-11-05: Added as the durable job API contract while resolving the inconsistent legacy/smart-async MCP surfaces.
