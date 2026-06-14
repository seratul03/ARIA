import json
from aria.metrics.db import get_connection

def most_common_failures(limit=10) -> list[dict]:
    """From failure_patterns, ordered by occurrence_count DESC, status='active' first."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT 
                traceback_signature, 
                tool_names, 
                occurrence_count, 
                first_seen, 
                last_seen, 
                status 
            FROM failure_patterns 
            ORDER BY 
                CASE WHEN status = 'active' THEN 0 ELSE 1 END ASC,
                occurrence_count DESC, 
                last_seen DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(row) for row in rows]

def most_successful_fixes(limit=10) -> list[dict]:
    """From improvement_history WHERE result='deployed', ordered by fitness_delta DESC."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT 
                id,
                tool_name, 
                fix_summary, 
                fitness_delta, 
                memory_score,
                timestamp 
            FROM improvement_history 
            WHERE result = 'deployed'
            ORDER BY fitness_delta DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(row) for row in rows]

def worst_tool() -> dict:
    """
    Combine:
      - SUM(occurrence_count) of active failure_patterns per tool
      - AVG(memory_score) of improvement_history per tool
    into a single 'pain score' per tool; return the highest.
    Pain Score = (Failure Occurrence Count) * (1.0 + (1.0 - Avg Fix Reliability))
    """
    with get_connection() as conn:
        # Get failure occurrences per tool
        # tool_names is a JSON array in failure_patterns, so we need to parse it in Python
        # or we can sum occurrence_count per tool from failure_history where pattern is active.
        # Let's use failure_history joined with failure_patterns.
        failure_rows = conn.execute("""
            SELECT 
                fh.tool_name, 
                COUNT(*) as failure_count
            FROM failure_history fh
            JOIN failure_patterns fp ON fh.traceback_signature = fp.traceback_signature
            WHERE fp.status = 'active'
            GROUP BY fh.tool_name
        """).fetchall()
        
        failure_counts = {r["tool_name"]: r["failure_count"] for r in failure_rows}

        # Get avg memory score per tool from deployed improvements
        fix_rows = conn.execute("""
            SELECT 
                tool_name, 
                AVG(memory_score) as avg_memory_score
            FROM improvement_history
            WHERE result = 'deployed'
            GROUP BY tool_name
        """).fetchall()
        
        avg_memory_scores = {r["tool_name"]: r["avg_memory_score"] for r in fix_rows}
        
        # Calculate Pain Score
        all_tools = set(failure_counts.keys()).union(set(avg_memory_scores.keys()))
        if not all_tools:
            return {}
            
        tool_scores = []
        for tool in all_tools:
            f_count = failure_counts.get(tool, 0)
            avg_score = avg_memory_scores.get(tool)
            if avg_score is None:
                avg_score = 1.0 # Default to perfect reliability if no fixes
                
            # Pain Score formula: f_count * (1.0 + (1.0 - avg_score))
            # If avg_score is 1.0 (perfect), pain multiplier is 1.0
            # If avg_score is 0.0 (terrible), pain multiplier is 2.0
            pain_score = f_count * (1.0 + (1.0 - avg_score))
            
            tool_scores.append({
                "tool_name": tool,
                "pain_score": pain_score,
                "failure_count": f_count,
                "avg_fix_reliability": avg_score
            })
            
        # Return highest
        tool_scores.sort(key=lambda x: x["pain_score"], reverse=True)
        return tool_scores[0] if tool_scores else {}

def fix_reliability_report() -> list[dict]:
    """Per fix pattern: reuse_count vs reuse_success_count."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT 
                id,
                tool_name,
                fix_summary,
                reuse_count,
                reuse_success_count,
                memory_score,
                timestamp
            FROM improvement_history
            WHERE result = 'deployed' AND reuse_count > 0
            ORDER BY memory_score DESC
        """).fetchall()
        
        report = []
        for row in rows:
            r_dict = dict(row)
            r_dict["survival_percentage"] = (r_dict["reuse_success_count"] / r_dict["reuse_count"]) * 100.0 if r_dict["reuse_count"] > 0 else 0.0
            report.append(r_dict)
            
        return report
