import json
import logging
import time
from aria.metrics.db import get_connection
from aria.memory.store import update_derived_stats

logger = logging.getLogger(__name__)

def compress_failure_history(db_path: str = "") -> dict:
    """
    1. GROUP BY traceback_signature on failure_history.
    2. For each group: upsert into failure_patterns
    3. Determine resolution status.
    4. Update failure_history derived stats.
    Returns a summary dict.
    """
    summary = {
        "patterns_created": 0,
        "patterns_updated": 0,
        "patterns_resolved": 0
    }

    with get_connection() as conn:
        # Group failure_history by signature
        groups = conn.execute("""
            SELECT 
                traceback_signature,
                COUNT(*) as occurrence_count,
                MIN(timestamp) as first_seen,
                MAX(timestamp) as last_seen
            FROM failure_history
            GROUP BY traceback_signature
        """).fetchall()

        for group in groups:
            sig = group["traceback_signature"]
            occurrence_count = group["occurrence_count"]
            first_seen = group["first_seen"]
            last_seen = group["last_seen"]

            # Get distinct tool names for this signature
            tools_rows = conn.execute(
                "SELECT DISTINCT tool_name FROM failure_history WHERE traceback_signature = ?", 
                (sig,)
            ).fetchall()
            tool_names = json.dumps([r["tool_name"] for r in tools_rows])

            # Get representative failure ID (earliest instance)
            rep_row = conn.execute(
                "SELECT id FROM failure_history WHERE traceback_signature = ? ORDER BY timestamp ASC LIMIT 1",
                (sig,)
            ).fetchone()
            representative_failure_id = rep_row["id"] if rep_row else None

            # Check if this pattern is resolved
            # It is resolved if there is a deployed improvement for ANY failure in this group
            # AND the improvement timestamp is >= the pattern's last_seen timestamp
            resolved_by_id = None
            status = 'active'
            
            improvements = conn.execute("""
                SELECT ih.id, ih.timestamp 
                FROM improvement_history ih
                JOIN failure_history fh ON ih.triggering_failure_id = fh.id
                WHERE fh.traceback_signature = ? AND ih.result = 'deployed'
                ORDER BY ih.timestamp DESC
                LIMIT 1
            """, (sig,)).fetchone()
            
            if improvements:
                imp_ts = improvements["timestamp"]
                # Cast to float to handle SQLite returning ISO strings instead of REAL in some edge cases
                try:
                    if float(imp_ts) >= float(last_seen) - 0.001:
                        status = 'resolved'
                        resolved_by_id = improvements["id"]
                except (ValueError, TypeError):
                    pass

            # Check if pattern already exists in failure_patterns
            existing = conn.execute(
                "SELECT id, status FROM failure_patterns WHERE traceback_signature = ?",
                (sig,)
            ).fetchone()

            if existing:
                # Update existing pattern
                conn.execute("""
                    UPDATE failure_patterns 
                    SET representative_failure_id = ?, tool_names = ?, occurrence_count = ?, 
                        first_seen = ?, last_seen = ?, status = ?, resolved_by_improvement_id = ?
                    WHERE traceback_signature = ?
                """, (representative_failure_id, tool_names, occurrence_count, first_seen, last_seen, status, resolved_by_id, sig))
                summary["patterns_updated"] += 1
            else:
                # Insert new pattern
                conn.execute("""
                    INSERT INTO failure_patterns (
                        traceback_signature, representative_failure_id, tool_names, 
                        occurrence_count, first_seen, last_seen, status, resolved_by_improvement_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (sig, representative_failure_id, tool_names, occurrence_count, first_seen, last_seen, status, resolved_by_id))
                summary["patterns_created"] += 1

            if status == 'resolved':
                summary["patterns_resolved"] += 1

            # Push occurrence_count/last_seen back onto matching failure_history rows
            fail_ids = conn.execute(
                "SELECT id FROM failure_history WHERE traceback_signature = ?",
                (sig,)
            ).fetchall()
            
            for f_row in fail_ids:
                # The prompt explicitly authorizes this via update_derived_stats
                update_derived_stats(
                    table_name="failure_history", 
                    row_id=f_row["id"], 
                    stats={
                        "occurrence_count": occurrence_count,
                        "last_seen": last_seen
                    }
                )

    return summary
