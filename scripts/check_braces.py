#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable

# Simple brace/parenthesis/bracket balance checker.
# It:
# - Tracks (), {}, [] outside of string literals.
# - Ignores characters inside single-line Python comments (# ...).
# - Handles simple string literals (single, double, triple quotes) without full escape handling.
# - Reports first mismatch or first unclosed opener.
#
# Limitations:
# - Does not fully parse Python; e.g. unmatched quotes can degrade accuracy.
# - Does not skip braces in multi-line strings if quotes are unmatched.
#
# Exit codes:
#   0: OK
#   1: Unmatched closing delimiter
#   2: Unclosed opening delimiter
#   3: File read error / invalid arguments
#
# Usage examples:
#   ./scripts/check_braces.py zed/crates/zed/src/main.rs
#   git ls-files '*.rs' | xargs ./scripts/check_braces.py
#
PAIRS = {")": "(", "]": "[", "}": "{"}
OPENERS = set(PAIRS.values())
CLOSERS = set(PAIRS.keys())


def iter_sources(paths: list[str]) -> Iterable[tuple[str, str | None]]:
    if not paths:
        try:
            yield "<stdin>", sys.stdin.read()
        except Exception as e:  # noqa: BLE001
            print(f"READ_ERROR <stdin>: {e}", file=sys.stderr)
            yield "<stdin>", None
        return
    for p in paths:
        path = Path(p)
        try:
            data = path.read_text(encoding="utf-8", errors="replace")
            yield str(path), data
        except Exception as e:  # noqa: BLE001
            print(f"READ_ERROR {path}: {e}", file=sys.stderr)
            # Yield marker to allow exit code 3
            yield str(path), None
            continue


def check_balance(src: str, pairs: dict[str, str]) -> tuple[bool, list[str]]:
    OPENERS = set(pairs.values())
    CLOSERS = set(pairs.keys())
    stack: list[tuple[str, int, int]] = []
    in_string = False
    string_delim = ""
    string_start_line = 0
    string_start_col = 0
    errors: list[str] = []

    lines = src.splitlines()
    for line_no, line in enumerate(lines, 1):
        i = 0
        # Comments only apply when not currently in a string
        effective = line if in_string else line.split("#", 1)[0]

        while i < len(effective):
            ch = effective[i]

            # String handling
            if not in_string:
                if ch in ("'", '"'):
                    # Detect triple quotes
                    triple = effective[i : i + 3] == ch * 3
                    string_delim = ch * 3 if triple else ch
                    in_string = True
                    string_start_line = line_no
                    string_start_col = i + 1
                    i += 3 if triple else 1
                    continue
            else:
                if string_delim in ("'''", '"""'):
                    if effective[i : i + 3] == string_delim:
                        in_string = False
                        string_delim = ""
                        i += 3
                        continue
                    # Skip escaped character inside triple-quoted string
                    if effective[i] == "\\":
                        i += 2
                        continue
                    i += 1
                    continue
                else:
                    # Handle escapes in single/double-quoted strings
                    if ch == "\\":
                        i += 2
                        continue
                    if ch == string_delim:
                        in_string = False
                        string_delim = ""
                        i += 1
                        continue
                    i += 1
                    continue

            # Delimiter tracking outside strings
            if ch in OPENERS:
                stack.append((ch, line_no, i + 1))
            elif ch in CLOSERS:
                expected_open = pairs.get(ch)
                if not stack or stack[-1][0] != expected_open:
                    errors.append(f"Unmatched '{ch}' at {line_no}:{i + 1}")
                else:
                    stack.pop()
            i += 1

    if in_string:
        errors.append(
            f"Unclosed string starting at {string_start_line}:{string_start_col}"
        )

    if stack:
        for opener, lno, col in stack:
            errors.append(f"Unclosed '{opener}' opened at {lno}:{col}")

    return (len(errors) == 0), errors


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Check delimiter balance in source files."
    )
    parser.add_argument(
        "paths", nargs="*", help="Files to check (reads stdin if empty)"
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop after the first file with errors.",
    )
    parser.add_argument(
        "--pairs",
        default="()[]{}",
        help="Delimiter pairs as a flat string (default: ()[]{}). Example: '()[]{}<>'",
    )
    args = parser.parse_args(argv)

    # Parse pairs spec into closer->opener mapping
    spec = "".join(args.pairs.split())
    if len(spec) % 2 != 0:
        print(f"Invalid --pairs spec (odd length): {args.pairs}", file=sys.stderr)
        return 3
    pairs: dict[str, str] = {}
    for i in range(0, len(spec), 2):
        open_ch = spec[i]
        close_ch = spec[i + 1]
        pairs[close_ch] = open_ch

    any_unmatched = False
    any_unclosed = False
    read_error = False
    overall_ok = True

    for path, content in iter_sources(args.paths):
        if content is None:
            read_error = True
            print(f"ERR\t{path}\tREAD_ERROR")
            if args.fail_fast:
                break
            continue

        ok, errors = check_balance(content, pairs)
        if ok:
            print(f"OK\t{path}\tOK")
        else:
            overall_ok = False
            for msg in errors:
                print(f"ERR\t{path}\t{msg}")
                if msg.startswith("Unmatched"):
                    any_unmatched = True
                elif msg.startswith("Unclosed"):
                    any_unclosed = True
            if args.fail_fast:
                break

    if read_error:
        return 3
    if not overall_ok:
        if any_unmatched:
            return 1
        if any_unclosed:
            return 2
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        raise SystemExit(3)
