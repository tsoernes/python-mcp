# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
"""Readable identifier helpers for user-visible MCP runtime entities."""

from __future__ import annotations

import secrets
from collections.abc import Collection

import randomname


def generate_readable_id(
    existing_ids: Collection[str], *, max_plain_attempts: int = 20
) -> str:
    """
    Generate a Docker-style readable identifier that is unique in ``existing_ids``.

    The plain ID is produced by randomname (for example ``"quiet-river"``).
    If repeated collisions occur, a short random hex suffix is appended while
    preserving the readable prefix.
    """
    existing = set(existing_ids)

    for _ in range(max_plain_attempts):
        candidate = randomname.get_name()
        if candidate not in existing:
            return candidate

    while True:
        candidate = f"{randomname.get_name()}-{secrets.token_hex(3)}"
        if candidate not in existing:
            return candidate
