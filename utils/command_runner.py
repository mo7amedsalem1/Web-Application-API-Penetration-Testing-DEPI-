"""
command_runner.py — Safe subprocess execution wrapper.

All external commands are launched via argument lists (never shell=True)
to prevent command injection.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class CommandError(Exception):
    """Raised when an external command fails in an unrecoverable way."""


class DependencyError(Exception):
    """Raised when a required binary cannot be found."""


def find_binary(name: str) -> Optional[str]:
    """Return the absolute path to *name* or None if not on PATH."""
    path = shutil.which(name)
    if path:
        logger.debug("Found binary: %s -> %s", name, path)
    return path


def require_binary(name: str) -> str:
    """
    Return the absolute path to *name* or raise DependencyError.
    Validates the path exists and is executable.
    """
    path = find_binary(name)
    if not path:
        raise DependencyError(
            f"Required binary '{name}' not found on PATH. "
            f"Please install it before running this stage."
        )
    return path


def run_command(
    args: List[str],
    *,
    capture_output: bool = True,
    timeout: Optional[int] = None,
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    input_data: Optional[bytes] = None,
    check: bool = False,
) -> subprocess.CompletedProcess:
    """
    Execute *args* safely via subprocess.run (no shell=True).

    Parameters
    ----------
    args:
        Command and its arguments as a list.  The first element is
        resolved through PATH before execution.
    capture_output:
        When True, stdout/stderr are captured and returned.
    timeout:
        Maximum wall-clock seconds to allow.  None = no limit.
    cwd:
        Working directory for the subprocess.
    env:
        Optional explicit environment mapping.
    input_data:
        Data to send to the process's stdin.
    check:
        If True, raise CommandError on non-zero exit code.

    Returns
    -------
    subprocess.CompletedProcess

    Raises
    ------
    DependencyError  — binary not found.
    CommandError     — subprocess returned non-zero when check=True.
    TimeoutError     — process exceeded *timeout* seconds.
    """
    if not args:
        raise ValueError("args must be a non-empty list")

    binary = find_binary(args[0])
    if binary is None:
        raise DependencyError(
            f"Binary '{args[0]}' not found on PATH."
        )
    resolved_args = [binary] + args[1:]

    logger.debug("Running: %s", " ".join(str(a) for a in resolved_args))
    start = time.monotonic()

    try:
        result = subprocess.run(
            resolved_args,
            capture_output=capture_output,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
            env=env,
            input=input_data,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(
            f"Command timed out after {timeout}s: {' '.join(str(a) for a in resolved_args)}"
        ) from exc
    except FileNotFoundError as exc:
        raise DependencyError(
            f"Binary not found when executing: {args[0]}"
        ) from exc

    elapsed = time.monotonic() - start
    logger.debug(
        "Finished (rc=%d, %.1fs): %s",
        result.returncode,
        elapsed,
        resolved_args[0],
    )

    if check and result.returncode != 0:
        stderr = result.stderr.decode(errors="replace") if result.stderr else ""
        raise CommandError(
            f"Command failed (rc={result.returncode}): "
            f"{' '.join(str(a) for a in resolved_args)}\nstderr: {stderr}"
        )

    return result


def stream_command(
    args: List[str],
    *,
    timeout: Optional[int] = None,
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    input_bytes: Optional[bytes] = None,
) -> Tuple[int, str, str]:
    """
    Run *args* and stream stdout/stderr to logger in real time,
    returning (returncode, stdout_text, stderr_text).

    Useful for long-running commands where live progress is desired.
    """
    if not args:
        raise ValueError("args must be a non-empty list")

    binary = find_binary(args[0])
    if binary is None:
        raise DependencyError(f"Binary '{args[0]}' not found on PATH.")

    resolved_args = [binary] + args[1:]
    logger.debug("Streaming: %s", " ".join(str(a) for a in resolved_args))

    stdout_lines: List[str] = []
    stderr_lines: List[str] = []

    with subprocess.Popen(
        resolved_args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdin=subprocess.PIPE if input_bytes else None,
    ) as proc:
        if input_bytes and proc.stdin:
            proc.stdin.write(input_bytes)
            proc.stdin.close()

        def _reader(pipe, lines: List[str], level: int) -> None:
            for raw in pipe:
                line = raw.decode(errors="replace").rstrip()
                lines.append(line)
                logger.log(level, "%s", line)

        t_out = threading.Thread(target=_reader, args=(proc.stdout, stdout_lines, logging.DEBUG))
        t_err = threading.Thread(target=_reader, args=(proc.stderr, stderr_lines, logging.DEBUG))
        t_out.start()
        t_err.start()

        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise TimeoutError(
                f"Command timed out after {timeout}s: {resolved_args[0]}"
            )
        finally:
            t_out.join()
            t_err.join()

    return proc.returncode, "\n".join(stdout_lines), "\n".join(stderr_lines)
