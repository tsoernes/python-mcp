# python_executor_mcp

A FastMCP server primarily used from Codex to execute Python scripts and manage long-running work. It supports `uv`-managed execution, explicit/detected dependencies, environment variables, benchmarking, saved scripts, and smart-async jobs.

> **Job API source of truth:** [docs/JOB_MANAGEMENT.md](docs/JOB_MANAGEMENT.md). Active usage guides: [Smart-async usage](docs/SMART_ASYNC_USAGE.md) and [Incremental output](docs/INCREMENTAL_OUTPUT.md).

## Requirements and startup

- Python 3.13 or newer
- `uv`
- Project dependencies installed with `uv sync`

Run the stdio server from the repository root:

```sh
uv run python-executor-mcp
```

The module and compatibility entry point are also available:

```sh
uv run python -m python_mcp_server
uv run python-mcp-server
```

## Registered tools

Tool names below are the names exposed to MCP clients, including Codex. The live MCP schemas are authoritative for parameters.

### Execution

- `py_run_script_in_dir` — run an existing script or inline source in a directory, with optional `uv`, environment and timeout settings.
- `py_run_script_with_dependencies` — run code in a transient `uv` environment with requested dependencies.
- `py_benchmark_script` — run code while collecting wall time, CPU time and peak RSS metrics.
- `py_run_saved_script` — run a script saved with `py_save_script`.

These tools use smart-async behavior: work that completes within the default 20-second budget returns its result directly; longer work switches to a background job. Set `async_mode=true` to request background execution immediately. Background responses contain a `job_id` for the lifecycle tools below. `SMART_ASYNC_TIMEOUT_SECONDS` changes the default budget.

### Saved-script management

- `py_save_script`
- `py_list_scripts`
- `py_view_script`

Inline source passed to execution tools is autosaved under the repository's ignored `autosaved_scripts/` directory, with metadata alongside it.

### Job lifecycle

- `py_job_status(job_id, incremental=True, markdown_output=True)`
- `py_list_jobs(status_filter=None, limit=50, markdown_output=True)`
- `py_cancel_job(job_id, markdown_output=True)`
- `py_prune_jobs(keep_completed=True, keep_failed=True, max_age_hours=24, markdown_output=True)`

Use the same returned `job_id` for status, cancellation, and job-list verification. For structured responses set `markdown_output=false`. Unknown IDs return an error object. Current details, response shapes, persistence and output behavior are in [docs/JOB_MANAGEMENT.md](docs/JOB_MANAGEMENT.md).

The server no longer exposes the disconnected legacy job tools (`py_get_job_output`, `py_list_running_jobs`, `py_kill_job`, `py_cleanup_jobs`) or the `job-stream://` resource. Use the smart-async lifecycle tools instead.

## Environment variables

Execution tools accept an `env_vars` mapping and/or an `env_file`. Values supplied in `env_vars` override the same keys loaded from the file; the parent process environment is inherited unless overridden.

## Security note

Submitted code runs with the MCP server process's filesystem and network permissions. This server does not sandbox executed code. Use it only with trusted scripts and inputs.

## Development and maintenance

The repository's test scripts are under `tests/`. The job registry and MCP API regression tests are in `tests/test_job_management.py`; the smart-async implementation is in `src/python_mcp_server/smart_async.py`.

Historical design and migration documents are retained under `docs/` for context. They are not current tool references; use the current contract and guides linked above.
