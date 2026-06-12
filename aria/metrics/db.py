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
    timestamp           REAL    NOT NULL,
    success             INTEGER NOT NULL,
    latency_seconds     REAL    NOT NULL,
    input_hash          TEXT,
    output_quality_score REAL   DEFAULT 0.0,
    error_message       TEXT,
    memory_mb           REAL    DEFAULT 0.0,
    tokens_used         INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS review_queue (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT    NOT NULL,
    tool_name           TEXT    NOT NULL,
    timestamp           REAL    NOT NULL,
    combat_report       TEXT    NOT NULL,
    generated_code      TEXT    NOT NULL,
    status              TEXT    DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS improvement_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name       TEXT    NOT NULL,
    timestamp       REAL    NOT NULL,
    status          TEXT    NOT NULL,
    reason          TEXT,
    git_commit_hash TEXT,
    old_success_rate REAL,
    new_success_rate REAL,
    old_latency_p90  REAL,
    new_latency_p90  REAL,
    old_memory_mb    REAL DEFAULT 0.0,
    new_memory_mb    REAL DEFAULT 0.0,
    old_tokens_used  INTEGER DEFAULT 0,
    new_tokens_used  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS cycle_traces (
    cycle_id            TEXT PRIMARY KEY,
    timestamp           REAL NOT NULL,
    component           TEXT NOT NULL,
    trigger             TEXT,
    llm_prompt_tokens   INTEGER DEFAULT 0,
    llm_response_tokens INTEGER DEFAULT 0,
    candidates_generated INTEGER DEFAULT 0,
    candidates_rejected TEXT,
    candidates_deployed INTEGER DEFAULT 0,
    cycle_outcome       TEXT NOT NULL,
    duration_seconds    REAL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS detailed_traces (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id            TEXT,
    timestamp           REAL NOT NULL,
    component           TEXT NOT NULL,
    event_type          TEXT NOT NULL,
    details             TEXT
);

CREATE TRIGGER IF NOT EXISTS prevent_cycle_traces_update
BEFORE UPDATE ON cycle_traces
BEGIN
    SELECT RAISE(ABORT, 'cycle_traces is append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_cycle_traces_delete
BEFORE DELETE ON cycle_traces
BEGIN
    SELECT RAISE(ABORT, 'cycle_traces is append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_detailed_traces_update
BEFORE UPDATE ON detailed_traces
BEGIN
    SELECT RAISE(ABORT, 'detailed_traces is append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_detailed_traces_delete
BEFORE DELETE ON detailed_traces
BEGIN
    SELECT RAISE(ABORT, 'detailed_traces is append-only');
END;

CREATE INDEX IF NOT EXISTS idx_executions_tool_time
    ON tool_executions(tool_name, timestamp);

CREATE INDEX IF NOT EXISTS idx_improvement_tool
    ON improvement_history(tool_name, timestamp);

CREATE INDEX IF NOT EXISTS idx_cycle_traces_time
    ON cycle_traces(timestamp);
"""

from aria.config import settings

_local = threading.local()
_db_path: Path = settings.db_path


def init_db(db_path: Path) -> None:
    global _db_path
    _db_path = db_path
    with get_connection() as conn:
        conn.executescript(_SCHEMA)


@contextmanager
def get_connection() -> Generator[sqlite3.Connection, None, None]:
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


def insert_execution(
    *,
    tool_name: str,
    timestamp: float,
    success: bool,
    latency_seconds: float,
    input_hash: str | None = None,
    output_quality_score: float = 0.0,
    error_message: str | None = None,
    memory_mb: float = 0.0,
    tokens_used: int = 0,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO tool_executions
                (tool_name, timestamp, success, latency_seconds,
                 input_hash, output_quality_score, error_message, memory_mb, tokens_used)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tool_name, timestamp, 1 if success else 0, latency_seconds,
                input_hash, output_quality_score, error_message, memory_mb, tokens_used
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
    old_memory_mb: float | None = None,
    new_memory_mb: float | None = None,
    old_tokens_used: int | None = None,
    new_tokens_used: int | None = None,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO improvement_history
                (tool_name, timestamp, status, reason, git_commit_hash,
                 old_success_rate, new_success_rate, old_latency_p90, new_latency_p90,
                 old_memory_mb, new_memory_mb, old_tokens_used, new_tokens_used)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tool_name, timestamp, status, reason, git_commit_hash,
                old_success_rate, new_success_rate, old_latency_p90, new_latency_p90,
                old_memory_mb, new_memory_mb, old_tokens_used, new_tokens_used
            ),
        )


def insert_review_queue(
    *,
    session_id: str,
    tool_name: str,
    timestamp: float,
    combat_report: str,
    generated_code: str,
    status: str = "pending",
) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO review_queue
                (session_id, tool_name, timestamp, combat_report, generated_code, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, tool_name, timestamp, combat_report, generated_code, status),
        )
        return cursor.lastrowid


def get_pending_reviews() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM review_queue WHERE status = 'pending' ORDER BY timestamp ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def update_review_status(review_id: int, status: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE review_queue SET status = ? WHERE id = ?",
            (status, review_id),
        )

def insert_cycle_trace(
    *,
    cycle_id: str,
    timestamp: float,
    component: str,
    trigger: str | None,
    llm_prompt_tokens: int,
    llm_response_tokens: int,
    candidates_generated: int,
    candidates_rejected: str,
    candidates_deployed: int,
    cycle_outcome: str,
    duration_seconds: float,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO cycle_traces
                (cycle_id, timestamp, component, trigger, llm_prompt_tokens,
                 llm_response_tokens, candidates_generated, candidates_rejected,
                 candidates_deployed, cycle_outcome, duration_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cycle_id, timestamp, component, trigger, llm_prompt_tokens,
                llm_response_tokens, candidates_generated, candidates_rejected,
                candidates_deployed, cycle_outcome, duration_seconds
            ),
        )


def insert_detailed_trace(
    *,
    cycle_id: str | None,
    timestamp: float,
    component: str,
    event_type: str,
    details: str,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO detailed_traces
                (cycle_id, timestamp, component, event_type, details)
            VALUES (?, ?, ?, ?, ?)
            """,
            (cycle_id, timestamp, component, event_type, details),
        )


def query_cycle_traces(
    limit: int = 10,
    component: str | None = None,
    outcome: str | None = None,
    tool: str | None = None,
) -> list[dict]:
    query = "SELECT * FROM cycle_traces WHERE 1=1"
    params = []

    if component:
        query += " AND component = ?"
        params.append(component)
    
    if outcome:
        query += " AND cycle_outcome = ?"
        params.append(outcome)

    if tool:
        query += " AND trigger LIKE ?"
        params.append(f"%{tool}%")
        
    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)
    
    with get_connection() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
        return [dict(r) for r in rows]



@dataclass
class ToolStats:
    tool_name: str
    total_executions: int
    success_count: int
    failure_count: int
    success_rate: float
    avg_latency: float
    p90_latency: float
    avg_memory_mb: float
    avg_tokens_used: float
    last_seen: float | None


def get_tool_stats(tool_name: str, window: int = 100) -> ToolStats | None:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT success, latency_seconds, timestamp, memory_mb, tokens_used
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
    memories = [r["memory_mb"] for r in rows]
    tokens = [r["tokens_used"] for r in rows]
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
        avg_memory_mb=sum(memories) / n,
        avg_tokens_used=sum(tokens) / n,
        last_seen=rows[0]["timestamp"],
    )


def get_all_tool_stats(window: int = 100) -> list[ToolStats]:
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
    with get_connection() as conn:
        if tool_name:
            rows = conn.execute(
                "SELECT * FROM improvement_history WHERE tool_name = ? ORDER BY timestamp DESC LIMIT ?",
                (tool_name, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM improvement_history ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]
