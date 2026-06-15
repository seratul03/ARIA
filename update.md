## DAY 14 — Root Cause Report ("Why Am I Failing?")

### Objective
A single command that produces a human-readable (and `self_model.json`-readable) answer to: *"ARIA, why are you failing?"* — synthesizing Days 10-13 into one narrative.

### `aria/rootcause/report.py`

```python
def generate_root_cause_report(db_path: str, llm_narrative: bool = True) -> dict:
    """
    Assembles:
      - root_cause_breakdown() (Day 10) — both weightings
      - top 5 root_cause_clusters by total_occurrences (Day 11)
      - all 'active' architectural_patterns (Day 12)
      - all 'proposed' and 'implemented' (last 30 days) hypotheses (Day 13)
      - reliability check: for 'implemented' hypotheses, did the linked
        improvement_history.result stay 'deployed' (not later 'rolled_back')?
        -> "fixes that actually held up" vs "fixes we thought worked"

    If llm_narrative=True, makes ONE additional LLM call: given the structured
    summary above, write a 2-3 paragraph plain-English narrative answering
    "why am I failing and what am I doing about it". This is the only
    free-form LLM output in the report — everything else is structured data.

    Returns:
    {
      "root_cause_breakdown": {...},
      "top_clusters": [...],
      "architectural_patterns": [...],
      "hypotheses": {"proposed": [...], "implemented_recent": [...]},
      "fix_durability": {"held": N, "rolled_back": M},
      "narrative": "..." | None
    }
    """
```

### CLI

```bash
python -m aria why                  # full report with narrative
python -m aria why --no-narrative   # structured data only, no LLM call
python -m aria rootcause --report   # alias, for consistency with `aria rootcause --stats/--clusters`
```

Formatting should match the Phase 1 `aria memory` dashboard styling — this command is the natural "headline" view, with `aria memory` and `aria rootcause --stats/--clusters` as the drill-down views.

### `self_model.json` integration

Add a `root_cause_report` block containing everything from `generate_root_cause_report` **except** `narrative` (keep the self-model machine-readable and bounded in size; the narrative is for human consumption via the CLI and can be regenerated on demand). This block is what Phase 6 (Self-Reflection Layer) will read when building `self_model.json`'s own meta-level weaknesses — e.g., "the Security category has zero classified patterns — is that because ARIA has no security issues, or because the classifier never assigns Security?" is exactly the kind of question Phase 6's self-reflection should be able to ask using this data.

### Testing checklist
- [ ] `generate_root_cause_report(llm_narrative=False)` runs with **zero** LLM calls and produces complete structured output — verify with a mocked/blocked LLM client.
- [ ] `fix_durability` correctly counts an `implemented` hypothesis whose `resolved_improvement_id` later appears with `result='rolled_back'` in a *new* `improvement_history` row (Phase 1, Day 1's rollback-as-new-row pattern) as "rolled_back", not "held".
- [ ] Report generation is read-only — confirm with a test that asserts no `INSERT`/`UPDATE` statements run during `generate_root_cause_report` (it's purely a query/aggregation layer; all writes happened in Days 9-13).
- [ ] `self_model.json`'s `root_cause_report` block stays under a defined size budget (e.g., top 5 clusters/patterns/hypotheses only, not unbounded lists) — add an explicit truncation test.

### Definition of Done
`python -m aria why` on the fully-seeded DB from Days 8-13 produces a coherent report: breakdown percentages match Day 10's output, the Day 12 architectural pattern and Day 13 hypothesis both appear, and the narrative (if enabled) plausibly references the retry-logic example without hallucinating tools/numbers not present in the structured data (spot-check manually — this is the one place "vibes-based" review is appropriate, since it's a narrative summary).

---
