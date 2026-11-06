# pyright: reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportUnknownLambdaType=false, reportMissingParameterType=false, reportUnusedCallResult=false, reportUnusedFunction=false, reportUnusedParameter=false, reportDeprecated=false, reportOptionalMemberAccess=false, reportUnnecessaryComparison=false
"""Persistent, JSON-lines worker for the Python REPL stdio MCP entrypoint."""

from __future__ import annotations

import ast
import asyncio
import builtins
import contextlib
import inspect
import io
import json
import sys
import time
import traceback
from collections.abc import Awaitable, Iterator
from typing import Any, final, override


@final
class CappedText(io.TextIOBase):
    """Capture at most a configured number of characters."""

    def __init__(self, limit: int) -> None:
        self.limit: int = limit
        self.parts: list[str] = []
        self.length: int = 0
        self.truncated: bool = False

    @override
    def writable(self) -> bool:
        return True

    @override
    def write(self, text: str) -> int:
        remaining = max(self.limit - self.length, 0)
        if remaining:
            kept = text[:remaining]
            self.parts.append(kept)
            self.length += len(kept)
        if len(text) > remaining:
            self.truncated = True
        return len(text)

    def getvalue(self) -> str:
        value = "".join(self.parts)
        if self.truncated:
            value += "\n… output truncated …"
        return value


@final
class ReplWorker:
    def __init__(self, max_output_chars: int) -> None:
        self.max_output_chars: int = max_output_chars
        self.namespace: dict[str, Any] = {
            "__name__": "__main__",
            "__file__": "<python-repl>",
            "__builtins__": builtins.__dict__,
        }
        self.started_at: float = time.monotonic()

    def info(self) -> dict[str, Any]:
        return {
            "python_version": sys.version.split()[0],
            "python_executable": sys.executable,
            "cwd": __import__("os").getcwd(),
            "uptime_seconds": round(time.monotonic() - self.started_at, 3),
        }

    @staticmethod
    def _evaluate_compiled(code: Any, namespace: dict[str, Any]) -> Any:
        result = eval(code, namespace)
        if inspect.isawaitable(result):

            async def await_result(value: Awaitable[Any]) -> Any:
                return await value

            result = asyncio.run(await_result(result))
        return result

    def execute(self, source: str) -> dict[str, Any]:
        stdout = CappedText(self.max_output_chars)
        stderr = CappedText(self.max_output_chars)
        result_text = ""
        error_text = ""
        ok = True
        started = time.monotonic()
        flags = ast.PyCF_ALLOW_TOP_LEVEL_AWAIT
        try:
            tree = ast.parse(source, filename="<python-repl>", mode="exec")
            final_expr: ast.Expr | None = None
            if tree.body and isinstance(tree.body[-1], ast.Expr):
                last_node = tree.body.pop()
                assert isinstance(last_node, ast.Expr)
                final_expr = last_node
            module = ast.Module(body=tree.body, type_ignores=tree.type_ignores)
            with (
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
                _redirect_stdin(),
            ):
                if module.body:
                    module_code = compile(module, "<python-repl>", "exec", flags=flags)
                    _ = self._evaluate_compiled(module_code, self.namespace)
                if final_expr is not None:
                    expression_code = compile(
                        ast.Expression(final_expr.value),
                        "<python-repl>",
                        "eval",
                        flags=flags,
                    )
                    value = self._evaluate_compiled(expression_code, self.namespace)
                    self.namespace["_"] = value
                    if value is not None:
                        try:
                            result_text = repr(value)
                        except Exception as repr_error:  # noqa: BLE001 - user-defined repr may fail.
                            result_text = f"<repr failed: {type(repr_error).__name__}>"
                        if len(result_text) > self.max_output_chars:
                            result_text = (
                                result_text[: self.max_output_chars]
                                + "… (repr truncated)"
                            )
        except (Exception, SystemExit, KeyboardInterrupt) as exc:  # noqa: BLE001 - keep the REPL alive after user code exits or interrupts.
            ok = False
            error_text = f"{type(exc).__name__}: {exc}"
            with contextlib.redirect_stderr(stderr):
                traceback.print_exc()
        return {
            "ok": ok,
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
            "result": result_text,
            "error": error_text,
            "duration_seconds": round(time.monotonic() - started, 3),
            "output_truncated": stdout.truncated
            or stderr.truncated
            or len(result_text) > self.max_output_chars,
        }


def _send(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=True, separators=(",", ":")) + "\n")
    sys.stdout.flush()


@contextlib.contextmanager
def _redirect_stdin() -> Iterator[None]:
    """Prevent a cell's input() call from consuming worker protocol messages."""
    original = sys.stdin
    sys.stdin = io.StringIO()
    try:
        yield
    finally:
        sys.stdin = original


def serve(max_output_chars: int = 40_000, max_request_bytes: int = 1_000_000) -> int:
    worker = ReplWorker(max_output_chars)
    _send({"type": "ready", **worker.info()})
    while True:
        raw = sys.stdin.buffer.readline(max_request_bytes + 1)
        if not raw:
            return 0
        if len(raw) > max_request_bytes and not raw.endswith(b"\n"):
            while raw and not raw.endswith(b"\n"):
                raw = sys.stdin.buffer.readline(max_request_bytes + 1)
            _send(
                {
                    "type": "response",
                    "id": None,
                    "ok": False,
                    "error": "Request exceeded the worker limit",
                }
            )
            continue
        try:
            request = json.loads(raw)
            request_id = request.get("id")
            method = request.get("method")
            if method == "execute" and isinstance(request.get("code"), str):
                result = worker.execute(request["code"])
                _send({"type": "response", "id": request_id, **result})
            elif method == "info":
                _send(
                    {
                        "type": "response",
                        "id": request_id,
                        "ok": True,
                        "info": worker.info(),
                    }
                )
            else:
                _send(
                    {
                        "type": "response",
                        "id": request_id,
                        "ok": False,
                        "error": "Unknown or malformed worker request",
                    }
                )
        except (json.JSONDecodeError, AttributeError, TypeError) as exc:
            _send(
                {
                    "type": "response",
                    "id": None,
                    "ok": False,
                    "error": f"Invalid worker request: {type(exc).__name__}",
                }
            )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--max-output-chars", type=int, default=40_000)
    args = parser.parse_args()
    if args.max_output_chars < 1:
        parser.error("--max-output-chars must be positive")
    raise SystemExit(serve(max_output_chars=args.max_output_chars))
