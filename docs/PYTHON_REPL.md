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
  --project-venv /home/torstein.sornes/code/python-mcp/.venv-daemon
```

Pass optional PEP 508 requirements on each `python_repl_execute` call using its
`dependencies` list, for example:

```json
{
  "code": "import pandas as pd\npd.DataFrame({'value': [1, 2, 3]})",
  "dependencies": ["pandas>=2"]
}
```

Dependencies are installed lazily into a private, per-server-session overlay by
`uv pip install --target`; the selected project venv, `pyproject.toml`, and
`uv.lock` are not modified. Newly requested dependencies are additive and remain
available to later cells in the same REPL session. The first dependency request
may take time while uv resolves/downloads/builds packages. If no
`--project-venv` is supplied, the MCP process's Python interpreter is the base.

To use another project's environment, point `--project-venv` to its existing
venv. The directory must contain `pyvenv.cfg` and its platform's Python
executable. The worker's current directory defaults to the MCP process's cwd;
`--cwd` can override it.

## Codex configuration

Add a second MCP server entry; keep the existing `python-mcp` shared HTTP entry
unchanged. Dependencies are passed to `python_repl_execute`, not as server
startup arguments.

```toml
[mcp_servers."python-repl"]
command = "/home/torstein.sornes/.local/bin/uv"
args = [
  "run", "--no-project",
  "--python", "/home/torstein.sornes/code/python-mcp/.venv-daemon/bin/python",
  "--",
  "/home/torstein.sornes/code/python-mcp/scripts/python_repl_mcp.py",
  "--project-venv", "/home/torstein.sornes/code/python-mcp/.venv-daemon",
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

- `python_repl_execute(code, timeout_seconds?, dependencies?)` — optionally
  install dependencies for the session, execute a cell, and return captured
  stdout/stderr and the repr of a final expression.
- `python_repl_reset()` — terminate the worker and discard its variables and
  imported modules; the next execution creates a fresh worker with the same
  session dependency overlay.
- `python_repl_info()` — report session dependencies, base interpreter,
  cwd, and whether a worker is running without starting it.

Cells are serialized. Default code/output limits are 100,000 and 40,000
characters; the default execution timeout is 60 seconds and the maximum is 600
seconds. The worker is stopped on MCP shutdown, reset, timeout, or cancellation.

## Security

This is a convenience REPL, **not a sandbox**. Python code has the filesystem,
environment variables, network access, and user permissions of the selected
interpreter process. Requested dependencies are resolved through uv and may
download or build third-party packages. Only use trusted code and dependency
specifications. Separate stdio processes keep in-memory namespaces isolated
between Codex clients; they are not an OS security boundary between processes
running as the same user.

## Possible follow-up features

- An optional persisted dependency manifest/export for reproducible sessions.
- Structured display support for tables, plots, and notebook-style rich output.
- Optional idle timeout and memory limits for long-lived kernels.
- Save/export the current namespace or replay cells into a reproducible script.
