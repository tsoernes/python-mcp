#!/usr/bin/env python3
"""
Environment Variables Demo for Python MCP Server

This example demonstrates how to use environment variables with the python-mcp-server
in three different ways:
1. Passing env_vars as a dictionary
2. Loading from a .env file
3. Combining both (with proper precedence)
"""

import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from python_mcp_server import _exec_with_dependencies_sync


def example_1_env_vars_dict():
    """Example 1: Pass environment variables as a dictionary."""
    print("=" * 70)
    print("Example 1: Using env_vars Dictionary")
    print("=" * 70)

    script = """
import os

# Access environment variables
db_url = os.getenv('DATABASE_URL', 'not set')
api_key = os.getenv('API_KEY', 'not set')
debug = os.getenv('DEBUG', 'not set')

print(f"Database URL: {db_url}")
print(f"API Key: {api_key}")
print(f"Debug Mode: {debug}")
"""

    result = _exec_with_dependencies_sync(
        script_content=script,
        script_path=None,
        python_version="3.13",
        dependencies=[],
        args=None,
        timeout_seconds=30,
        env_vars={
            "DATABASE_URL": "postgresql://localhost:5432/mydb",
            "API_KEY": "sk-test-1234567890",
            "DEBUG": "true",
        },
    )

    print("Output:")
    print(result.stdout)
    print(f"Exit code: {result.exit_code}")
    print()


def example_2_env_file():
    """Example 2: Load environment variables from a .env file."""
    print("=" * 70)
    print("Example 2: Loading from .env File")
    print("=" * 70)

    # Create a sample .env file
    env_file = Path("/tmp/demo.env")
    env_content = """
# Application Configuration
APP_NAME=MyAwesomeApp
APP_VERSION=1.0.0
APP_ENV=production

# Database Configuration
DB_HOST=db.example.com
DB_PORT=5432
DB_NAME=prod_db
DB_USER=app_user
DB_PASSWORD="s3cr3t_p@ssw0rd"

# API Settings
API_TIMEOUT=30
API_RETRY_COUNT=3
"""
    env_file.write_text(env_content)

    script = """
import os

print("Application Settings:")
print(f"  Name: {os.getenv('APP_NAME')}")
print(f"  Version: {os.getenv('APP_VERSION')}")
print(f"  Environment: {os.getenv('APP_ENV')}")
print()
print("Database Configuration:")
print(f"  Host: {os.getenv('DB_HOST')}")
print(f"  Port: {os.getenv('DB_PORT')}")
print(f"  Database: {os.getenv('DB_NAME')}")
print(f"  User: {os.getenv('DB_USER')}")
print(f"  Password: {'*' * len(os.getenv('DB_PASSWORD', ''))}")
print()
print("API Settings:")
print(f"  Timeout: {os.getenv('API_TIMEOUT')}s")
print(f"  Retry Count: {os.getenv('API_RETRY_COUNT')}")
"""

    result = _exec_with_dependencies_sync(
        script_content=script,
        script_path=None,
        python_version="3.13",
        dependencies=[],
        args=None,
        timeout_seconds=30,
        env_file=env_file,
    )

    print("Output:")
    print(result.stdout)
    print(f"Exit code: {result.exit_code}")

    # Clean up
    env_file.unlink()
    print()


def example_3_combined_with_override():
    """Example 3: Combine .env file with env_vars dict (demonstrating precedence)."""
    print("=" * 70)
    print("Example 3: Combining .env File and env_vars Dict")
    print("=" * 70)

    # Create a .env file with base configuration
    env_file = Path("/tmp/base.env")
    env_content = """
APP_ENV=production
DATABASE_URL=postgresql://prod-db.example.com/proddb
LOG_LEVEL=info
FEATURE_FLAG_A=enabled
FEATURE_FLAG_B=disabled
"""
    env_file.write_text(env_content)

    script = """
import os

print("Configuration (showing override precedence):")
print(f"  APP_ENV: {os.getenv('APP_ENV')}")
print(f"  DATABASE_URL: {os.getenv('DATABASE_URL')}")
print(f"  LOG_LEVEL: {os.getenv('LOG_LEVEL')}")
print(f"  FEATURE_FLAG_A: {os.getenv('FEATURE_FLAG_A')}")
print(f"  FEATURE_FLAG_B: {os.getenv('FEATURE_FLAG_B')}")
print(f"  ONLY_IN_DICT: {os.getenv('ONLY_IN_DICT')}")
"""

    print("\nBase config from .env file:")
    print("  APP_ENV=production")
    print("  DATABASE_URL=postgresql://prod-db.example.com/proddb")
    print("  LOG_LEVEL=info")
    print("  FEATURE_FLAG_A=enabled")
    print("  FEATURE_FLAG_B=disabled")
    print()
    print("Overrides from env_vars dict:")
    print("  APP_ENV=development  (overrides .env)")
    print("  DATABASE_URL=postgresql://localhost/devdb  (overrides .env)")
    print("  FEATURE_FLAG_B=enabled  (overrides .env)")
    print("  ONLY_IN_DICT=yes  (new variable)")
    print()

    result = _exec_with_dependencies_sync(
        script_content=script,
        script_path=None,
        python_version="3.13",
        dependencies=[],
        args=None,
        timeout_seconds=30,
        env_file=env_file,
        env_vars={
            "APP_ENV": "development",  # Override from .env
            "DATABASE_URL": "postgresql://localhost/devdb",  # Override from .env
            "FEATURE_FLAG_B": "enabled",  # Override from .env
            "ONLY_IN_DICT": "yes",  # New variable not in .env
        },
    )

    print("Actual output (env_vars overrides .env values):")
    print(result.stdout)
    print(f"Exit code: {result.exit_code}")

    # Clean up
    env_file.unlink()
    print()


def example_4_with_dependencies():
    """Example 4: Using environment variables with external dependencies."""
    print("=" * 70)
    print("Example 4: Environment Variables with Dependencies")
    print("=" * 70)

    script = """
import os
import requests

# Use environment variable for API endpoint
api_base_url = os.getenv('API_BASE_URL', 'https://api.github.com')
endpoint = f"{api_base_url}/zen"

print(f"Fetching from: {endpoint}")

try:
    response = requests.get(endpoint, timeout=5)
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text}")
except Exception as e:
    print(f"Error: {e}")
"""

    result = _exec_with_dependencies_sync(
        script_content=script,
        script_path=None,
        python_version="3.13",
        dependencies=["requests"],
        args=None,
        timeout_seconds=30,
        env_vars={"API_BASE_URL": "https://api.github.com"},
    )

    print("Output:")
    print(result.stdout)
    if result.stderr:
        print("Errors:")
        print(result.stderr)
    print(f"Exit code: {result.exit_code}")
    print()


def main():
    """Run all examples."""
    print("\n" + "=" * 70)
    print("PYTHON MCP SERVER - ENVIRONMENT VARIABLES DEMO")
    print("=" * 70 + "\n")

    try:
        example_1_env_vars_dict()
        example_2_env_file()
        example_3_combined_with_override()
        example_4_with_dependencies()

        print("=" * 70)
        print("All examples completed successfully! ✅")
        print("=" * 70)

    except Exception as e:
        print(f"\n❌ Error running examples: {e}")
        import traceback

        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
