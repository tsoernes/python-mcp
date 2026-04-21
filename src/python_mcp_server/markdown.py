from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


def _json_ready(value: Any) -> Any:
    """Convert common Python objects into JSON-serializable data."""
    if hasattr(value, "model_dump") and callable(value.model_dump):
        return _json_ready(value.model_dump())
    if is_dataclass(value):
        return _json_ready(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_ready(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_ready(v) for v in value]
    return value


def render_markdown_response(value: Any, title: str | None = None) -> str:
    """Render a tool response as markdown, defaulting to fenced JSON."""
    data = _json_ready(value)

    if isinstance(data, str):
        if "\n" in data:
            body = f"```text\n{data}\n```"
        else:
            body = data
    else:
        body = f"```json\n{json.dumps(data, indent=2, ensure_ascii=False, default=str)}\n```"

    if title:
        return f"# {title}\n\n{body}"
    return body


def maybe_render_markdown(
    value: Any, markdown_output: bool = False, title: str | None = None
) -> Any:
    """Return markdown when requested, otherwise pass through the original value."""
    if not markdown_output:
        return value
    return render_markdown_response(value, title=title)
