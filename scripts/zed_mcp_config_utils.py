#!/usr/bin/env python3
# pyright: reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnusedCallResult=false, reportUnusedImport=false, reportUnusedVariable=false, reportUnusedParameter=false, reportPrivateUsage=false, reportDeprecated=false, reportOptionalMemberAccess=false, reportUnnecessaryIsInstance=false, reportUnreachable=false, reportImplicitStringConcatenation=false, reportAssignmentType=false, reportMissingImports=false, reportMissingTypeArgument=false, reportArgumentType=false, reportUnannotatedClassAttribute=false
"""Small JSONC-tolerant helpers for reading/editing Zed MCP config blocks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

DEFAULT_SETTINGS = Path.home() / ".config" / "zed" / "settings.json"
ValueKind = Literal["object", "array", "string", "literal"]


@dataclass(frozen=True)
class Entry:
    key: str
    value_start: int
    value_end: int
    value_kind: ValueKind
    value_text: str


def json_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def parse_string(text: str, start: int) -> tuple[str, int]:
    if start >= len(text) or text[start] != '"':
        raise ValueError(f"Expected string at offset {start}")
    chars: list[str] = []
    idx = start + 1
    while idx < len(text):
        ch = text[idx]
        if ch == '"':
            return "".join(chars), idx + 1
        if ch == "\\":
            if idx + 1 >= len(text):
                raise ValueError("Unterminated escape sequence")
            nxt = text[idx + 1]
            if nxt == "u" and idx + 5 < len(text):
                chars.append(chr(int(text[idx + 2 : idx + 6], 16)))
                idx += 6
                continue
            chars.append({"n": "\n", "r": "\r", "t": "\t"}.get(nxt, nxt))
            idx += 2
            continue
        chars.append(ch)
        idx += 1
    raise ValueError("Unterminated string")


def skip_ws_comments(text: str, idx: int) -> int:
    while idx < len(text):
        if text[idx].isspace():
            idx += 1
            continue
        if text.startswith("//", idx):
            nl = text.find("\n", idx + 2)
            idx = len(text) if nl == -1 else nl + 1
            continue
        if text.startswith("/*", idx):
            end = text.find("*/", idx + 2)
            idx = len(text) if end == -1 else end + 2
            continue
        return idx
    return idx


def find_matching(text: str, start: int, open_ch: str, close_ch: str) -> int:
    depth = 0
    idx = start
    while idx < len(text):
        if text.startswith("//", idx):
            nl = text.find("\n", idx + 2)
            idx = len(text) if nl == -1 else nl + 1
            continue
        if text.startswith("/*", idx):
            end = text.find("*/", idx + 2)
            idx = len(text) if end == -1 else end + 2
            continue
        ch = text[idx]
        if ch == '"':
            _, idx = parse_string(text, idx)
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return idx
        idx += 1
    raise ValueError(f"No matching {close_ch!r} for offset {start}")


def value_end_and_kind(text: str, start: int) -> tuple[int, ValueKind]:
    ch = text[start]
    if ch == "{":
        return find_matching(text, start, "{", "}") + 1, "object"
    if ch == "[":
        return find_matching(text, start, "[", "]") + 1, "array"
    if ch == '"':
        _, end = parse_string(text, start)
        return end, "string"
    idx = start
    while idx < len(text) and text[idx] not in ",}\n\r":
        idx += 1
    return idx, "literal"


def iter_object_entries(text: str, object_start: int, object_end: int) -> list[Entry]:
    entries: list[Entry] = []
    idx = skip_ws_comments(text, object_start + 1)
    while idx < object_end:
        if text[idx] == "}":
            break
        key, after_key = parse_string(text, idx)
        idx = skip_ws_comments(text, after_key)
        if idx >= len(text) or text[idx] != ":":
            raise ValueError(f"Expected ':' after key {key!r}")
        value_start = skip_ws_comments(text, idx + 1)
        value_end, kind = value_end_and_kind(text, value_start)
        entries.append(Entry(key, value_start, value_end, kind, text[value_start:value_end]))
        idx = skip_ws_comments(text, value_end)
        if idx < object_end and text[idx] == ",":
            idx = skip_ws_comments(text, idx + 1)
    return entries


def find_context_servers(text: str) -> Entry:
    root_start = skip_ws_comments(text, 0)
    if root_start >= len(text) or text[root_start] != "{":
        raise ValueError("Settings file must start with an object")
    root_end = find_matching(text, root_start, "{", "}")
    for entry in iter_object_entries(text, root_start, root_end):
        if entry.key == "context_servers":
            if entry.value_kind != "object":
                raise ValueError("context_servers is not an object")
            return entry
    raise KeyError("context_servers not found")


def context_server_entries(text: str) -> list[Entry]:
    context = find_context_servers(text)
    return iter_object_entries(text, context.value_start, context.value_end - 1)


def find_server_entry(text: str, name: str) -> Entry:
    entries = context_server_entries(text)
    for entry in entries:
        if entry.key == name:
            if entry.value_kind != "object":
                raise ValueError(f"Server {name!r} is not an object")
            return entry
    available = ", ".join(entry.key for entry in entries)
    raise KeyError(f"Server {name!r} not found. Available: {available}")


def parse_bool_from_block(block: str, key: str) -> bool | None:
    start = skip_ws_comments(block, 0)
    end = find_matching(block, start, "{", "}")
    for entry in iter_object_entries(block, start, end):
        if entry.key == key:
            value = entry.value_text.strip().lower()
            if value.startswith("true"):
                return True
            if value.startswith("false"):
                return False
    return None


def parse_string_from_block(block: str, key: str) -> str | None:
    start = skip_ws_comments(block, 0)
    end = find_matching(block, start, "{", "}")
    for entry in iter_object_entries(block, start, end):
        if entry.key == key and entry.value_kind == "string":
            value, _ = parse_string(entry.value_text, 0)
            return value
    return None


def line_indent_before(text: str, offset: int) -> str:
    line_start = text.rfind("\n", 0, offset) + 1
    idx = line_start
    while idx < len(text) and text[idx] in " \t":
        idx += 1
    return text[line_start:idx]


def set_top_level_bool(block: str, key: str, value: bool) -> str:
    desired = "true" if value else "false"
    start = skip_ws_comments(block, 0)
    end = find_matching(block, start, "{", "}")
    entries = iter_object_entries(block, start, end)
    for entry in entries:
        if entry.key == key:
            return block[: entry.value_start] + desired + block[entry.value_end :]
    indent = "      "
    if entries:
        indent = line_indent_before(block, entries[0].value_start - len(entries[0].key) - 3)
    return block[: start + 1] + f"\n{indent}\"{key}\": {desired}," + block[start + 1 :]


def set_or_add_env_token(block: str, token_name: str, token_value: str) -> str:
    start = skip_ws_comments(block, 0)
    end = find_matching(block, start, "{", "}")
    entries = iter_object_entries(block, start, end)
    env_entry = next((entry for entry in entries if entry.key == "env"), None)
    if env_entry is None or env_entry.value_kind != "object":
        indent = "      "
        if entries:
            indent = line_indent_before(block, entries[-1].value_start - len(entries[-1].key) - 3)
        env_text = f"\n{indent}\"env\": {{\n{indent}  \"{token_name}\": {json_string(token_value)},\n{indent}}},"
        return block[:end] + env_text + block[end:]

    env_block = env_entry.value_text
    env_start = skip_ws_comments(env_block, 0)
    env_end = find_matching(env_block, env_start, "{", "}")
    env_entries = iter_object_entries(env_block, env_start, env_end)
    for entry in env_entries:
        if entry.key == token_name:
            new_env = env_block[: entry.value_start] + json_string(token_value) + env_block[entry.value_end :]
            return block[: env_entry.value_start] + new_env + block[env_entry.value_end :]
    indent = "        "
    if env_entries:
        indent = line_indent_before(env_block, env_entries[-1].value_start - len(env_entries[-1].key) - 3)
    insert_at = env_end
    while insert_at > env_start and env_block[insert_at - 1].isspace():
        insert_at -= 1
    closing_ws = env_block[insert_at:env_end]
    inserted = f"\n{indent}\"{token_name}\": {json_string(token_value)},"
    new_env = env_block[:insert_at] + inserted + closing_ws + env_block[env_end:]
    return block[: env_entry.value_start] + new_env + block[env_entry.value_end :]


def replace_server_block(text: str, name: str, new_block: str) -> str:
    server = find_server_entry(text, name)
    return text[: server.value_start] + new_block + text[server.value_end :]
