#!/usr/bin/python3
"""Print the Codex HTTP authorization header from the local private token file."""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path


def main() -> int:
    token_file = Path.home() / ".config" / "shared-mcp" / "python-mcp.token"
    try:
        token_stat = token_file.stat()
        if not stat.S_ISREG(token_stat.st_mode):
            raise ValueError("token path is not a regular file")
        if token_stat.st_uid != os.getuid():
            raise PermissionError("token file is not owned by the current user")
        if stat.S_IMODE(token_stat.st_mode) & 0o077:
            raise PermissionError("token file must have mode 0600")
        token = token_file.read_text(encoding="ascii").strip()
        if re.fullmatch(r"[0-9a-f]{64}", token) is None:
            raise ValueError("token file has an invalid format")
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"python-mcp auth helper: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({"Authorization": f"Bearer {token}"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
