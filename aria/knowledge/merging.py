import sqlite3
from typing import Dict, List, Tuple
from rapidfuzz import fuzz

from aria.knowledge.confidence import recompute_confidence, update_rule_status
from aria.knowledge.export import export_rules_json

RULE_SIMILARITY_THRESHOLD = 80.0

def find_duplicate_rules(db_path: str) -> List[Tuple[dict, dict]]:
    """
    For each category, compare all status IN ('candidate','active') rules pairwise.
    """
    pairs = []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        
        categories = conn.execute("SELECT DISTINCT category FROM engineering_rules WHERE status IN ('candidate', 'active')").fetchall()
        
        for cat_row in categories:
            cat = cat_row["category"]
            rules = conn.execute("SELECT * FROM engineering_rules WHERE status IN ('candidate', 'active') AND category = ?", (cat,)).fetchall()
            rules = [dict(r) for r in rules]
            
            for i in range(len(rules)):
                for j in range(i + 1, len(rules)):
                    r1 = rules[i]
                    r2 = rules[j]
                    score = fuzz.token_set_ratio(r1["rule_text"], r2["rule_text"])
                    if score >= RULE_SIMILARITY_THRESHOLD:
                        pairs.append((r1, r2))
    return pairs

def merge_rule_pair(winner_id: int, loser_id: int, db_path: str) -> None:
    """
    Repoint applications, sum evidence, update status and confidence.
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        winner = conn.execute("SELECT applications_count, success_count FROM engineering_rules WHERE id = ?", (winner_id,)).fetchone()
        loser = conn.execute("SELECT applications_count, success_count FROM engineering_rules WHERE id = ?", (loser_id,)).fetchone()
        
        if not winner or not loser:
            return
            
        new_apps = winner["applications_count"] + loser["applications_count"]
        new_succ = winner["success_count"] + loser["success_count"]
        
        # Repoint applications
        conn.execute("UPDATE rule_applications SET rule_id = ? WHERE rule_id = ?", (winner_id, loser_id))
        
        # Update winner counters
        conn.execute("UPDATE engineering_rules SET applications_count = ?, success_count = ? WHERE id = ?", (new_apps, new_succ, winner_id))
        
        # Deprecate loser
        conn.execute("UPDATE engineering_rules SET status = 'merged', superseded_by = ? WHERE id = ?", (winner_id, loser_id))
        
    # Recompute
    recompute_confidence(winner_id, db_path)
    update_rule_status(winner_id, db_path)

def merge_duplicate_rules(db_path: str) -> Dict[str, int]:
    """
    Find pairs, Union-Find to group, merge.
    """
    pairs = find_duplicate_rules(db_path)
    
    # Union-find over pairs
    parent = {}
    
    def find(i):
        if parent.setdefault(i, i) == i:
            return i
        parent[i] = find(parent[i])
        return parent[i]
        
    def union(i, j):
        root_i = find(i)
        root_j = find(j)
        if root_i != root_j:
            parent[root_i] = root_j

    rule_meta = {}
    
    for r1, r2 in pairs:
        id1, id2 = r1["id"], r2["id"]
        rule_meta[id1] = r1
        rule_meta[id2] = r2
        union(id1, id2)
        
    groups = {}
    for rule_id in rule_meta:
        root = find(rule_id)
        groups.setdefault(root, []).append(rule_id)
        
    stats = {"merge_groups": 0, "rules_merged": 0}
    
    for root, group in groups.items():
        if len(group) > 1:
            stats["merge_groups"] += 1
            # Find winner
            # Tiebreak by confidence
            # applications_count DESC, confidence DESC
            sorted_group = sorted(group, key=lambda x: (rule_meta[x]["applications_count"], rule_meta[x]["confidence"]), reverse=True)
            winner_id = sorted_group[0]
            losers = sorted_group[1:]
            
            for loser_id in losers:
                merge_rule_pair(winner_id, loser_id, db_path)
                stats["rules_merged"] += 1
                
    export_rules_json(db_path)
    return stats
