#!/usr/bin/env python3
# pyright: reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnusedCallResult=false, reportUnusedImport=false, reportUnusedVariable=false, reportUnusedParameter=false, reportPrivateUsage=false, reportDeprecated=false, reportOptionalMemberAccess=false, reportUnnecessaryIsInstance=false, reportUnreachable=false, reportImplicitStringConcatenation=false, reportAssignmentType=false, reportMissingImports=false, reportMissingTypeArgument=false, reportArgumentType=false, reportUnannotatedClassAttribute=false, reportImplicitRelativeImport=false
"""
List MCP context servers configured in Zed and whether each is enabled.

Usage:
    python scripts/list_zed_mcp_servers.py
    python scripts/list_zed_mcp_servers.py --settings ~/.config/zed/settings.json --json

Reads Zed's top-level `context_servers` object. Missing `enabled` is displayed
as enabled, matching the usual Zed behavior for configured context servers.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from zed_mcp_config_utils import (
    DEFAULT_SETTINGS,
    context_server_entries,
    parse_bool_from_block,
    parse_string_from_block,
)


def list_servers(settings_path: Path) -> list[dict[str, str | bool]]:
    text = settings_path.expanduser().read_text(encoding="utf-8")
    servers: list[dict[str, str | bool]] = []
    for entry in context_server_entries(text):
        if entry.value_kind != "object":
            continue
        enabled_value = parse_bool_from_block(entry.value_text, "enabled")
        enabled = True if enabled_value is None else enabled_value
        command = parse_string_from_block(entry.value_text, "command") or ""
        source = parse_string_from_block(entry.value_text, "source") or "custom"
        servers.append({"name": entry.key, "enabled": enabled, "source": source, "command": command})
    return servers


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS)
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a table")
    args = parser.parse_args()

    servers = list_servers(args.settings)
    if args.json:
        print(json.dumps(servers, indent=2, ensure_ascii=False))
        return

    name_width = max([len("NAME"), *(len(str(server["name"])) for server in servers)])
    source_width = max([len("SOURCE"), *(len(str(server["source"])) for server in servers)])
    print(f"{'NAME':<{name_width}}  {'ENABLED':<7}  {'SOURCE':<{source_width}}  COMMAND")
    print(f"{'-' * name_width}  {'-' * 7}  {'-' * source_width}  {'-' * 7}")
    for server in servers:
        enabled = "yes" if server["enabled"] else "no"
        print(
            f"{str(server['name']):<{name_width}}  "
            f"{enabled:<7}  "
            f"{str(server['source']):<{source_width}}  "
            f"{server['command']}"
        )


if __name__ == "__main__":
    main()
