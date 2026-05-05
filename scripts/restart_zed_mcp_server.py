#!/usr/bin/env python3
# pyright: reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnusedCallResult=false, reportUnusedImport=false, reportUnusedVariable=false, reportUnusedParameter=false, reportPrivateUsage=false, reportDeprecated=false, reportOptionalMemberAccess=false, reportUnnecessaryIsInstance=false, reportUnreachable=false, reportImplicitStringConcatenation=false, reportAssignmentType=false, reportMissingImports=false, reportMissingTypeArgument=false, reportArgumentType=false, reportUnannotatedClassAttribute=false, reportImplicitRelativeImport=false
"""
Restart a named Zed MCP context server by editing Zed's settings file.

Usage:
    python scripts/restart_zed_mcp_server.py python-mcp
    python scripts/restart_zed_mcp_server.py repo-summarize-mcp --delay 5

Toggles `context_servers.<name>.enabled` to false, waits briefly, then toggles
it back to true and refreshes/adds `ZED_MCP_RESTART_TOKEN` under that server's
`env` block. A timestamped backup is created unless `--no-backup` is supplied.
"""

from __future__ import annotations

import argparse
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from zed_mcp_config_utils import (
    DEFAULT_SETTINGS,
    find_server_entry,
    replace_server_block,
    set_or_add_env_token,
    set_top_level_bool,
)


def write_server_enabled(settings: Path, name: str, enabled: bool, token: str | None = None) -> None:
    text = settings.read_text(encoding="utf-8")
    server = find_server_entry(text, name)
    block = set_top_level_bool(server.value_text, "enabled", enabled)
    if enabled and token:
        block = set_or_add_env_token(block, "ZED_MCP_RESTART_TOKEN", token)
    settings.write_text(replace_server_block(text, name, block), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", help="Name under settings.context_servers, e.g. python-mcp")
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS)
    parser.add_argument("--delay", type=float, default=3.0, help="Seconds to leave the server disabled")
    parser.add_argument("--no-backup", action="store_true", help="Do not create a timestamped settings backup")
    args = parser.parse_args()

    settings: Path = args.settings.expanduser()
    if not settings.is_file():
        raise FileNotFoundError(settings)

    backup: Path | None = None
    if not args.no_backup:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = settings.with_name(f"{settings.name}.bak-restart-{args.name}-{stamp}")
        shutil.copy2(str(settings), str(backup))

    write_server_enabled(settings, args.name, False)
    print(f"Disabled {args.name!r}; waiting {args.delay:g}s for Zed to stop it...")
    time.sleep(args.delay)

    token = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    write_server_enabled(settings, args.name, True, token=token)
    print(f"Re-enabled {args.name!r} with ZED_MCP_RESTART_TOKEN={token}")
    if backup:
        print(f"Backup: {backup}")


if __name__ == "__main__":
    main()
