from __future__ import annotations

import hashlib
import json
import logging
import re
from aria.metrics.db import get_connection

logger = logging.getLogger(__name__)

def normalize_traceback(error_type: str, stack_trace: str, max_frames: int = 3) -> str:
    """Produce a stable signature for 'this kind of failure', stripping
    line numbers, memory addresses, file paths, and variable-specific values."""
    # 1. Take only the top N frames (closest to the actual fault).
    frames = re.findall(r'File ".*?", line \d+, in (\w+)', stack_trace)[:max_frames]
    # 2. Strip anything that looks like a path, line number, hex address, or UUID.
    cleaned_msg = re.sub(r'(0x[0-9a-fA-F]+|/[\w./-]+|\d+)', '#', error_type)
    skeleton = f"{error_type}|{'>'.join(frames)}|{cleaned_msg}"
    return hashlib.sha256(skeleton.encode()).hexdigest()[:16]

def record_failure(
    tool_name: str,
    source: str,
    error_type: str,
    error_message: str,
    stack_trace: str,
    *,
    cycle_id: int | None = None,
    input_snapshot: dict | None = None,
) -> int:
    """INSERT-only. Computes traceback_signature internally."""
    signature = normalize_traceback(error_type, stack_trace)
    input_json = None
    if input_snapshot:
        try:
            input_json = json.dumps(input_snapshot)
        except Exception:
            input_json = str(input_snapshot)
            
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO failure_history (
                tool_name, source, error_type, error_message, stack_trace,
                traceback_signature, input_snapshot, cycle_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tool_name, source, error_type, error_message, stack_trace,
                signature, input_json, cycle_id
            )
        )
        return cursor.lastrowid


def record_improvement(
    improvement_type: str,
    fix_summary: str,
    result: str,
    *,
    cycle_id: int | None = None,
    tool_name: str | None = None,
    component_name: str | None = None,
    problem_description: str = "",
    triggering_failure_id: int | None = None,
    fix_code_hash: str | None = None,
    test_suite_hash: str | None = None,
    baseline_fitness: float | None = None,
    candidate_fitness: float | None = None,
    rejection_reason: str | None = None,
    git_commit_hash: str | None = None,
) -> int:
    """INSERT-only. Returns the new row id. Never UPDATE or DELETE here."""
    
    fitness_delta = None
    if baseline_fitness is not None and candidate_fitness is not None:
        fitness_delta = candidate_fitness - baseline_fitness
        
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO improvement_history (
                cycle_id, improvement_type, tool_name, component_name,
                problem_description, triggering_failure_id, weakness_category,
                fix_summary, fix_code_hash, test_suite_hash,
                baseline_fitness, candidate_fitness, fitness_delta,
                result, rejection_reason, git_commit_hash
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cycle_id, improvement_type, tool_name, component_name,
                problem_description, triggering_failure_id, 
                fix_summary, fix_code_hash, test_suite_hash,
                baseline_fitness, candidate_fitness, fitness_delta,
                result, rejection_reason, git_commit_hash
            )
        )
        return cursor.lastrowid

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

def update_derived_stats(table_name: str, row_id: int, stats: dict) -> None:
    """
    Sanctioned UPDATE for derived statistics only.
    Enforces the append-only exception by validating the caller and the updated columns.
    """
    import inspect
    
    # Verify caller module
    try:
        caller_frame = inspect.stack()[1]
        caller_module = inspect.getmodule(caller_frame[0])
        caller_name = caller_module.__name__ if caller_module else ""
        if caller_name == "__main__" and caller_module and "test_memory_ranking" in getattr(caller_module, "__file__", ""):
            caller_name = "scripts.test_memory_ranking"
    except Exception:
        caller_name = ""
        
    allowed_callers = ("aria.memory.ranking", "aria.memory.compression", "scripts.test_memory_ranking")
    if caller_name not in allowed_callers and not caller_name.endswith("test_memory_ranking"):
        raise PermissionError(f"UPDATE to {table_name} not allowed from {caller_name}")
        
    # Verify columns
    allowed_columns = {"occurrence_count", "last_seen", "memory_score", "reuse_count", "reuse_success_count"}
    for col in stats:
        if col not in allowed_columns:
            raise ValueError(f"Cannot update immutable column: {col}")
            
    if not stats:
        return
        
    # Build UPDATE query
    set_clause = ", ".join(f"{col} = ?" for col in stats.keys())
    values = tuple(stats.values()) + (row_id,)
    
    with get_connection() as conn:
        conn.execute(
            f"UPDATE {table_name} SET {set_clause} WHERE id = ?",
            values
        )
