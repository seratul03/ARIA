"""
aria/knowledge/confidence.py
──────────────────────────────
Confidence scoring and status lifecycle for engineering rules.
"""

import math
import sqlite3
import logging

logger = logging.getLogger(__name__)

PRIOR_WEIGHT = 5.0                  # "virtual applications" the initial confidence is worth
RULE_PROMOTION_THRESHOLD = 0.65     # candidate -> active
RULE_DEPRECATION_THRESHOLD = 0.35   # active -> deprecated (after MIN_APPLICATIONS_FOR_DEPRECATION)
MIN_APPLICATIONS_FOR_PROMOTION = 3
MIN_APPLICATIONS_FOR_DEPRECATION = 5
MIN_APPLICATIONS_FOR_REFINEMENT = 8

def initial_confidence(source: dict, llm_confidence: float) -> float:
    """
    Blend of:
      - llm_confidence (the LLM's self-rated confidence at extraction time)
      - a durability bonus: +0.1 if source came from a 'resolved' architectural_pattern
        (cross-tool evidence) vs an 'implemented' hypothesis (single-tool evidence)
      - an evidence-volume term: source's occurrence_count (via cluster/pattern),
        normalized the same way as Phase 1's frequency_component (log1p, capped)

    result = clamp(0.5*llm_confidence + 0.3*durability_bonus_normalized + 0.2*evidence_volume, 0.0, 1.0)
    """
    # Durability bonus
    durability_bonus_normalized = 0.0
    if source.get('source_type') == 'architectural_pattern':
        durability_bonus_normalized = 1.0 # Translates to +0.1 in the final formula because 0.3 * something. Actually, if bonus is +0.1 total, 0.3 * (1/3) is 0.1. Let's just use 1.0 for true, 0.0 for false. Wait, +0.1 if from architectural pattern. So 0.3 * durability_bonus_normalized = 0.1 -> durability = 0.333. Let's just say 1.0 means full bonus (0.3). The spec says +0.1. We can just do what the comment says exactly.
        
    if source.get('source_type') == 'architectural_pattern':
        durability_bonus = 0.1
    else:
        durability_bonus = 0.0
        
    # Evidence volume term
    occurrence_count = float(source.get('occurrence_count', 1.0))
    # log10 of occurrence count, capped at 10 for max bonus (log10(10) = 1)
    evidence_volume = min(math.log10(max(occurrence_count, 1.0)), 1.0)
    
    # Wait, the prompt says: result = clamp(0.5*llm_confidence + 0.3*durability_bonus_normalized + 0.2*evidence_volume, 0.0, 1.0)
    # If durability_bonus_normalized is 1.0 for arch pattern, then +0.3. The spec text says "a durability bonus: +0.1 if source came from a 'resolved' architectural_pattern". 
    # Let's align with the formula provided: 0.5 * llm + 0.3 * durability_bonus_normalized + 0.2 * evidence_volume.
    if source.get('source_type') == 'architectural_pattern':
        durability_bonus_normalized = 1.0
    else:
        durability_bonus_normalized = 0.0
        
    score = (0.5 * llm_confidence) + (0.3 * durability_bonus_normalized) + (0.2 * evidence_volume)
    return max(0.0, min(1.0, score))


def recompute_confidence(rule_id: int, db_path: str) -> float:
    """
    Bayesian shrinkage toward observed outcomes:

        confidence = (PRIOR_WEIGHT * initial_confidence_at_creation
                       + success_count) / (PRIOR_WEIGHT + applications_count)
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT initial_confidence, success_count, applications_count FROM engineering_rules WHERE id = ?", 
            (rule_id,)
        ).fetchone()
        
        if not row:
            raise ValueError(f"Rule {rule_id} not found")
            
        initial_conf = row["initial_confidence"]
        success_count = row["success_count"]
        apps_count = row["applications_count"]
        
    new_confidence = (PRIOR_WEIGHT * initial_conf + success_count) / (PRIOR_WEIGHT + apps_count)
    
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE engineering_rules SET confidence = ? WHERE id = ?", (new_confidence, rule_id))
        
    return new_confidence


def update_rule_status(rule_id: int, db_path: str) -> str:
    """
    Transitions status based on confidence and applications_count.
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT confidence, applications_count, status FROM engineering_rules WHERE id = ?", 
            (rule_id,)
        ).fetchone()
        
        if not row:
            raise ValueError(f"Rule {rule_id} not found")
            
        status = row["status"]
        apps_count = row["applications_count"]
        confidence = row["confidence"]
        
        new_status = status
        deprecation_reason = None
        
        if status == 'candidate' and apps_count >= MIN_APPLICATIONS_FOR_PROMOTION and confidence >= RULE_PROMOTION_THRESHOLD:
            new_status = 'active'
        elif status == 'active' and apps_count >= MIN_APPLICATIONS_FOR_DEPRECATION and confidence <= RULE_DEPRECATION_THRESHOLD:
            new_status = 'deprecated'
            deprecation_reason = 'confidence_below_threshold'
            
        if new_status != status:
            if deprecation_reason:
                conn.execute("UPDATE engineering_rules SET status = ?, deprecation_reason = ? WHERE id = ?", (new_status, deprecation_reason, rule_id))
            else:
                conn.execute("UPDATE engineering_rules SET status = ? WHERE id = ?", (new_status, rule_id))
                
        return new_status
