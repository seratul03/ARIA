## DAY 17 — Confidence Scoring Formula

### Objective
Define exactly how a rule's `confidence` is computed at creation and how it evolves — the formula that Days 18-24 all depend on.

### `aria/knowledge/confidence.py`

```python
# Tunable constants — document these in aria/knowledge/README.md
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
    Lands new rules in a 0.4-0.75 range typically — candidates, not yet active.
    """


def recompute_confidence(rule_id: int, db_path: str) -> float:
    """
    Bayesian shrinkage toward observed outcomes:

        confidence = (PRIOR_WEIGHT * initial_confidence_at_creation
                       + success_count) / (PRIOR_WEIGHT + applications_count)

    Note: `initial_confidence_at_creation` must be preserved separately from
    the live `confidence` column to make this formula well-defined as
    applications accumulate — store it once at INSERT time (Day 16) in a
    column... but engineering_rules has no such column yet!

    RESOLUTION: instead of a separate column, derive it analytically:
    initial_confidence_at_creation = the `confidence` value at applications_count=0,
    which Day 16 already wrote. Since `confidence` is mutable but
    `applications_count`/`success_count` are also tracked, we can recover the
    implied prior by recomputing from the FIRST row state — but simplest is to
    just ALSO add one more column in migration 009: `initial_confidence REAL NOT NULL`
    (immutable, set once at creation, never touched again). Go back and add this
    to Day 15's schema before Day 16 code ships. (Documented here because this
    is exactly the kind of cross-day dependency worth catching before, not after,
    you've written extraction code.)
    """
```

> **Action item folded back into Day 15:** add `initial_confidence REAL NOT NULL` to `engineering_rules` (immutable). Day 16's `INSERT` sets both `confidence` and `initial_confidence` to the same value; Day 18 onward only updates `confidence`.

### Promotion / demotion logic

```python
def update_rule_status(rule_id: int, db_path: str) -> str:
    """
    After recompute_confidence:
      - status='candidate' and applications_count >= MIN_APPLICATIONS_FOR_PROMOTION
        and confidence >= RULE_PROMOTION_THRESHOLD -> status='active'
      - status='active' and applications_count >= MIN_APPLICATIONS_FOR_DEPRECATION
        and confidence <= RULE_DEPRECATION_THRESHOLD -> status='deprecated',
        deprecation_reason='confidence_below_threshold'
    Returns the resulting status (unchanged if no transition).
    """
```

### Testing checklist
- [ ] `initial_confidence` is deterministic for fixed inputs (no randomness leaking from the LLM call itself — `llm_confidence` is a parsed number, the formula around it is pure).
- [ ] `recompute_confidence` with `applications_count=0` returns exactly `initial_confidence` (sanity check on the shrinkage formula's boundary).
- [ ] A rule with 3 applications, all successes, and `initial_confidence=0.6` → confidence rises above 0.65 → promoted to `active` (hand-verify the arithmetic in a unit test, don't just assert "it's higher").
- [ ] A rule with 5 applications, 1 success, `initial_confidence=0.6` → confidence falls below 0.35 → deprecated with the correct `deprecation_reason`.

### Definition of Done
Unit tests for `initial_confidence`, `recompute_confidence`, and `update_rule_status` pass with hand-computed expected values for at least 3 scenarios each (promotion, deprecation, no-change).

---
