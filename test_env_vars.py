#!/usr/bin/env python3
"""
Test script to verify environment variable support in python-mcp-server.

This script tests:
1. Passing env_vars as a dictionary
2. Loading from a .env file
3. Proper override precedence (env_file < env_vars)
"""

import os
import sys
import tempfile
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent / "src"))

from python_mcp_server import (
    _build_process_env,
    _exec_with_dependencies_sync,
    _load_env_file,
)


def test_env_file_parsing():
    """Test .env file parsing."""
    print("=" * 60)
    print("TEST 1: .env file parsing")
    print("=" * 60)

    env_content = """
# This is a comment
DATABASE_URL=postgresql://localhost/testdb
API_KEY="secret123"
DEBUG=true

# Another comment
EMPTY_LINE_ABOVE=yes
QUOTED='single quotes work too'
NO_QUOTES=also_work
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
        f.write(env_content)
        env_file = Path(f.name)

    try:
        env_vars = _load_env_file(env_file)
        print(f"Loaded {len(env_vars)} variables:")
        for key, value in sorted(env_vars.items()):
            print(f"  {key} = {value}")

        # Verify parsing
        assert env_vars["DATABASE_URL"] == "postgresql://localhost/testdb"
        assert env_vars["API_KEY"] == "secret123"
        assert env_vars["DEBUG"] == "true"
        assert env_vars["QUOTED"] == "single quotes work too"
        assert env_vars["NO_QUOTES"] == "also_work"

        print("✅ PASSED: .env file parsing works correctly\n")
    finally:
        env_file.unlink()


def test_build_process_env():
    """Test environment building with different sources."""
    print("=" * 60)
    print("TEST 2: Environment building and override precedence")
    print("=" * 60)

    # Create a test .env file
    env_content = """
FROM_FILE=file_value
OVERRIDE_TEST=from_file
KEEP_THIS=file_only
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
        f.write(env_content)
        env_file = Path(f.name)

    try:
        # Test with just env_file
        env1 = _build_process_env(env_file=env_file)
        print("With env_file only:")
        print(f"  FROM_FILE = {env1.get('FROM_FILE')}")
        print(f"  OVERRIDE_TEST = {env1.get('OVERRIDE_TEST')}")
        assert env1["FROM_FILE"] == "file_value"
        assert env1["OVERRIDE_TEST"] == "from_file"

        # Test with env_vars dict overriding file
        env2 = _build_process_env(
            env_file=env_file,
            env_vars={"OVERRIDE_TEST": "from_dict", "NEW_VAR": "dict_only"},
        )
        print("\nWith env_file + env_vars (dict should override):")
        print(f"  FROM_FILE = {env2.get('FROM_FILE')}")
        print(f"  OVERRIDE_TEST = {env2.get('OVERRIDE_TEST')}")
        print(f"  NEW_VAR = {env2.get('NEW_VAR')}")
        print(f"  KEEP_THIS = {env2.get('KEEP_THIS')}")

        assert env2["FROM_FILE"] == "file_value"  # from file
        assert env2["OVERRIDE_TEST"] == "from_dict"  # dict overrides file
        assert env2["NEW_VAR"] == "dict_only"  # only in dict
        assert env2["KEEP_THIS"] == "file_only"  # only in file

        # Test with just env_vars
        env3 = _build_process_env(env_vars={"DICT_ONLY": "yes"})
        print("\nWith env_vars dict only:")
        print(f"  DICT_ONLY = {env3.get('DICT_ONLY')}")
        assert env3["DICT_ONLY"] == "yes"

        # Verify inherited environment is present
        assert "PATH" in env3  # Should inherit from os.environ

        print("✅ PASSED: Environment building and precedence work correctly\n")
    finally:
        env_file.unlink()


def test_script_execution_with_env():
    """Test actual script execution with environment variables."""
    print("=" * 60)
    print("TEST 3: Script execution with environment variables")
    print("=" * 60)

    # Test script that prints environment variables
    test_script = """
import os
print(f"CUSTOM_VAR={os.getenv('CUSTOM_VAR', 'NOT_SET')}")
print(f"ANOTHER_VAR={os.getenv('ANOTHER_VAR', 'NOT_SET')}")
print(f"FROM_FILE={os.getenv('FROM_FILE', 'NOT_SET')}")
"""

    # Create .env file
    env_content = "FROM_FILE=loaded_from_env_file\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
        f.write(env_content)
        env_file = Path(f.name)

    try:
        # Test with env_vars dict
        print("Running with env_vars dict...")
        result1 = _exec_with_dependencies_sync(
            script_content=test_script,
            script_path=None,
            python_version="3.13",
            dependencies=[],
            args=None,
            timeout_seconds=300,
            env_vars={"CUSTOM_VAR": "hello", "ANOTHER_VAR": "world"},
        )

        print("Output:")
        print(result1.stdout)
        assert "CUSTOM_VAR=hello" in result1.stdout
        assert "ANOTHER_VAR=world" in result1.stdout
        assert result1.exit_code == 0

        # Test with env_file
        print("Running with env_file...")
        result2 = _exec_with_dependencies_sync(
            script_content=test_script,
            script_path=None,
            python_version="3.13",
            dependencies=[],
            args=None,
            timeout_seconds=300,
            env_file=env_file,
        )

        print("Output:")
        print(result2.stdout)
        assert "FROM_FILE=loaded_from_env_file" in result2.stdout
        assert result2.exit_code == 0

        # Test with both (dict should override file)
        print("Running with both env_file and env_vars...")
        result3 = _exec_with_dependencies_sync(
            script_content=test_script,
            script_path=None,
            python_version="3.13",
            dependencies=[],
            args=None,
            timeout_seconds=300,
            env_file=env_file,
            env_vars={"CUSTOM_VAR": "override", "FROM_FILE": "overridden"},
        )

        print("Output:")
        print(result3.stdout)
        assert "CUSTOM_VAR=override" in result3.stdout
        assert "FROM_FILE=overridden" in result3.stdout  # dict overrides file
        assert result3.exit_code == 0

        print("✅ PASSED: Script execution with environment variables works\n")
    finally:
        env_file.unlink()


def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("ENVIRONMENT VARIABLE SUPPORT TESTS")
    print("=" * 60 + "\n")

    try:
        test_env_file_parsing()
        test_build_process_env()
        test_script_execution_with_env()

        print("=" * 60)
        print("ALL TESTS PASSED ✅")
        print("=" * 60)
        return 0
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
