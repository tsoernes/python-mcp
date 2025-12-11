# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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
  - New "Environment Variables" section in README
  - Updated all tool reference documentation
  - Added usage examples for env_vars and env_file
  - Added FAQ entries for environment variable questions

### Changed

- All `subprocess.Popen` calls now accept `env` parameter
- Updated feature matrix in README to show env var support

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