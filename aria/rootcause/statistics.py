import json
import time
from collections import defaultdict
from aria.metrics.db import get_connection

def root_cause_breakdown(weight_by: str = "occurrence_count", active_only: bool = True) -> dict[str, float]:
    """
    Returns {category_name: fraction} summing to 1.0, computed over failure_patterns.
    weight_by='occurrence_count' (default) -> weighted by how often each pattern fires.
    weight_by='pattern_count' -> each distinct pattern counts equally.
    """
    with get_connection() as conn:
        query = "SELECT root_cause_category, occurrence_count FROM failure_patterns WHERE root_cause_category IS NOT NULL"
        if active_only:
            query += " AND status = 'active'"
            
        cursor = conn.execute(query)
        rows = cursor.fetchall()
        
    if not rows:
        return {}
        
    breakdown = defaultdict(float)
    total_weight = 0.0
    
    for row in rows:
        category = row["root_cause_category"]
        weight = row["occurrence_count"] if weight_by == "occurrence_count" else 1.0
        breakdown[category] += weight
        total_weight += weight
        
    if total_weight == 0.0:
        return {}
        
    return {cat: val / total_weight for cat, val in breakdown.items()}


def root_cause_breakdown_by_tool() -> dict[str, dict[str, float]]:
    """
    {tool_name: {category: fraction}} 
    A pattern affecting 2 tools contributes its full occurrence_count to EACH tool's breakdown.
    """
    with get_connection() as conn:
        query = "SELECT root_cause_category, occurrence_count, tool_names FROM failure_patterns WHERE root_cause_category IS NOT NULL AND status = 'active'"
        cursor = conn.execute(query)
        rows = cursor.fetchall()
        
    tool_raw_counts = defaultdict(lambda: defaultdict(float))
    tool_totals = defaultdict(float)
    
    for row in rows:
        category = row["root_cause_category"]
        count = row["occurrence_count"]
        
        try:
            tools = json.loads(row["tool_names"])
        except Exception:
            tools = []
            
        for t in tools:
            tool_raw_counts[t][category] += count
            tool_totals[t] += count
            
    result = {}
    for t, cat_counts in tool_raw_counts.items():
        total = tool_totals[t]
        if total > 0:
            result[t] = {cat: val / total for cat, val in cat_counts.items()}
            
    return result


def root_cause_trend(window_days: int = 30) -> dict[str, list[tuple[str, float]]]:
    """
    Per category, a time series of breakdown over rolling windows.
    Returns: {"Network": [["2026-05-15", 0.30], ["2026-06-01", 0.40]], ...}
    """
    now = time.time()
    start_time = now - (window_days * 86400)
    
    with get_connection() as conn:
        query = """
            SELECT 
                fh.timestamp, 
                fp.root_cause_category
            FROM failure_history fh
            JOIN failure_patterns fp ON fh.traceback_signature = fp.traceback_signature
            WHERE fh.timestamp >= ? AND fp.root_cause_category IS NOT NULL
        """
        cursor = conn.execute(query, (start_time,))
        rows = cursor.fetchall()
        
    if not rows:
        return {}
        
    # Bucket into 6 equal segments of `window_days / 6` days
    num_buckets = 6
    bucket_duration = (window_days * 86400) / num_buckets
    
    from datetime import datetime
    bucket_starts = []
    for i in range(num_buckets):
        b_start = start_time + (i * bucket_duration)
        bucket_starts.append(datetime.fromtimestamp(b_start).strftime("%Y-%m-%d"))
        
    bucket_counts = [defaultdict(int) for _ in range(num_buckets)]
    bucket_totals = [0 for _ in range(num_buckets)]
    
    categories = set()
    
    for row in rows:
        try:
            ts = float(row["timestamp"])
        except (ValueError, TypeError):
            continue
            
        cat = row["root_cause_category"]
        categories.add(cat)
        
        # Find which bucket
        bucket_idx = int((ts - start_time) / bucket_duration)
        if bucket_idx >= num_buckets:
            bucket_idx = num_buckets - 1
        if bucket_idx < 0:
            bucket_idx = 0
            
        bucket_counts[bucket_idx][cat] += 1
        bucket_totals[bucket_idx] += 1
        
    # Format output
    trend = defaultdict(list)
    for cat in categories:
        for i in range(num_buckets):
            total = bucket_totals[i]
            frac = bucket_counts[i][cat] / total if total > 0 else 0.0
            trend[cat].append([bucket_starts[i], round(frac, 4)])
            
    return dict(trend)
