# /// script
# requires-python = ">=3.12"
# ///
"""
Clean Jupyter .ipynb outputs by removing binary and image payloads while preserving text and error outputs.

Usage:
    uv run --script scripts/clean_ipynb_outputs.py <notebook.ipynb>

This writes a temporary cleaned notebook file next to the original and prints its path.

Options:
    --drop-html      Also remove text/html outputs (keeps plain text and errors only)
    --inplace        Overwrite the original notebook instead of writing a temp file

Notes:
- Preserves output_type 'stream' (stdout/stderr text) and 'error'.
- For 'execute_result' and 'display_data', retains textual MIME types (e.g., text/plain, text/markdown),
  and removes image/*, video/*, audio/*, application/pdf, application/vnd.*, and application/octet-stream.
- If --drop-html is set, text/html is removed as well to further reduce token usage.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

# MIME types considered binary or image-like and removed from output data
BINARY_MIME_PREFIXES: list[str] = [
    "image/",
    "video/",
    "audio/",
]
BINARY_MIME_EXACT: set[str] = {
    "application/pdf",
    "application/octet-stream",
}
BINARY_MIME_PREFIXES_APPLICATION: list[str] = [
    "application/vnd",
]

TEXTUAL_MIME_DEFAULTS: set[str] = {
    "text/plain",
    "text/markdown",
    "text/latex",
}


def _is_binary_mime(mime: str) -> bool:
    if mime in BINARY_MIME_EXACT:
        return True
    for p in BINARY_MIME_PREFIXES:
        if mime.startswith(p):
            return True
    for p in BINARY_MIME_PREFIXES_APPLICATION:
        if mime.startswith(p):
            return True
    return False


def clean_outputs(notebook: dict[str, Any], drop_html: bool = False) -> dict[str, Any]:
    """Return a copy of the notebook with binary/image outputs removed.

    - Keeps 'stream' and 'error' outputs intact.
    - For 'execute_result' and 'display_data', prunes non-text MIME payloads.
    """
    # Work on a shallow copy of top-level; we will replace cell outputs in place
    nb = notebook.copy()
    cells: list[dict[str, Any]] = nb.get("cells", [])
    for cell in cells:
        if cell.get("cell_type") != "code":
            continue
        outputs: list[dict[str, Any]] = cell.get("outputs", [])
        cleaned_outputs: list[dict[str, Any]] = []
        for out in outputs:
            otype: str = out.get("output_type", "")
            if otype in {"stream", "error"}:
                # Keep text streams and errors as-is
                cleaned_outputs.append(out)
                continue
            if otype in {"execute_result", "display_data"}:
                data: dict[str, Any] = out.get("data", {})
                if not isinstance(data, dict):
                    # Unexpected structure; keep output but continue
                    cleaned_outputs.append(out)
                    continue
                keep_data: dict[str, Any] = {}
                for mime, payload in list(data.items()):
                    if drop_html and mime == "text/html":
                        continue
                    if _is_binary_mime(mime):
                        # Drop binary/image payloads
                        continue
                    # Keep textual formats and anything not flagged as binary
                    keep_data[mime] = payload
                # If nothing textual remains, drop this output entirely
                if keep_data:
                    new_out = out.copy()
                    new_out["data"] = keep_data
                    cleaned_outputs.append(new_out)
                # else: omit this output
                continue
            # Unknown output types: pass through but attempt to scrub 'data' if present
            data = out.get("data")
            if isinstance(data, dict):
                keep_data = {
                    k: v
                    for k, v in data.items()
                    if not _is_binary_mime(k) and (not drop_html or k != "text/html")
                }
                new_out = out.copy()
                new_out["data"] = keep_data
                cleaned_outputs.append(new_out)
            else:
                cleaned_outputs.append(out)
        cell["outputs"] = cleaned_outputs
    nb["cells"] = cells
    return nb


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean binary/image outputs from a Jupyter notebook")
    parser.add_argument("input", type=str, help="Path to input .ipynb notebook")
    parser.add_argument(
        "--drop-html",
        action="store_true",
        help="Also remove text/html outputs (keeps plain text and errors only)",
    )
    parser.add_argument(
        "--inplace",
        action="store_true",
        help="Overwrite the original notebook instead of writing a temp file",
    )
    args = parser.parse_args()

    in_path = Path(args.input)
    if in_path.suffix.lower() != ".ipynb":
        raise ValueError("Input file must have .ipynb extension")
    raw = in_path.read_text(encoding="utf-8")
    notebook = json.loads(raw)

    cleaned = clean_outputs(notebook, drop_html=args.drop_html)

    if args.inplace:
        in_path.write_text(json.dumps(cleaned, ensure_ascii=False), encoding="utf-8")
        print(str(in_path))
        return 0

    # Write to a temp file in the same directory for easy discovery
    with tempfile.NamedTemporaryFile(
        mode="w",
        delete=False,
        dir=str(in_path.parent),
        suffix=".clean.ipynb",
        encoding="utf-8",
    ) as tmp:
        json.dump(cleaned, tmp, ensure_ascii=False)
        tmp_path = Path(tmp.name)
    print(str(tmp_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
