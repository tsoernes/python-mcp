# Optional persistent Python REPL

The REPL is a separate **stdio** MCP entrypoint. A Codex instance launches its
own MCP process, which lazily launches one Python worker. Names and imports
persist between `python_repl_execute` calls in that process only. This is not
part of the shared HTTP Python MCP daemon.

## Launch directly

From the repository root:

```fish
uv run --no-project \
  --python /home/torstein.sornes/code/python-mcp/.venv-daemon/bin/python -- \
  /home/torstein.sornes/code/python-mcp/scripts/python_repl_mcp.py \
  --project-venv /home/torstein.sornes/code/python-mcp/.venv-daemon \
  --dependency 'numpy>=2' --dependency 'pandas>=2'
```

`--dependency` is repeatable and accepts PEP 508 requirements. The REPL worker
uses `uv run --no-project --python <base-python> --with <requirement> ...`; uv
reuses its cache and does not modify the selected project venv, `pyproject.toml`
or `uv.lock`. If no `--project-venv` is supplied, the MCP process's Python
interpreter is the base. Dependencies are resolved lazily on the first execute
call, not merely by starting the MCP process.

To use another project's environment, point `--project-venv` to its existing
venv. The directory must contain `pyvenv.cfg` and its platform's Python
executable. The worker's current directory defaults to the MCP process's cwd;
`--cwd` can override it.

## Codex configuration

Add a second MCP server entry; keep the existing `python-mcp` shared HTTP entry
unchanged. Add one `--dependency`, `<requirement>` pair per startup dependency
after the script path when desired.

```toml
[mcp_servers."python-repl"]
command = "/home/torstein.sornes/.local/bin/uv"
args = [
  "run", "--no-project",
  "--python", "/home/torstein.sornes/code/python-mcp/.venv-daemon/bin/python",
  "--",
  "/home/torstein.sornes/code/python-mcp/scripts/python_repl_mcp.py",
  "--project-venv", "/home/torstein.sornes/code/python-mcp/.venv-daemon",
  # Optional, repeat as needed:
  # "--dependency", "pandas>=2",
]
tool_timeout_sec = 600.0

[mcp_servers."python-repl".tools.python_repl_execute]
approval_mode = "approve"

[mcp_servers."python-repl".tools.python_repl_reset]
approval_mode = "approve"

[mcp_servers."python-repl".tools.python_repl_info]
approval_mode = "approve"
```

Restart/reload Codex after editing MCP configuration. The REPL server exposes:

- `python_repl_execute(code, timeout_seconds?)` — execute a cell and return
  captured stdout/stderr and the repr of a final expression.
- `python_repl_reset()` — terminate the worker and discard its variables and
  imported modules; the next execution creates a fresh worker with the same
  startup environment.
- `python_repl_info()` — report configured dependencies, base interpreter,
  cwd, and whether a worker is running without starting it.

Cells are serialized. Default code/output limits are 100,000 and 40,000
characters; the default execution timeout is 60 seconds and the maximum is 600
seconds. The worker is stopped on MCP shutdown, reset, timeout, or cancellation.

## Security

This is a convenience REPL, **not a sandbox**. Python code has the filesystem,
environment variables, network access, and user permissions of the selected
interpreter process. Only use it with trusted code. Separate stdio processes
keep in-memory namespaces isolated between Codex clients; they are not an OS
security boundary between processes running as the same user.

## Possible follow-up features

- Per-cell dependency additions, if a workflow needs them without restarting.
- Structured display support for tables, plots, and notebook-style rich output.
- Optional idle timeout and memory limits for long-lived kernels.
- Save/export the current namespace or replay cells into a reproducible script.
