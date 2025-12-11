# Python MCP Server Examples

This directory contains practical examples demonstrating various features of the Python MCP Server.

## Available Examples

### env_vars_demo.py

Comprehensive demonstration of environment variable support, including:

1. **Using env_vars Dictionary** - Pass environment variables directly as a Python dict
2. **Loading from .env File** - Load variables from a .env file
3. **Combining Both** - Demonstrates override precedence when using both methods
4. **With Dependencies** - Using environment variables alongside external packages

**Run the demo:**
```bash
uv run python examples/env_vars_demo.py
```

**Key Takeaways:**
- Environment variables can be passed via `env_vars` dict or loaded from `env_file`
- Override precedence: process env < env_file < env_vars (dict has highest priority)
- Works with all execution tools (sync, async, with dependencies, benchmarking)
- Supports standard .env file format with comments and quotes

## Environment Variable Features

All execution tools support environment variables:

- `py_run_script_in_dir` (sync & async)
- `py_run_script_with_dependencies` (sync & async)
- `py_benchmark_script`
- `py_run_saved_script`

### Quick Example

```python
from python_mcp_server import _exec_with_dependencies_sync

result = _exec_with_dependencies_sync(
    script_content="""
import os
print(f"API Key: {os.getenv('API_KEY')}")
print(f"Database: {os.getenv('DATABASE_URL')}")
""",
    script_path=None,
    python_version="3.13",
    dependencies=[],
    args=None,
    timeout_seconds=30,
    env_vars={
        "API_KEY": "sk-test-123",
        "DATABASE_URL": "postgresql://localhost/db"
    }
)

print(result.stdout)
```

### .env File Format

```env
# Comments are supported
DATABASE_URL=postgresql://localhost/testdb
API_KEY="secret123"
DEBUG=true
QUOTED='single quotes work too'
NO_QUOTES=also_work
```

## Contributing Examples

When adding new examples:

1. Create a descriptive filename (e.g., `async_streaming_demo.py`)
2. Include docstrings explaining what the example demonstrates
3. Add error handling and clear output
4. Update this README with a description
5. Test the example works correctly with `uv run python examples/your_example.py`

## See Also

- [Main README](../README.md) - Complete server documentation
- [Test Suite](../test_env_vars.py) - Comprehensive tests for env var support