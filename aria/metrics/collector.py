"""
aria/metrics/collector.py
──────────────────────────
Wraps tool execution with automatic metrics recording.
Every call to a tool is timed, its outcome recorded, and persisted to SQLite.
"""

from __future__ import annotations

import hashlib
import json
import time
from contextlib import contextmanager
from typing import Any, Callable, Generator

from aria.metrics.db import insert_execution


def _hash_input(input_data: Any) -> str:
    """Create a short SHA-256 hash of the tool input for deduplication/debugging."""
    try:
        raw = json.dumps(input_data, sort_keys=True, default=str)
    except Exception:
        raw = str(input_data)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _score_output(output: Any, success: bool) -> float:
    """
    Heuristic quality score for a tool output.
    1.0 = rich, non-empty output
    0.5 = minimal output (short string, small number)
    0.0 = failure or empty
    """
    if not success or output is None:
        return 0.0
    if isinstance(output, str):
        if len(output) == 0:
            return 0.0
        if len(output) < 10:
            return 0.4
        return 1.0
    if isinstance(output, (int, float)):
        return 0.8  # numeric results are usually correct
    if isinstance(output, (list, dict)):
        return 1.0 if output else 0.2
    return 0.7


@contextmanager
def record(tool_name: str, input_data: Any) -> Generator[None, None, None]:
    """
    Context manager that wraps a tool's run() call.

    Usage:
        with record("search_tool", input_dict) as ctx:
            result = tool.run(input_dict)
            ctx.set_result(result)

    But more commonly, use the `execute()` function below.
    """
    # We use a mutable container to pass result back out
    class _Ctx:
        result: Any = None
        error: str | None = None

    ctx = _Ctx()
    start = time.monotonic()
    input_hash = _hash_input(input_data)

    try:
        yield ctx
        success = ctx.error is None
    except Exception as exc:
        ctx.error = str(exc)
        success = False
    finally:
        latency = time.monotonic() - start
        quality = _score_output(ctx.result, success)
        insert_execution(
            tool_name=tool_name,
            timestamp=time.time(),
            success=success,
            latency_seconds=latency,
            input_hash=input_hash,
            output_quality_score=quality,
            error_message=ctx.error,
        )


def execute(tool_name: str, run_fn: Callable[[], Any], input_data: Any = None) -> Any:
    """
    Execute a callable, record its metrics, and return its result.

    Args:
        tool_name:  The tool's identifier (e.g. "search_tool")
        run_fn:     Zero-argument callable wrapping the tool invocation
        input_data: The original input dict (used for hashing only)

    Returns:
        Whatever run_fn() returns.

    Raises:
        Re-raises any exception from run_fn after logging it.
    """
    start = time.monotonic()
    input_hash = _hash_input(input_data)
    error_message: str | None = None
    result: Any = None
    success = True

    try:
        result = run_fn()
    except Exception as exc:
        error_message = str(exc)
        success = False
        raise
    finally:
        latency = time.monotonic() - start
        quality = _score_output(result, success)
        insert_execution(
            tool_name=tool_name,
            timestamp=time.time(),
            success=success,
            latency_seconds=latency,
            input_hash=input_hash,
            output_quality_score=quality,
            error_message=error_message,
        )

    return result
