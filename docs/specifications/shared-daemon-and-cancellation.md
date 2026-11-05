# User requirements: shared Python MCP daemon and cancellation

**Recorded:** 2026-11-05
**Source:** User request in this Codex task.

## Confirmed requirements

1. Implement the previously selected job-cancellation item: cancelling a job must stop the subprocess work it started, including child processes, rather than only changing the MCP job status.
2. Implement the previously selected dependency-upgrade item: review and upgrade project packages to current releases where compatible, then validate the result.
3. Implement a persistent daemon so multiple Codex instances can connect to one Python MCP server process.
4. Enable that daemon on this PC and configure Codex to connect to it.
5. Commit and push the task code. Exclude logs, caches, autosaved test scripts, and `.env` files.

## Scope boundary

- Preserve existing stdio operation as a compatibility path.
- Preserve unrelated existing working-tree changes and do not include them in the task commit.
- Do not commit credentials, generated logs, caches, or autosaved test scripts.

## Agent implementation notes (not user requirements)

- The planned daemon transport, loopback binding, authentication mechanism, service manager, port, tests, and exact package versions are implementation decisions. Record these as implementation details, not as user requirements.
