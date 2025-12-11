# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Smart Async Pattern** - Production-tested `@smart_async` decorator for intelligent background job execution
  - Automatic timeout switching: fast tasks complete inline, slow tasks move to background seamlessly
  - Shielded task execution: tasks continue running even after timeout (not cancelled)
  - Explicit async mode: `async_mode=True` parameter for known long-running operations
  - Progress tracking: jobs can report progress updates via `create_progress_callback()`
  - Job persistence: jobs saved to `~/.python_mcp/meta/jobs.json` and survive server restarts
  - Context-based tracking: uses `contextvars` for automatic job_id propagation
  - New module: `src/python_mcp_server/smart_async.py` (489 lines)
  - Based on mcp-builder skill production-tested pattern

- **Job Management Tools** - Complete job lifecycle management
  - `py_job_status(job_id)`: Get job status, progress, result, or error
  - `py_list_jobs(status_filter, limit)`: List jobs with optional filtering
  - `py_cancel_job(job_id)`: Cancel running background jobs
  - `py_prune_jobs(keep_completed, keep_failed, max_age_hours)`: Clean up old jobs
  - Job states: pending, running, completed, failed, cancelled
  - Progress metadata: `{"current": int, "total": int, "message": str}`

- **Environment Variable Support** - All execution tools now support passing environment variables via dictionary or .env file
  - New `env_vars` parameter: accepts `dict[str, str]` for direct variable passing
  - New `env_file` parameter: accepts `Path` to load variables from .env files
  - Proper override precedence: process environment < env_file < env_vars
  - Simple .env parser supporting comments, empty lines, and quoted values
  - Added to all execution tools:
    - `py_run_script_in_dir` (sync & async)
    - `py_run_script_with_dependencies` (sync & async)
    - `py_benchmark_script`
    - `py_run_saved_script`

- **Helper Functions**
  - `_load_env_file()`: Parses .env files with standard KEY=VALUE syntax
  - `_build_process_env()`: Merges environment sources with correct precedence

- **Examples and Tests**
  - Comprehensive test suite in `test_env_vars.py` covering:
    - .env file parsing
    - Environment building and precedence
    - Script execution with environment variables
  - Detailed demo in `examples/env_vars_demo.py` with 4 practical examples
  - Examples README documenting usage patterns

- **Documentation**
  - New "Smart Async Pattern" section in README with comprehensive guide
  - New "Environment Variables" section in README
  - Updated feature matrix to show smart async, progress tracking, and env vars
  - Updated all tool reference documentation
  - Added usage examples for env_vars, env_file, and smart async jobs
  - Added FAQ entries for smart async and environment variables
  - Production examples in `test_smart_async.py` (8 comprehensive tests)

### Changed

- All `subprocess.Popen` calls now accept `env` parameter
- Updated feature matrix in README to show smart async, progress, and env var support
- Added `Any` type import for proper type hints
- Initialize smart async state in `main()` function
- Switched from custom .env parser to `python-dotenv` library

### Dependencies

- Added `python-dotenv` for industry-standard .env file parsing

### Tests

- `test_smart_async.py` - 8/8 tests passing ✅
  1. Fast synchronous completion (< timeout)
  2. Automatic timeout switching to background
  3. Explicit async mode launches immediately
  4. Progress tracking with live updates
  5. Job cancellation
  6. Job listing and filtering by status
  7. Error handling in async jobs
  8. Job pruning by age and status

## [0.1.0] - Initial Release

### Added

- Basic script execution with `py_run_script_in_dir`
- Dependency management with `py_run_script_with_dependencies`
- Asynchronous execution variants
- Job management and introspection tools
- Streaming output support
- Benchmarking capabilities with `py_benchmark_script`
- Saved script management
- FastMCP-based MCP server implementation