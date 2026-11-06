# Python REPL entrypoint — task specification

## User-specified requirements

- **2026-11-06:** Add REPL functionality like `node_repl` as a separate entrypoint in the `python-mcp` repository.
- **2026-11-06:** The entrypoint must use `uv` and allow dependencies to be passed at startup.
- **2026-11-06 clarification:** Use stdio and separate processes.
- **2026-11-06 clarification:** Also allow starting the REPL with a project virtual environment.
- **2026-11-06:** Implement it, suggest further features, ask questions only if needed, and add it to Codex when complete.
- **2026-11-06:** Commit and push progress as work proceeds.

## Implementation plan (agent proposal, not additional user requirements)

- Keep the existing shared HTTP Python MCP daemon and its script/job tools unchanged; add an independently launchable stdio MCP entrypoint.
- Give each launched REPL MCP process its own persistent Python worker process and namespace. Do not expose one process-global namespace to the shared HTTP daemon.
- Have `uv` create/reuse an environment for optional startup requirements without mutating the repository's project environment or lockfile.
- Permit selecting an existing project venv as the REPL's base environment; startup dependencies should be layered without changing that venv.
- Bound execution time/output, serialize operations on a kernel, and terminate/reap the worker on reset, shutdown, timeout or transport exit.

## Acceptance checks

- A stdio client can initialize, list tools, execute multiple cells sharing state, reset state, and query REPL information.
- Startup dependencies are passed to `uv` as individual arguments (no shell interpolation); both the default uv-managed environment and an explicit project venv are supported.
- Separate MCP processes cannot see each other's REPL namespaces.
- Failure, timeout, cancellation and server shutdown do not leave the worker process tree running.
- Repository tests, `py_compile`, and basedpyright pass for the new code; the Codex entry is validated without restarting other Codex instances.

## Progress and verification

- **2026-11-06:** Separate stdio FastMCP entrypoint, persistent per-process worker, uv startup requirements, explicit project-venv selection, lifecycle cleanup, usage/security documentation, and focused tests implemented.
- **2026-11-06:** Full unittest discovery passed (19 tests); `py_compile`, Ruff, and basedpyright passed. An exact `uv --no-project --python ...` stdio launch matching Codex configuration successfully listed tools and evaluated code inside `.venv-daemon`.
- **2026-11-06:** Added the `python-repl` entry to `/home/torstein.sornes/.codex/config.toml` and TOML parsing passed. Codex loads MCP configuration at startup; existing Codex processes were not restarted, so they will see the entry after a restart/reload.
