import json
import logging
from rapidfuzz import fuzz
from aria.metrics.db import get_connection

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.60

def cluster_patterns_by_category(db_path: str, category: str) -> dict:
    """
    1. Fetch all `failure_patterns` WHERE root_cause_category = category AND status='active'.
    2. Greedy single-link clustering on error_message using rapidfuzz.fuzz.token_set_ratio.
    3. For each cluster of size >= 2:
       - upsert into root_cause_clusters
    """
    with get_connection() as conn:
        # Join to get error_message from the representative failure
        query = """
            SELECT 
                p.id, 
                p.tool_names, 
                p.occurrence_count, 
                h.error_message
            FROM failure_patterns p
            JOIN failure_history h ON p.representative_failure_id = h.id
            WHERE p.root_cause_category = ? AND p.status = 'active'
        """
        rows = conn.execute(query, (category,)).fetchall()
        
    if not rows:
        return {"clusters_created": 0, "clusters_updated": 0, "lonely_patterns": 0}
        
    patterns = []
    for r in rows:
        try:
            tools = json.loads(r["tool_names"])
        except Exception:
            tools = []
        
        patterns.append({
            "id": r["id"],
            "error_message": r["error_message"] or "",
            "tools": tools,
            "occurrence_count": r["occurrence_count"]
        })
        
    # Greedy single-link clustering
    clusters = []
    
    for p in patterns:
        msg1 = p["error_message"]
        added_to_existing = False
        
        for c in clusters:
            for member in c:
                msg2 = member["error_message"]
                score = fuzz.token_set_ratio(msg1, msg2) / 100.0
                if score >= SIMILARITY_THRESHOLD:
                    c.append(p)
                    added_to_existing = True
                    break # Break inner loop, move to next pattern
            
            if added_to_existing:
                break
                
        if not added_to_existing:
            clusters.append([p])
            
    stats = {"clusters_created": 0, "clusters_updated": 0, "lonely_patterns": 0}
    
    with get_connection() as conn:
        for c in clusters:
            if len(c) < 2:
                stats["lonely_patterns"] += 1
                continue
                
            pattern_ids = sorted([p["id"] for p in c])
            all_tools = set()
            for p in c:
                all_tools.update(p["tools"])
            tool_names = sorted(list(all_tools))
            total_occ = sum(p["occurrence_count"] for p in c)
            
            existing_clusters = conn.execute(
                "SELECT id, pattern_ids FROM root_cause_clusters WHERE root_cause_category = ?", 
                (category,)
            ).fetchall()
            
            overlapping_cluster_id = None
            for ec in existing_clusters:
                try:
                    ec_pids = set(json.loads(ec["pattern_ids"]))
                except Exception:
                    continue
                    
                if ec_pids.intersection(set(pattern_ids)):
                    overlapping_cluster_id = ec["id"]
                    break
                    
            if overlapping_cluster_id:
                # Update existing cluster
                conn.execute("""
                    UPDATE root_cause_clusters
                    SET pattern_ids = ?, tool_names = ?, total_occurrences = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (json.dumps(pattern_ids), json.dumps(tool_names), total_occ, overlapping_cluster_id))
                stats["clusters_updated"] += 1
            else:
                # Insert new cluster
                conn.execute("""
                    INSERT INTO root_cause_clusters 
                    (root_cause_category, pattern_ids, tool_names, total_occurrences, similarity_threshold)
                    VALUES (?, ?, ?, ?, ?)
                """, (category, json.dumps(pattern_ids), json.dumps(tool_names), total_occ, SIMILARITY_THRESHOLD))
                stats["clusters_created"] += 1
                
    return stats

def cluster_all_categories(db_path: str) -> dict:
    from aria.rootcause.categories import CATEGORY_DESCRIPTIONS
    summary = {"clusters_created": 0, "clusters_updated": 0, "lonely_patterns": 0}
    
    for cat in CATEGORY_DESCRIPTIONS.keys():
        stats = cluster_patterns_by_category(db_path, cat)
        summary["clusters_created"] += stats["clusters_created"]
        summary["clusters_updated"] += stats["clusters_updated"]
        summary["lonely_patterns"] += stats["lonely_patterns"]
        
    return summary
