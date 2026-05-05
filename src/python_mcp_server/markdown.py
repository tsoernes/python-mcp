# pyright: reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnusedCallResult=false, reportUnusedImport=false, reportUnusedVariable=false, reportUnusedParameter=false, reportPrivateUsage=false, reportDeprecated=false, reportOptionalMemberAccess=false, reportUnnecessaryIsInstance=false, reportUnreachable=false, reportImplicitStringConcatenation=false, reportAssignmentType=false, reportMissingImports=false, reportMissingTypeArgument=false, reportMissingModuleSource=false, reportArgumentType=false, reportUnannotatedClassAttribute=false
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


_SCRIPT_RESULT_KEYS = {"stdout", "stderr", "exit_code", "execution_strategy", "elapsed_seconds"}


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


def _fence(text: str, language: str = "text") -> str:
    """Return text in a Markdown fence, extending the fence if text contains backticks."""
    fence = "````" if "```" in text else "```"
    return f"{fence}{language}\n{text}\n{fence}"


def _looks_like_script_result(data: dict[str, Any]) -> bool:
    return (
        _SCRIPT_RESULT_KEYS.issubset(data.keys())
        and isinstance(data.get("stdout"), str)
        and isinstance(data.get("stderr"), str)
    )


def _render_script_result(data: dict[str, Any], heading_level: int = 2) -> str:
    """Render Python execution results with readable stdout/stderr sections."""
    h = "#" * heading_level
    lines = [
        f"- exit_code: `{data.get('exit_code')}`",
        f"- execution_strategy: `{data.get('execution_strategy')}`",
        f"- elapsed_seconds: `{data.get('elapsed_seconds')}`",
    ]

    if "python_version_used" in data:
        lines.append(f"- python_version_used: `{data.get('python_version_used')}`")
    if "resolved_dependencies" in data:
        deps = data.get("resolved_dependencies") or []
        if deps:
            lines.append("- resolved_dependencies: " + ", ".join(f"`{dep}`" for dep in deps))
        else:
            lines.append("- resolved_dependencies: none")

    stdout = data.get("stdout", "")
    stderr = data.get("stderr", "")

    sections = [f"{h} Summary", "\n".join(lines), f"{h} stdout", _fence(stdout or "", "text")]
    if stderr:
        sections.extend([f"{h} stderr", _fence(stderr, "text")])
    return "\n\n".join(sections)


def _looks_like_job_status(data: dict[str, Any]) -> bool:
    return isinstance(data.get("job"), dict)


def _render_job_status(data: dict[str, Any]) -> str:
    """Render py_job_status output naturally, including nested script results."""
    job = data.get("job")
    if not isinstance(job, dict):
        return _fence(json.dumps(data, indent=2, ensure_ascii=False, default=str), "json")

    lines = [
        f"- id: `{job.get('id')}`",
        f"- status: `{job.get('status')}`",
    ]
    if job.get("label"):
        lines.append(f"- label: `{job.get('label')}`")
    if job.get("error"):
        lines.append(f"- error: `{job.get('error')}`")
    if job.get("progress"):
        lines.append(f"- progress: `{job.get('progress')}`")

    sections = ["## Job", "\n".join(lines)]
    result = job.get("result")
    if isinstance(result, dict) and _looks_like_script_result(result):
        sections.extend(["## Result", _render_script_result(result, heading_level=3)])
    elif result is not None:
        sections.extend(["## Result", _fence(json.dumps(result, indent=2, ensure_ascii=False, default=str), "json")])

    if data.get("incremental"):
        sections.append("_Incremental output returned._")
    return "\n\n".join(sections)


def _looks_like_script_view(data: dict[str, Any]) -> bool:
    return isinstance(data.get("source"), str) and isinstance(data.get("path"), str)


def _render_script_view(data: dict[str, Any]) -> str:
    lines = [
        f"- name: `{data.get('name')}`",
        f"- kind: `{data.get('kind')}`",
        f"- path: `{data.get('path')}`",
    ]
    if data.get("docstring"):
        lines.append(f"- docstring: {data.get('docstring')}")
    if data.get("header"):
        lines.append(f"- header: `{data.get('header')}`")

    sections = ["## Script", "\n".join(lines), "## Source", _fence(str(data.get("source", "")), "python")]
    metadata = data.get("metadata")
    if metadata:
        sections.extend(["## Metadata", _fence(json.dumps(metadata, indent=2, ensure_ascii=False, default=str), "json")])
    return "\n\n".join(sections)


def render_markdown_response(value: Any, title: str | None = None) -> str:
    """Render a tool response as markdown.

    Script execution results are rendered with natural stdout/stderr sections so
    large textual output stays readable instead of becoming JSON-escaped.
    Other structured responses still use fenced JSON.
    """
    data = _json_ready(value)

    if isinstance(data, str):
        if "\n" in data:
            body = _fence(data, "text")
        else:
            body = data
    elif isinstance(data, dict) and _looks_like_script_result(data):
        body = _render_script_result(data)
    elif isinstance(data, dict) and _looks_like_job_status(data):
        body = _render_job_status(data)
    elif isinstance(data, dict) and _looks_like_script_view(data):
        body = _render_script_view(data)
    else:
        body = _fence(json.dumps(data, indent=2, ensure_ascii=False, default=str), "json")

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
