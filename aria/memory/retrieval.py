from __future__ import annotations

from rapidfuzz import fuzz
from aria.metrics.db import get_connection
from aria.memory.store import normalize_traceback

def find_similar_failures(
    tool_name: str, 
    error_type: str,
    error_message: str, 
    stack_trace: str,
    top_k: int = 5
) -> list[dict]:
    """
    Two-tier search to find similar past failures.
    Tier 1: Exact traceback signature match.
    Tier 2: Fuzzy match on error_message within the same tool.
    """
    sig = normalize_traceback(error_type, stack_trace)

    with get_connection() as conn:
        # Tier 1: exact signature match (same bug pattern, any tool)
        exact_rows = conn.execute(
            """
            SELECT * FROM failure_history
            WHERE traceback_signature = ?
            ORDER BY memory_score DESC, timestamp DESC LIMIT ?
            """, 
            (sig, top_k)
        ).fetchall()
        
        exact = [dict(r) for r in exact_rows]
        
        if len(exact) >= top_k:
            return exact

        # Tier 2: fuzzy match on error_message within the same tool, backfilling
        candidates_rows = conn.execute(
            """
            SELECT * FROM failure_history
            WHERE tool_name = ? AND traceback_signature != ?
            ORDER BY memory_score DESC, timestamp DESC LIMIT 200
            """, 
            (tool_name, sig)
        ).fetchall()
        
        candidates = [dict(r) for r in candidates_rows]
        
    scored = sorted(
        candidates,
        key=lambda r: (fuzz.token_set_ratio(error_message, r['error_message']), r.get('memory_score') or 0.0, r.get('timestamp') or 0.0),
        reverse=True
    )
    
    return (exact + scored)[:top_k]

def find_successful_fixes(tool_name: str, traceback_signature: str | None = None, top_k: int = 3) -> list[dict]:
    with get_connection() as conn:
        if traceback_signature:
            # Direct lineage: fixes whose triggering_failure had this signature
            rows = conn.execute("""
                SELECT ih.* FROM improvement_history ih
                JOIN failure_history fh ON ih.triggering_failure_id = fh.id
                WHERE fh.traceback_signature = ? AND ih.result = 'deployed'
                ORDER BY ih.memory_score DESC, ih.fitness_delta DESC, ih.timestamp DESC LIMIT ?
            """, (traceback_signature, top_k)).fetchall()
            if rows:
                return [dict(r) for r in rows]

        # Fallback: best historical fixes for this tool generally
        rows = conn.execute("""
            SELECT * FROM improvement_history
            WHERE tool_name = ? AND result = 'deployed'
            ORDER BY memory_score DESC, fitness_delta DESC, timestamp DESC LIMIT ?
        """, (tool_name, top_k)).fetchall()
        return [dict(r) for r in rows]

def find_failed_fixes(tool_name: str, traceback_signature: str | None = None, top_k: int = 3) -> list[dict]:
    with get_connection() as conn:
        if traceback_signature:
            rows = conn.execute("""
                SELECT ih.* FROM improvement_history ih
                JOIN failure_history fh ON ih.triggering_failure_id = fh.id
                WHERE fh.traceback_signature = ? AND ih.result IN ('rejected', 'rolled_back')
                ORDER BY ih.memory_score DESC, ih.timestamp DESC LIMIT ?
            """, (traceback_signature, top_k)).fetchall()
            if rows:
                return [dict(r) for r in rows]
        
        # Fallback: worst historical fixes for this tool generally
        rows = conn.execute("""
            SELECT * FROM improvement_history
            WHERE tool_name = ? AND result IN ('rejected', 'rolled_back')
            ORDER BY memory_score DESC, timestamp DESC LIMIT ?
        """, (tool_name, top_k)).fetchall()
        return [dict(r) for r in rows]
