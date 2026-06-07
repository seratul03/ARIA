"""
aria/metrics/db.py
──────────────────
SQLite schema, connection management, and query helpers for ARIA metrics.

Tables:
  - tool_executions   : one row per tool invocation
  - improvement_history : one row per improvement cycle outcome
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Generator


# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS tool_executions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name           TEXT    NOT NULL,
    timestamp           REAL    NOT NULL,       -- Unix epoch float
    success             INTEGER NOT NULL,       -- 1 = success, 0 = failure
    latency_seconds     REAL    NOT NULL,
    input_hash          TEXT,                   -- sha256 of input dict
    output_quality_score REAL   DEFAULT 0.0,    -- 0.0–1.0
    error_message       TEXT                    -- NULL if success
);

CREATE TABLE IF NOT EXISTS improvement_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name       TEXT    NOT NULL,
    timestamp       REAL    NOT NULL,
    status          TEXT    NOT NULL,   -- 'deployed' | 'rejected' | 'rolled_back'
    reason          TEXT,               -- human-readable explanation
    git_commit_hash TEXT,               -- NULL if rejected/rolled back
    old_success_rate REAL,
    new_success_rate REAL,
    old_latency_p90  REAL,
    new_latency_p90  REAL
);

CREATE INDEX IF NOT EXISTS idx_executions_tool_time
    ON tool_executions(tool_name, timestamp);

CREATE INDEX IF NOT EXISTS idx_improvement_tool
    ON improvement_history(tool_name, timestamp);
"""


# ── Thread-local connection pool ──────────────────────────────────────────────

_local = threading.local()
_db_path: Path = Path("aria.db")


def init_db(db_path: Path) -> None:
    """Initialize the database at the given path. Call once at startup."""
    global _db_path
    _db_path = db_path
    with get_connection() as conn:
        conn.executescript(_SCHEMA)


@contextmanager
def get_connection() -> Generator[sqlite3.Connection, None, None]:
    """
    Yield a thread-local SQLite connection.
    Uses WAL mode for concurrent reads from TUI + writes from agent.
    """
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(
            str(_db_path),
            detect_types=sqlite3.PARSE_DECLTYPES,
            check_same_thread=False,
        )
        _local.conn.row_factory = sqlite3.Row
    try:
        yield _local.conn
        _local.conn.commit()
    except Exception:
        _local.conn.rollback()
        raise


# ── Write helpers ─────────────────────────────────────────────────────────────

def insert_execution(
    *,
    tool_name: str,
    timestamp: float,
    success: bool,
    latency_seconds: float,
    input_hash: str | None = None,
    output_quality_score: float = 0.0,
    error_message: str | None = None,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO tool_executions
                (tool_name, timestamp, success, latency_seconds,
                 input_hash, output_quality_score, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tool_name,
                timestamp,
                1 if success else 0,
                latency_seconds,
                input_hash,
                output_quality_score,
                error_message,
            ),
        )


def insert_improvement(
    *,
    tool_name: str,
    timestamp: float,
    status: str,
    reason: str | None = None,
    git_commit_hash: str | None = None,
    old_success_rate: float | None = None,
    new_success_rate: float | None = None,
    old_latency_p90: float | None = None,
    new_latency_p90: float | None = None,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO improvement_history
                (tool_name, timestamp, status, reason, git_commit_hash,
                 old_success_rate, new_success_rate, old_latency_p90, new_latency_p90)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tool_name,
                timestamp,
                status,
                reason,
                git_commit_hash,
                old_success_rate,
                new_success_rate,
                old_latency_p90,
                new_latency_p90,
            ),
        )


# ── Read helpers ──────────────────────────────────────────────────────────────

@dataclass
class ToolStats:
    tool_name: str
    total_executions: int
    success_count: int
    failure_count: int
    success_rate: float
    avg_latency: float
    p90_latency: float
    last_seen: float | None


def get_tool_stats(tool_name: str, window: int = 100) -> ToolStats | None:
    """
    Return rolling statistics for a tool over the last `window` executions.
    Returns None if the tool has never been executed.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT success, latency_seconds, timestamp
            FROM tool_executions
            WHERE tool_name = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (tool_name, window),
        ).fetchall()

    if not rows:
        return None

    successes = sum(r["success"] for r in rows)
    latencies = sorted(r["latency_seconds"] for r in rows)
    n = len(rows)
    p90_idx = int(0.9 * n)
    p90 = latencies[min(p90_idx, n - 1)]

    return ToolStats(
        tool_name=tool_name,
        total_executions=n,
        success_count=successes,
        failure_count=n - successes,
        success_rate=successes / n,
        avg_latency=sum(latencies) / n,
        p90_latency=p90,
        last_seen=rows[0]["timestamp"],
    )


def get_all_tool_stats(window: int = 100) -> list[ToolStats]:
    """Return stats for every tool that has been executed."""
    with get_connection() as conn:
        names = conn.execute(
            "SELECT DISTINCT tool_name FROM tool_executions"
        ).fetchall()

    return [
        stats
        for name in names
        if (stats := get_tool_stats(name["tool_name"], window)) is not None
    ]


def get_recent_failures(tool_name: str, limit: int = 5) -> list[dict]:
    """Return the most recent failure records for a tool (for LLM context)."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT timestamp, latency_seconds, error_message, input_hash
            FROM tool_executions
            WHERE tool_name = ? AND success = 0
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (tool_name, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_improvement_history(tool_name: str | None = None, limit: int = 20) -> list[dict]:
    """Return improvement history, optionally filtered by tool."""
    with get_connection() as conn:
        if tool_name:
            rows = conn.execute(
                """
                SELECT * FROM improvement_history
                WHERE tool_name = ?
                ORDER BY timestamp DESC LIMIT ?
                """,
                (tool_name, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM improvement_history
                ORDER BY timestamp DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]
