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

## Shared Codex daemon (Linux)

For multiple local Codex instances to share one MCP process, use the authenticated
Streamable HTTP daemon. Stdio remains available for other clients and fallback.
The daemon binds only to `127.0.0.1:8794/mcp`; it is a single process because
job execution tasks are process-local.

Create an isolated environment so updating daemon dependencies does not replace
packages currently loaded by stdio MCP sessions:

```fish
cd /home/torstein.sornes/code/python-mcp
set -lx UV_PROJECT_ENVIRONMENT .venv-daemon
uv sync
install -d -m 700 ~/.config/shared-mcp
umask 077; openssl rand -hex 32 > ~/.config/shared-mcp/python-mcp.token
install -Dm 644 deploy/systemd/user/python-mcp-server.service ~/.config/systemd/user/python-mcp-server.service
systemctl --user daemon-reload
systemctl --user enable --now python-mcp-server.service
```

The service's user-local unit sends logs to the user journal and stores job state
under `~/.local/state/python-mcp-server`. Its bearer token stays in the private
`~/.config/shared-mcp/python-mcp.token` file and is never placed in Codex config
or the repository. The helper referenced by Codex reads that file and emits an
HTTP authorization header on demand. Do not expose this service beyond loopback:
execution tools run with the server user's filesystem and network permissions.

Configure the existing Codex `python-mcp` entry as a local HTTP server:

```toml
[mcp_servers."python-mcp"]
url = "http://127.0.0.1:8794/mcp"
http_headers_helper = "/home/torstein.sornes/code/python-mcp/scripts/python_mcp_http_auth_header.py"
tool_timeout_sec = 600.0
```

Keep any existing per-tool approval settings under that table. Codex needs to
reload its MCP configuration before existing sessions switch from stdio to the
shared daemon. Check daemon health with
`systemctl --user status python-mcp-server.service` and its logs with
`journalctl --user -u python-mcp-server.service`.

To update the isolated environment after changing dependencies, run
`UV_PROJECT_ENVIRONMENT=.venv-daemon uv sync` and restart the user service.

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

## Optional persistent Python REPL

For iterative scratch work, the repository also provides a separate stdio MCP
entrypoint at `scripts/python_repl_mcp.py`. Codex starts one server process per
client; each server lazily starts its own persistent Python worker. See
[`docs/PYTHON_REPL.md`](docs/PYTHON_REPL.md) for startup dependency and project
venv options, Codex configuration, lifecycle, and security limits. This is
separate from the shared HTTP Python MCP daemon and does not change the existing
script/job tools.

## Environment variables

Execution tools accept an `env_vars` mapping and/or an `env_file`. Values supplied in `env_vars` override the same keys loaded from the file; the parent process environment is inherited unless overridden.

## Security note

Submitted code runs with the MCP server process's filesystem and network permissions. This server does not sandbox executed code. Use it only with trusted scripts and inputs.

## Development and maintenance

The repository's test scripts are under `tests/`. The job registry and MCP API regression tests are in `tests/test_job_management.py`; the smart-async implementation is in `src/python_mcp_server/smart_async.py`.

Historical design and migration documents are retained under `docs/` for context. They are not current tool references; use the current contract and guides linked above.
