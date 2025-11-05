from __future__ import annotations

from fastmcp import FastMCP

mcp = FastMCP(name="Python Script Executor")


@mcp.tool
def run_script_in_dir(
    directory: str,
    script: str,
    args: list[str] | None = None,
    use_uv: bool = True,
    timeout_seconds: int = 300,
) -> dict[str, str]:
    """
    Execute an existing Python script located in a given directory using either a uv-managed environment
    (default) or a Poetry virtual environment if pyproject.toml with poetry configuration is detected.

    Parameters:
        directory: Path to the directory containing the script.
        script: Script filename (e.g. run.py).
        args: Optional list of argument strings passed to the script.
        use_uv: When True prefer uv; when False attempt Poetry if available, otherwise fallback to system Python.
        timeout_seconds: Maximum execution time before termination.

    Returns:
        dict with:
            stdout: Captured standard output.
            stderr: Captured standard error.
            exit_code: Stringified integer exit code.
            execution_strategy: Description of how the environment was resolved.

    Error Handling:
        - Raises informative exceptions for missing directory, script not found, or timeout.
        - Tool should surface concise, actionable messages guiding next steps.

    Future Enhancements:
        - Streaming output support.
        - Provide structured logs (timestamped lines).
        - Sandboxed execution / resource limits.
    """
    # Skeleton implementation; actual process spawning, environment resolution and timeout handling
    # will be implemented in subsequent edits.
    raise NotImplementedError(
        "Environment resolution and execution logic not yet implemented."
    )


@mcp.tool
def run_script_with_dependencies(
    code: str,
    python_version: str = "3.12",
    dependencies: list[str] | None = None,
    args: list[str] | None = None,
    timeout_seconds: int = 300,
) -> dict[str, str]:
    """
    Execute an ad-hoc Python code snippet in a transient uv-managed environment.

    Parameters:
        code: The Python source code to execute (single script).
        python_version: Target Python version (must be installed or available via pyenv/uv).
        dependencies: List of package specifiers (e.g. ['requests', 'pydantic==2.*']).
        args: Optional list of argument strings accessible via sys.argv.
        timeout_seconds: Maximum execution time before termination.

    Returns:
        dict with:
            stdout: Captured standard output.
            stderr: Captured standard error.
            exit_code: Stringified integer exit code.
            resolved_dependencies: Dependency list after resolution.
            python_version_used: Final interpreter version string.

    Notes:
        - Will create an ephemeral environment (cacheable) keyed by hash of dependencies + version.
        - Future plan: Allow persistent named environments to avoid repeated resolution.

    Error Handling Strategy:
        - Provide actionable messages (e.g. suggest pinning if resolution fails).
        - Distinguish dependency resolution failures vs runtime exceptions.

    Security Considerations (to expand later):
        - Optional sandboxing (resource limits, network toggle).
        - Validate code length and deny overly large payloads.

    """
    # Skeleton implementation placeholder
    raise NotImplementedError(
        "Dynamic environment creation and execution not yet implemented."
    )


@mcp.tool
def suggest_additional_capabilities() -> list[str]:
    """
    Suggest additional server capabilities that complement script execution.

    Returns:
        A list of capability suggestions (strings).
    """
    return [
        "list_environments: Enumerate cached ephemeral environments and their metadata.",
        "inspect_environment: Show installed packages and versions for an environment key.",
        "create_persistent_environment: Pre-build and cache a named environment with dependencies.",
        "format_code: Apply code formatting (e.g. ruff / black) to a submitted snippet before execution.",
        "static_analysis: Run basic lint / security checks (bandit) prior to execution.",
        "stream_run_script: Stream stdout/stderr incrementally (requires transport streaming).",
        "benchmark_script: Time execution and report CPU/memory usage (needs resource collection).",
        "kill_running_script: Terminate a long-running process given an execution id.",
    ]


def main() -> None:
    # Entry point kept for script invocation compatibility; prefer mcp.run() when used as an MCP server.
    mcp.run()


if __name__ == "__main__":
    main()
