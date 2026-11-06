# pyright: reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportUnknownLambdaType=false, reportMissingParameterType=false, reportUnusedCallResult=false, reportUnusedFunction=false, reportUnusedParameter=false, reportDeprecated=false, reportOptionalMemberAccess=false, reportUnnecessaryComparison=false
"""Per-Codex stdio MCP server for a persistent uv-managed Python REPL."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import math
import os
import shutil
import signal
import sys
import tempfile
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, final

from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan

WORKER_PATH = Path(__file__).with_name("python_repl_worker.py")
MAX_DEPENDENCIES = 32
MAX_DEPENDENCY_LENGTH = 512
MAX_PROTOCOL_BYTES = 1_000_000


@dataclass(frozen=True)
class ReplSettings:
    uv_executable: str
    base_python: str
    project_venv: str | None
    cwd: str
    default_timeout_seconds: float
    max_timeout_seconds: float
    startup_timeout_seconds: float
    max_code_chars: int
    max_output_chars: int


def _dependency(value: str) -> str:
    value = value.strip()
    if (
        not value
        or len(value) > MAX_DEPENDENCY_LENGTH
        or value.startswith("-")
        or any(ord(char) < 32 for char in value)
    ):
        raise argparse.ArgumentTypeError(
            "dependency must be a non-empty PEP 508 requirement without control characters"
        )
    return value


def _resolve_uv(value: str) -> str:
    candidate = shutil.which(value) if os.path.sep not in value else value
    if not candidate or not Path(candidate).is_file():
        raise argparse.ArgumentTypeError(f"uv executable not found: {value}")
    return str(Path(candidate).resolve())


def _python_in_venv(venv: Path) -> Path:
    executable = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not (venv / "pyvenv.cfg").is_file() or not executable.is_file():
        raise argparse.ArgumentTypeError(
            f"project venv must contain pyvenv.cfg and a Python executable: {venv}"
        )
    # Keep the venv-local path: resolving a symlinked python executable escapes
    # the venv and makes uv select only the base interpreter, not the venv.
    return executable


def parse_settings(argv: list[str] | None = None) -> ReplSettings:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "--project-venv",
        type=Path,
        help="existing project venv to use as the kernel base; it is not modified",
    )
    _ = parser.add_argument(
        "--uv",
        default=shutil.which("uv") or "uv",
        help="uv executable (default: found on PATH)",
    )
    _ = parser.add_argument("--default-timeout-seconds", type=float, default=60.0)
    _ = parser.add_argument("--max-timeout-seconds", type=float, default=600.0)
    _ = parser.add_argument("--startup-timeout-seconds", type=float, default=300.0)
    _ = parser.add_argument("--max-code-chars", type=int, default=100_000)
    _ = parser.add_argument("--max-output-chars", type=int, default=40_000)
    _ = parser.add_argument(
        "--cwd",
        type=Path,
        default=Path.cwd(),
        help="working directory for the kernel (default: launcher's current directory)",
    )
    args = parser.parse_args(argv)

    if (
        not math.isfinite(args.default_timeout_seconds)
        or args.default_timeout_seconds <= 0
    ):
        parser.error("--default-timeout-seconds must be positive")
    if (
        not math.isfinite(args.max_timeout_seconds)
        or args.max_timeout_seconds < args.default_timeout_seconds
    ):
        parser.error("--max-timeout-seconds must be >= --default-timeout-seconds")
    if (
        not math.isfinite(args.startup_timeout_seconds)
        or args.startup_timeout_seconds <= 0
    ):
        parser.error("--startup-timeout-seconds must be positive")
    if args.max_code_chars < 1:
        parser.error("--max-code-chars must be positive")
    if not 1_000 <= args.max_output_chars <= 1_000_000:
        parser.error("--max-output-chars must be between 1000 and 1000000")

    try:
        uv_executable = _resolve_uv(args.uv)
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    project_venv = None
    base_python = sys.executable
    if args.project_venv is not None:
        try:
            project_venv = str(args.project_venv.expanduser().resolve(strict=True))
            base_python = str(_python_in_venv(Path(project_venv)))
        except (OSError, argparse.ArgumentTypeError) as exc:
            parser.error(str(exc))
    try:
        cwd = str(args.cwd.expanduser().resolve(strict=True))
    except OSError as exc:
        parser.error(f"--cwd does not exist: {exc}")
    if not Path(cwd).is_dir():
        parser.error("--cwd must name a directory")

    return ReplSettings(
        uv_executable=uv_executable,
        base_python=base_python,
        project_venv=project_venv,
        cwd=cwd,
        default_timeout_seconds=args.default_timeout_seconds,
        max_timeout_seconds=args.max_timeout_seconds,
        startup_timeout_seconds=args.startup_timeout_seconds,
        max_code_chars=args.max_code_chars,
        max_output_chars=args.max_output_chars,
    )


def build_worker_command(settings: ReplSettings) -> list[str]:
    return [
        settings.uv_executable,
        "run",
        "--no-project",
        "--python",
        settings.base_python,
        "--",
        "python",
        "-u",
        str(WORKER_PATH),
        "--max-output-chars",
        str(settings.max_output_chars),
    ]


def build_dependency_command(
    settings: ReplSettings, target: Path, dependencies: tuple[str, ...]
) -> list[str]:
    return [
        settings.uv_executable,
        "pip",
        "install",
        "--target",
        str(target),
        "--python",
        settings.base_python,
        *dependencies,
    ]


@final
class KernelManager:
    """Manage one persistent worker process for this stdio MCP process."""

    def __init__(self, settings: ReplSettings) -> None:
        self.settings: ReplSettings = settings
        self.process: asyncio.subprocess.Process | None = None
        self.worker_info: dict[str, Any] | None = None
        self.started_at: float | None = None
        self._request_id: int = 0
        self._lock: asyncio.Lock = asyncio.Lock()
        self._stderr_lines: deque[str] = deque(maxlen=80)
        self._stderr_task: asyncio.Task[None] | None = None
        self.dependencies: tuple[str, ...] = ()
        self._dependency_root: Path | None = None
        self._dependency_paths: list[Path] = []

    async def _collect_stderr(self, stream: asyncio.StreamReader) -> None:
        try:
            while line := await stream.readline():
                decoded = line.decode("utf-8", errors="replace").rstrip()
                if len(decoded) > 2_000:
                    decoded = decoded[:2_000] + "…"
                self._stderr_lines.append(decoded)
        except (asyncio.CancelledError, ValueError):
            return

    def _stderr_tail(self) -> str:
        return "\n".join(list(self._stderr_lines)[-12:])[-8_000:]

    @staticmethod
    async def _capture_install_stderr(
        stream: asyncio.StreamReader, lines: deque[str]
    ) -> None:
        try:
            while line := await stream.readline():
                decoded = line.decode("utf-8", errors="replace").rstrip()
                if len(decoded) > 2_000:
                    decoded = decoded[:2_000] + "…"
                lines.append(decoded)
        except (asyncio.CancelledError, ValueError):
            return

    @staticmethod
    async def _terminate_process(process: asyncio.subprocess.Process) -> None:
        if os.name == "posix":
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), timeout=0.75)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                _ = await process.wait()
        else:
            if process.returncode is None:
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=0.75)
            except TimeoutError:
                process.kill()
                _ = await process.wait()

    async def _install_dependencies(self, requested: list[str] | None) -> None:
        if not requested:
            return
        if len(requested) > MAX_DEPENDENCIES:
            raise ValueError(
                f"at most {MAX_DEPENDENCIES} dependencies may be passed per call"
            )
        try:
            additions = tuple(dict.fromkeys(_dependency(item) for item in requested))
        except argparse.ArgumentTypeError as exc:
            raise ValueError(str(exc)) from exc
        candidate = tuple(dict.fromkeys((*self.dependencies, *additions)))
        if candidate == self.dependencies:
            return
        if len(candidate) > MAX_DEPENDENCIES:
            raise ValueError(
                f"this REPL session may add at most {MAX_DEPENDENCIES} dependencies"
            )

        if self._dependency_root is None:
            self._dependency_root = Path(
                tempfile.mkdtemp(prefix="python-repl-dependencies-")
            )
        target = self._dependency_root / f"overlay-{len(self._dependency_paths) + 1}"
        command = build_dependency_command(self.settings, target, candidate)
        env = os.environ.copy()
        env["UV_NO_PROGRESS"] = "1"
        env["UV_COLOR"] = "never"
        kwargs: dict[str, Any] = {}
        if os.name == "posix":
            kwargs["start_new_session"] = True

        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.settings.cwd,
            env=env,
            **kwargs,
        )
        assert process.stderr is not None
        stderr_lines: deque[str] = deque(maxlen=32)
        stderr_task = asyncio.create_task(
            self._capture_install_stderr(process.stderr, stderr_lines)
        )
        try:
            await asyncio.wait_for(
                process.wait(), timeout=self.settings.startup_timeout_seconds
            )
        except TimeoutError:
            await self._terminate_process(process)
            stderr_task.cancel()
            _ = await asyncio.gather(stderr_task, return_exceptions=True)
            shutil.rmtree(target, ignore_errors=True)
            raise RuntimeError(
                f"uv dependency installation exceeded {self.settings.startup_timeout_seconds:g} seconds"
            ) from None
        except asyncio.CancelledError:
            await self._terminate_process(process)
            stderr_task.cancel()
            _ = await asyncio.gather(stderr_task, return_exceptions=True)
            shutil.rmtree(target, ignore_errors=True)
            raise
        await stderr_task
        if process.returncode != 0:
            detail = "\n".join(stderr_lines)[-8_000:]
            shutil.rmtree(target, ignore_errors=True)
            raise RuntimeError(
                f"uv could not install dependencies (exit code {process.returncode})"
                + (f":\n{detail}" if detail else "")
            )

        # If the kernel is already running, add the new overlay without restarting
        # it so existing REPL variables and imports remain available.
        self._dependency_paths.append(target)
        try:
            if self.process is not None and self.process.returncode is None:
                response = await self._request(
                    "add_dependency_path",
                    dependency_path=str(target),
                    timeout_seconds=self.settings.startup_timeout_seconds,
                )
                if not response.get("ok"):
                    raise RuntimeError(
                        str(response.get("error", "worker rejected dependency path"))
                    )
        except (Exception, asyncio.CancelledError):
            self._dependency_paths.pop()
            shutil.rmtree(target, ignore_errors=True)
            raise
        self.dependencies = candidate

    async def _ensure_started(self) -> asyncio.subprocess.Process:
        if self.process is not None and self.process.returncode is None:
            return self.process
        self._stderr_lines.clear()
        command = build_worker_command(self.settings)
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["UV_NO_PROGRESS"] = "1"
        env["UV_COLOR"] = "never"
        if self._dependency_paths:
            inherited_pythonpath = env.get("PYTHONPATH")
            pythonpath = [str(self._dependency_paths[-1])]
            if inherited_pythonpath:
                pythonpath.append(inherited_pythonpath)
            env["PYTHONPATH"] = os.pathsep.join(pythonpath)
        kwargs: dict[str, Any] = {}
        if os.name == "posix":
            kwargs["start_new_session"] = True
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.settings.cwd,
            env=env,
            limit=max(MAX_PROTOCOL_BYTES, self.settings.max_output_chars * 8 + 8_192),
            **kwargs,
        )
        self.process = process
        assert process.stderr is not None
        self._stderr_task = asyncio.create_task(self._collect_stderr(process.stderr))
        try:
            assert process.stdout is not None
            ready_line = await asyncio.wait_for(
                process.stdout.readline(), timeout=self.settings.startup_timeout_seconds
            )
            if not ready_line:
                raise RuntimeError(
                    "REPL worker exited before sending its ready message"
                )
            ready = json.loads(ready_line)
            if ready.get("type") != "ready":
                raise RuntimeError("REPL worker sent an invalid ready message")
            self.worker_info = ready
            self.started_at = time.monotonic()
            return process
        except asyncio.CancelledError:
            await self._stop_worker()
            raise
        except Exception as exc:
            stderr = self._stderr_tail()
            await self._stop_worker()
            detail = f"uv could not start the Python REPL worker: {type(exc).__name__}: {exc}"
            if stderr:
                detail += f"\nuv/worker stderr:\n{stderr}"
            raise RuntimeError(detail) from exc

    async def _stop_worker(self) -> None:
        process = self.process
        self.process = None
        self.worker_info = None
        self.started_at = None
        if process is not None:
            await self._terminate_process(process)
        stderr_task = self._stderr_task
        self._stderr_task = None
        if stderr_task is not None:
            try:
                await asyncio.wait_for(stderr_task, timeout=1.0)
            except TimeoutError:
                stderr_task.cancel()
                _ = await asyncio.gather(stderr_task, return_exceptions=True)

    async def _request(
        self,
        method: str,
        *,
        code: str | None = None,
        dependency_path: str | None = None,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        process = await self._ensure_started()
        if process.stdin is None or process.stdout is None:
            raise RuntimeError("REPL worker pipes are unavailable")
        self._request_id += 1
        request_id = self._request_id
        request: dict[str, Any] = {"id": request_id, "method": method}
        if code is not None:
            request["code"] = code
        if dependency_path is not None:
            request["dependency_path"] = dependency_path
        payload = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
        process.stdin.write(payload.encode("utf-8") + b"\n")
        await process.stdin.drain()
        line = await asyncio.wait_for(
            process.stdout.readline(), timeout=timeout_seconds
        )
        if not line:
            raise RuntimeError("REPL worker exited without returning a response")
        response = json.loads(line)
        if response.get("type") != "response" or response.get("id") != request_id:
            raise RuntimeError(
                "REPL worker returned an out-of-order or malformed response"
            )
        return {
            key: value for key, value in response.items() if key not in {"type", "id"}
        }

    async def execute(
        self,
        code: str,
        timeout_seconds: float | None,
        dependencies: list[str] | None = None,
    ) -> dict[str, Any]:
        if len(code) > self.settings.max_code_chars:
            return {
                "ok": False,
                "stdout": "",
                "stderr": "",
                "result": "",
                "error": f"Code exceeds the configured {self.settings.max_code_chars}-character limit",
                "duration_seconds": 0.0,
                "output_truncated": False,
            }
        effective_timeout = (
            self.settings.default_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        if (
            not math.isfinite(effective_timeout)
            or effective_timeout <= 0
            or effective_timeout > self.settings.max_timeout_seconds
        ):
            return {
                "ok": False,
                "stdout": "",
                "stderr": "",
                "result": "",
                "error": f"timeout_seconds must be in (0, {self.settings.max_timeout_seconds}]",
                "duration_seconds": 0.0,
                "output_truncated": False,
            }
        async with self._lock:
            try:
                await self._install_dependencies(dependencies)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - expose dependency resolution errors to the tool caller.
                return {
                    "ok": False,
                    "stdout": "",
                    "stderr": "",
                    "result": "",
                    "error": f"Dependency setup failed: {type(exc).__name__}: {exc}",
                    "duration_seconds": 0.0,
                    "output_truncated": False,
                }
            if not code.strip():
                return {
                    "ok": True,
                    "stdout": "",
                    "stderr": "",
                    "result": "",
                    "error": "",
                    "duration_seconds": 0.0,
                    "output_truncated": False,
                }
            try:
                return await self._request(
                    "execute", code=code, timeout_seconds=effective_timeout
                )
            except TimeoutError:
                await self._stop_worker()
                return {
                    "ok": False,
                    "stdout": "",
                    "stderr": "",
                    "result": "",
                    "error": f"Execution exceeded {effective_timeout:g} seconds; the worker was terminated and will restart on the next cell.",
                    "duration_seconds": effective_timeout,
                    "output_truncated": False,
                }
            except asyncio.CancelledError:
                await self._stop_worker()
                raise
            except Exception as exc:  # noqa: BLE001 - convert worker failures into an MCP result.
                stderr = self._stderr_tail()
                await self._stop_worker()
                detail = f"{type(exc).__name__}: {exc}"
                if stderr:
                    detail += f"\nworker stderr:\n{stderr}"
                return {
                    "ok": False,
                    "stdout": "",
                    "stderr": "",
                    "result": "",
                    "error": detail,
                    "duration_seconds": 0.0,
                    "output_truncated": False,
                }

    async def reset(self) -> dict[str, Any]:
        async with self._lock:
            was_running = self.process is not None and self.process.returncode is None
            await self._stop_worker()
        return {
            "ok": True,
            "reset": was_running,
            "message": "REPL state cleared; the next execution starts a fresh worker with the same session dependency overlay.",
        }

    async def info(self) -> dict[str, Any]:
        async with self._lock:
            running = self.process is not None and self.process.returncode is None
            return {
                "worker_running": running,
                "worker_pid": self.process.pid
                if running and self.process is not None
                else None,
                "worker": self.worker_info if running else None,
                "project_venv": self.settings.project_venv,
                "base_python": self.settings.base_python,
                "dependencies": list(self.dependencies),
                "working_directory": self.settings.cwd,
                "default_timeout_seconds": self.settings.default_timeout_seconds,
                "max_timeout_seconds": self.settings.max_timeout_seconds,
            }

    async def close(self) -> None:
        async with self._lock:
            await self._stop_worker()
            if self._dependency_root is not None:
                shutil.rmtree(self._dependency_root, ignore_errors=True)
                self._dependency_root = None
                self._dependency_paths.clear()


def create_server(settings: ReplSettings) -> FastMCP:
    manager = KernelManager(settings)

    @lifespan
    async def app_lifespan(_server: FastMCP[Any]):
        try:
            yield {}
        finally:
            await manager.close()

    server = FastMCP(name="python-repl", lifespan=app_lifespan)

    @server.tool(tags={"execution", "repl"})
    async def python_repl_execute(
        code: str,
        timeout_seconds: float | None = None,
        dependencies: list[str] | None = None,
    ) -> dict[str, Any]:
        """Execute Python in this Codex process's persistent REPL namespace.

        The worker starts lazily. It is not sandboxed and has the filesystem and
        network permissions of the selected Python environment's user process.
        Pass PEP 508 requirements in `dependencies` to install them into a
        private session overlay before this cell runs. Added dependencies remain
        available to later cells. The final expression's repr is returned in `result`.
        """
        return await manager.execute(code, timeout_seconds, dependencies)

    @server.tool(tags={"execution", "repl"})
    async def python_repl_reset() -> dict[str, Any]:
        """Terminate the current kernel and clear all variables and imports."""
        return await manager.reset()

    @server.tool(tags={"repl", "introspection"})
    async def python_repl_info() -> dict[str, Any]:
        """Return session dependencies, working directory and worker status."""
        return await manager.info()

    return server


def main(argv: list[str] | None = None) -> None:
    settings = parse_settings(argv)
    server = create_server(settings)
    server.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
