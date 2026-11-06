# Python REPL entrypoint — task specification

## User-specified requirements

- **2026-11-06:** Add REPL functionality like `node_repl` as a separate entrypoint in the `python-mcp` repository.
- **2026-11-06, original request:** The entrypoint must use `uv` and accept dependencies at startup. This dependency-delivery detail was superseded by the clarification below.
- **2026-11-06 clarification:** Pass dependencies as an optional argument to the `python_repl_execute` MCP tool, not as stdio-server startup arguments.
- **2026-11-06 clarification:** Use stdio and separate processes.
- **2026-11-06 clarification:** Also allow starting the REPL with a project virtual environment.
- **2026-11-06:** Implement it, suggest further features, ask questions only if needed, and add it to Codex when complete.
- **2026-11-06:** Commit and push progress as work proceeds.

## Implementation plan (agent proposal, not additional user requirements)

- Keep the existing shared HTTP Python MCP daemon and its script/job tools unchanged; add an independently launchable stdio MCP entrypoint.
- Give each launched REPL MCP process its own persistent Python worker process and namespace. Do not expose one process-global namespace to the shared HTTP daemon.
- Permit selecting an existing project venv as the REPL's base environment.
- **Implementation choice:** Install dependencies requested by `python_repl_execute` into a private per-server-session overlay, rather than modifying the selected project venv or repository lockfiles.
- Bound execution time/output, serialize operations on a kernel, and terminate/reap the worker on reset, shutdown, timeout or transport exit.

## Acceptance checks

- A stdio client can initialize, list tools, execute multiple cells sharing state, reset state, and query REPL information.
- The `python_repl_execute` tool accepts optional dependencies; they are passed to `uv` as individual arguments (no shell interpolation), without placing dependency requirements in the stdio-server startup args.
- Both the default interpreter and an explicit project venv are supported.
- Separate MCP processes cannot see each other's REPL namespaces.
- Failure, timeout, cancellation and server shutdown do not leave the worker process tree running.
- Repository tests, `py_compile`, and basedpyright pass for the new code; the Codex entry is validated without restarting other Codex instances.

## Progress and verification

- **2026-11-06:** Separate stdio FastMCP entrypoint, persistent per-process worker, uv dependency support, explicit project-venv selection, lifecycle cleanup, usage/security documentation, and focused tests implemented. The original implementation accepted dependencies at server startup; see the later clarification and revision status below.
- **2026-11-06:** Full unittest discovery passed (19 tests); `py_compile`, Ruff, and basedpyright passed. An exact `uv --no-project --python ...` stdio launch matching Codex configuration successfully listed tools and evaluated code inside `.venv-daemon`.
- **2026-11-06:** Added the `python-repl` entry to `/home/torstein.sornes/.codex/config.toml` and TOML parsing passed. Codex loads MCP configuration at startup; existing Codex processes were not restarted, so they will see the entry after a restart/reload.
- **2026-11-06 clarification:** The user clarified that dependencies belong on each
  `python_repl_execute` tool call, not on stdio-server startup. Implemented the
  optional `dependencies` list as additive session dependencies in a private uv
  target overlay, preserving the selected project venv and existing REPL state.
  Verification: 21 repository tests passed; the dependency install path was
  exercised with a stub uv executable (no package was installed into the
  workstation environment), and py_compile, Ruff, and basedpyright passed. A
  fresh stdio launch exposed the updated input schema and passed execution,
  persistence, venv-selection and reset smoke checks. The already-running Codex
  MCP process must be reloaded/restarted to pick up the changed tool schema.
