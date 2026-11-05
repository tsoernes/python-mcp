"""Tests for the shared loopback daemon's local bearer authentication."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from python_mcp_server.daemon import FileTokenVerifier, read_server_settings


class FileTokenVerifierTests(unittest.IsolatedAsyncioTestCase):
    async def test_accepts_only_the_private_file_token(self) -> None:
        token = "0123456789abcdef" * 4
        with tempfile.TemporaryDirectory() as temp_dir:
            token_file = Path(temp_dir) / "token"
            _ = token_file.write_text(token, encoding="ascii")
            _ = token_file.chmod(0o600)
            verifier = FileTokenVerifier(
                token_file,
                base_url="http://127.0.0.1:8794",
            )

            accepted = await verifier.verify_token(token)
            rejected = await verifier.verify_token("invalid")

        self.assertIsNotNone(accepted)
        self.assertEqual(accepted.client_id if accepted else None, "local-codex")
        self.assertIsNone(rejected)

    async def test_rejects_token_files_readable_by_group_or_others(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            token_file = Path(temp_dir) / "token"
            _ = token_file.write_text("0123456789abcdef" * 4, encoding="ascii")
            _ = token_file.chmod(0o640)
            with self.assertRaises(PermissionError):
                _ = FileTokenVerifier(token_file, base_url="http://127.0.0.1:8794")


class DaemonSettingsTests(unittest.TestCase):
    def test_daemon_rejects_non_loopback_bind_addresses(self) -> None:
        with patch.dict(os.environ, {"PYTHON_MCP_HTTP_HOST": "0.0.0.0"}):
            with self.assertRaisesRegex(ValueError, "loopback"):
                _ = read_server_settings()

    def test_daemon_defaults_to_local_single_endpoint(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            for name in (
                "PYTHON_MCP_HTTP_HOST",
                "PYTHON_MCP_HTTP_PORT",
                "PYTHON_MCP_HTTP_PATH",
                "PYTHON_MCP_HTTP_TOKEN_FILE",
                "PYTHON_MCP_STATE_DIR",
            ):
                _ = os.environ.pop(name, None)
            host, port, path, _, _ = read_server_settings()

        self.assertEqual((host, port, path), ("127.0.0.1", 8794, "/mcp"))


if __name__ == "__main__":
    _ = unittest.main()
