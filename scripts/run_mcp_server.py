#!/usr/bin/env python3
"""
CLI entrypoint to run the python-mcp MCP server with robust trace capture.

Usage:
    python scripts/run_mcp_server.py [--log-file LOG] [--trace-file TRACE] [--pid-file PID]

This script:
- Configures logging to a file and stderr.
- Installs sys.excepthook and asyncio exception handler to persist tracebacks.
- Enables faulthandler and registers signal handlers to dump native/fatal traces.
- Writes an optional PID file.
- Calls the MCP server main() entrypoint.

Intended for debugging and capturing crashes/traces while running the server
in a reproducible, foreground manner (so you can reproduce the problem and
collect traces).
"""

from __future__ import annotations

import argparse
import asyncio
import faulthandler
import logging
import os
import signal
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

# Constants
DEFAULT_LOG = Path.cwd() / "server_run.out"
DEFAULT_TRACE = Path("/tmp/python_mcp_uncaught.log")
DEFAULT_PID = None  # or Path("/tmp/python_mcp_mcp.pid")


def setup_logging(log_path: Path) -> None:
    """
    Configure logging to both file and stderr.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # File handler
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root.addHandler(fh)

    # Console handler (stderr)
    ch = logging.StreamHandler(sys.stderr)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root.addHandler(ch)

    logging.info("Logging initialized; file=%s", log_path)


def write_trace_file(trace_path: Path, header: str, tb: Optional[str] = None) -> None:
    """
    Append a header + optional traceback text to the trace file.
    """
    try:
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        with trace_path.open("a", encoding="utf-8") as f:
            f.write("\n" + "=" * 80 + "\n")
            f.write(f"{header}\n")
            f.write(f"Timestamp: {datetime.utcnow().isoformat()} UTC\n")
            f.write("\n")
            if tb:
                f.write(tb)
            f.flush()
    except Exception:
        # Best-effort only; do not raise in diagnostic logging
        pass


def excepthook_writer(trace_path: Path):
    """
    Returns a sys.excepthook function that writes unhandled exceptions to trace_path.
    """

    def _hook(exc_type, exc_value, exc_tb):
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        write_trace_file(trace_path, "Uncaught exception (sys.excepthook)", tb_text)
        # Also print to stderr so the foreground runs show the error
        sys.__stderr__.write(tb_text)

    return _hook


def asyncio_exception_handler(trace_path: Path):
    """
    Returns an asyncio loop exception handler that logs its context to trace_path.
    """

    def _handler(loop, context):
        try:
            msg = context.get("message", str(context))
            tb = context.get("exception")
            tb_text = ""
            if tb:
                tb_text = "".join(
                    traceback.format_exception(type(tb), tb, tb.__traceback__)
                )
            body = f"Asyncio exception: {msg}\n{tb_text}"
            write_trace_file(trace_path, "Asyncio loop exception", body)
            logging.error("Asyncio loop exception: %s", msg, exc_info=tb)
        except Exception:
            pass

    return _handler


def register_faulthandler(trace_path: Path) -> None:
    """
    Enable faulthandler to write C-level/native tracebacks to the trace file.

    Also register handlers for SIGUSR1 (dump) and SIGTERM/SIGINT to dump before exit.
    """
    try:
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        f = trace_path.open("a", encoding="utf-8")
        # Enable faulthandler writing to file for fatal errors
        faulthandler.enable(file=f)

        # Register signal to dump stacks on demand
        try:
            faulthandler.register(signal.SIGUSR1, file=f, all_threads=True)
        except Exception:
            # Not all platforms support SIGUSR1
            pass

        def _dump_and_exit(signum, frame):
            try:
                f.write("\n=== Signal received: dumping faulthandler ===\n")
                f.flush()
                faulthandler.dump_traceback(file=f, all_threads=True)
            except Exception:
                pass
            finally:
                # Flush and close
                try:
                    f.flush()
                    f.close()
                except Exception:
                    pass
                # Use os._exit to avoid triggering any other handlers that might be unstable
                os._exit(128 + signum)

        # For termination signals, dump and exit
        for s in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(s, _dump_and_exit)
            except Exception:
                pass
    except Exception:
        # Best-effort only
        pass


def write_pid_file(pid_path: Optional[Path]) -> None:
    if not pid_path:
        return
    try:
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        with pid_path.open("w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass


def remove_pid_file(pid_path: Optional[Path]) -> None:
    if not pid_path:
        return
    try:
        pid_path.unlink()
    except Exception:
        pass


def main_entrypoint(
    log_file: Path,
    trace_file: Path,
    pid_file: Optional[Path] = None,
) -> int:
    """
    Setup diagnostic hooks and run the MCP server main() entrypoint.
    Returns an exit code.
    """
    # Ensure project src is on sys.path (so imports like `python_mcp_server` work)
    project_root = Path.cwd()
    src_dir = project_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    # Setup logging
    setup_logging(log_file)

    # Setup trace hooks
    sys.excepthook = excepthook_writer(trace_file)
    try:
        loop = asyncio.get_event_loop()
        loop.set_exception_handler(asyncio_exception_handler(trace_file))
    except Exception:
        # may be no loop at startup; okay
        pass

    register_faulthandler(trace_file)
    write_pid_file(pid_file)

    # Run the server main
    try:
        # Import here to pick up modified sys.path above
        # Redirect stdout/stderr to the log file before importing/running the server
        # to avoid interactive console writes (rich) trying to write to the MCP stdio.
        try:
            f = log_file.open("a", encoding="utf-8")
            try:
                sys.stdout.flush()
            except Exception:
                pass
            try:
                sys.stderr.flush()
            except Exception:
                pass
            # Rebind stdout/stderr to file handle (best-effort)
            sys.stdout = f
            sys.stderr = f
        except Exception:
            # If redirecting fails, continue; the exception will be logged separately
            pass

        from python_mcp_server import main as mcp_main  # type: ignore

        logging.info("Launching MCP server via CLI entrypoint")
        mcp_main()
        return 0
    except Exception:
        # Capture full traceback
        tb = "".join(traceback.format_exc())
        logging.error("Top-level exception while running MCP server:\n%s", tb)
        write_trace_file(trace_file, "Top-level MCP server exception", tb)
        return 1
    finally:
        remove_pid_file(pid_file)


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run python-mcp MCP server with trace capture"
    )
    p.add_argument(
        "--log-file",
        "-l",
        type=Path,
        default=Path.cwd() / "server_run.out",
        help="Log file path",
    )
    p.add_argument(
        "--trace-file",
        "-t",
        type=Path,
        default=Path("/tmp/python_mcp_uncaught.log"),
        help="Trace file path for exceptions and faulthandler",
    )
    p.add_argument(
        "--pid-file", "-p", type=Path, default=None, help="Optional PID file path"
    )
    return p.parse_args(argv)


if __name__ == "__main__":
    args = parse_args(sys.argv[1:])
    rc = main_entrypoint(args.log_file, args.trace_file, args.pid_file)
    # exit with the captured return code
    raise SystemExit(rc)
