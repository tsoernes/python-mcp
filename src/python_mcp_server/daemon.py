"""Authenticated loopback Streamable HTTP daemon for shared Codex clients."""

from __future__ import annotations

import hmac
import ipaddress
import os
import re
import stat
from pathlib import Path
from typing import override

from fastmcp.server.auth import AccessToken, TokenVerifier

from . import logger, mcp
from .smart_async import initialize_state

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8794
DEFAULT_PATH = "/mcp"
DEFAULT_TOKEN_FILE = Path.home() / ".config" / "shared-mcp" / "python-mcp.token"
DEFAULT_STATE_DIR = Path.home() / ".local" / "state" / "python-mcp-server"


class FileTokenVerifier(TokenVerifier):
    """Verify a local Codex bearer token loaded from a private file."""

    def __init__(self, token_file: Path, *, base_url: str) -> None:
        super().__init__(base_url=base_url)
        token_stat = token_file.stat()
        if not stat.S_ISREG(token_stat.st_mode):
            raise ValueError("The MCP bearer-token path must be a regular file")
        if token_stat.st_uid != os.getuid():
            raise PermissionError("The MCP bearer-token file must belong to this user")
        if stat.S_IMODE(token_stat.st_mode) & 0o077:
            raise PermissionError("The MCP bearer-token file must have mode 0600")

        token = token_file.read_text(encoding="ascii").strip()
        if re.fullmatch(r"[0-9a-f]{64}", token) is None:
            raise ValueError(
                "The MCP bearer-token file must contain 64 lowercase hex characters"
            )
        self._token: str = token

    @override
    async def verify_token(self, token: str) -> AccessToken | None:
        if not hmac.compare_digest(token, self._token):
            return None
        return AccessToken(token=token, client_id="local-codex", scopes=[])


def read_server_settings() -> tuple[str, int, str, Path, Path]:
    host = os.getenv("PYTHON_MCP_HTTP_HOST", DEFAULT_HOST)
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("PYTHON_MCP_HTTP_HOST must be a loopback IP address") from exc
    if not address.is_loopback:
        raise ValueError("The Python MCP daemon may only bind to a loopback address")

    try:
        port = int(os.getenv("PYTHON_MCP_HTTP_PORT", str(DEFAULT_PORT)))
    except ValueError as exc:
        raise ValueError("PYTHON_MCP_HTTP_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError("PYTHON_MCP_HTTP_PORT must be between 1 and 65535")

    path = os.getenv("PYTHON_MCP_HTTP_PATH", DEFAULT_PATH)
    if not path.startswith("/") or path.startswith("//"):
        raise ValueError("PYTHON_MCP_HTTP_PATH must be an absolute URL path")

    token_file = Path(os.getenv("PYTHON_MCP_HTTP_TOKEN_FILE", str(DEFAULT_TOKEN_FILE)))
    state_dir = Path(os.getenv("PYTHON_MCP_STATE_DIR", str(DEFAULT_STATE_DIR)))
    return host, port, path, token_file, state_dir


def main() -> None:
    """Start the single-process daemon used by local Codex MCP clients."""
    host, port, path, token_file, state_dir = read_server_settings()
    initialize_state(state_dir)
    mcp.auth = FileTokenVerifier(
        token_file,
        base_url=f"http://{host}:{port}",
    )
    logger.info(
        "Starting shared Python MCP daemon on http://%s:%d%s",
        host,
        port,
        path,
    )
    mcp.run(
        transport="streamable-http",
        host=host,
        port=port,
        path=path,
        show_banner=False,
        host_origin_protection="auto",
        stateless_http=False,
    )


if __name__ == "__main__":
    main()
