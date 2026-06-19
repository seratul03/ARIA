## DAYS 57-60 — Self-Improvement Proposal Generation

### Objective
Synthesize all findings from Days 52-56 (weaknesses, mistakes, ineffective improvements, token waste, bad prompts) into concrete, actionable, falsifiable proposals for how ARIA's improvement process itself should change.

### Schema (migration 022)

```sql
CREATE TABLE IF NOT EXISTS self_improvement_proposals (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    title               TEXT NOT NULL,
    source_finding_type TEXT NOT NULL,  -- 'weakness'|'mistake'|'ineffective'|'token_waste'|'bad_prompt'
    source_finding_id   INTEGER NOT NULL,  -- FK into the relevant finding table
    proposal_text       TEXT NOT NULL,     -- what to change, how, and why
    target_module       TEXT,              -- 'aria/improvement/prompt_builder.py' etc.
    change_type         TEXT NOT NULL,     -- 'prompt_change'|'parameter_change'|'pipeline_change'|'knowledge_change'|'schedule_change'
    success_metric      TEXT NOT NULL,     -- MUST be specific and measurable
    measurement_window_cycles INTEGER NOT NULL DEFAULT 20,  -- how many cycles to evaluate
    priority            TEXT NOT NULL DEFAULT 'medium',  -- 'low'|'medium'|'high'|'critical'
    status              TEXT NOT NULL DEFAULT 'proposed',  -- proposed|accepted|in_progress|implemented|failed|deferred
    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    accepted_at         DATETIME,
    implemented_at      DATETIME,
    implementation_notes TEXT,
    evaluation_at       DATETIME,    -- when to check success_metric
    outcome             TEXT,        -- 'success'|'failure'|'inconclusive' (set at evaluation_at)
    outcome_notes       TEXT,
    snapshot_id         INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_proposal_status ON self_improvement_proposals(status);
CREATE INDEX IF NOT EXISTS idx_proposal_priority ON self_improvement_proposals(priority);
```

### `aria/reflection/proposals.py`

```python
# Day 57: Proposal generation from weaknesses (Days 52)
def generate_proposals_from_weaknesses(db_path: str, snapshot_id: int) -> int:
    """
    For each 'active' architectural_weakness with no existing 'proposed' or
    'accepted' or 'in_progress' proposal referencing it:

    Build a proposal using a lookup table of pre-authored templates (NOT an
    LLM call — proposals for known weakness types have known fix directions):

    PROPOSAL_TEMPLATES = {
        "category_blind_spot": {
            "title": "Generate hypothesis for {category} blind spot",
            "proposal_text": "Manually trigger Phase 2 hypothesis generation targeting {category} failures specifically. The current scheduling has not prioritized this category. Call generate_hypotheses(db_path, force_category='{category}') outside the normal meta-cycle.",
            "target_module": "aria/rootcause/hypotheses.py",
            "change_type": "pipeline_change",
            "success_metric": "At least one 'implemented' hypothesis for {category} within {measurement_window_cycles} cycles.",
            "priority": severity_to_priority(weakness.severity)
        },
        "predictor_drift": {
            "title": "Retrain drifted {predictor_type} predictor",
            "proposal_text": "Force-retrain the {predictor_type} predictor immediately via python -m aria predictors --retrain {predictor_type}, then review and promote if metrics clear the threshold.",
            "change_type": "schedule_change",
            "success_metric": "actual_accuracy within 0.08 of test_accuracy over next {measurement_window_cycles} resolved predictions.",
        },
        "population_collapse": {...},
        "rule_coverage_gap": {...},
        # ... one template per weakness_type
    }
    Returns count of proposals inserted.
    """


# Day 58: Proposals from mistakes (Day 53)
def generate_proposals_from_mistakes(db_path: str, snapshot_id: int) -> int:
    """
    For each 'active' recurring_mistake with no existing proposal:

    MISTAKE_PROPOSAL_TEMPLATES = {
        "rule_violation_pattern": {
            "title": "Enforce rule {rule_id} in prompt structure",
            "proposal_text": "Move the GENERATION DIRECTIVE section containing rule {rule_id} to the first position in the improvement prompt, before memory retrieval context. Also add an explicit 'CONSTRAINT: your fix MUST implement X' line, not just a guideline.",
            "target_module": "aria/improvement/prompt_builder.py",
            "change_type": "prompt_change",
            "success_metric": "rule_compliance_score for rule {rule_id} averages > 0.60 across the next {measurement_window_cycles} applicable cycles.",
        },
        "target_selection_oscillation": {
            "title": "Add oscillation break for {tool_name}",
            "proposal_text": "Modify aria/introspection/introspection.py's select_next_target() to apply a COOLDOWN_CYCLES penalty to tools that have been selected more than OSCILLATION_FRACTION of recent cycles without improvement. Skip to the second-worst tool for COOLDOWN_CYCLES cycles.",
            "target_module": "aria/introspection/introspection.py",
            "change_type": "pipeline_change",
            "success_metric": "{tool_name} is NOT the selected target in > 3 of the next 5 cycles, OR its deploy_rate improves by > 0.15 within {measurement_window_cycles} cycles.",
        },
        "malformed_code_recurrence": {
            "title": "Add pre-generation syntax check prompt",
            "proposal_text": "Add an instruction to all generation prompts: 'Before outputting code, verify it is syntactically valid Python. Do not output code with syntax errors.' Simple but often effective at reducing LLM syntactic slippage.",
            "target_module": "aria/improvement/prompt_builder.py",
            "change_type": "prompt_change",
            "success_metric": "disqualification_rate due to static_analysis drops below {MALFORM_RATE/2} over next {measurement_window_cycles} runs.",
        },
        # ...
    }
    """


# Day 59: LLM-assisted proposals for complex or novel findings (Days 54-56)
def generate_proposals_from_complex_findings(db_path: str, snapshot_id: int,
                                              max_llm_calls: int = 3) -> int:
    """
    For ineffective_improvement findings, token_waste findings, and bad_prompt
    findings that don't have a pre-authored template (because they're more
    context-specific):

    Build a prompt containing:
      - The finding's description, evidence_json, and severity
      - ARIA's current relevant configuration (the affected module's key parameters)
      - The success_metric requirements: "Must name a specific measurable outcome.
        Must reference a specific column/query that can be checked automatically.
        Must specify a measurement window in cycles."

    Ask for STRICT JSON:
    {
        "title": "...",
        "proposal_text": "...",
        "target_module": "...",
        "change_type": "prompt_change|parameter_change|pipeline_change|knowledge_change|schedule_change",
        "success_metric": "...",
        "measurement_window_cycles": int,
        "priority": "low|medium|high|critical"
    }

    Validate:
      - success_metric contains at least one numeric threshold (regex: r'\d+')
      - change_type is one of the 5 valid values
      - target_module is an actual file path (os.path.exists check — rejects proposals
        targeting nonexistent files or Constitution-protected files)
    """


# Day 60: Proposal lifecycle management
def evaluate_implemented_proposals(db_path: str) -> dict:
    """
    For each proposal with status='implemented' AND evaluation_at <= NOW():
      - Parse success_metric to determine what to query.
        (success_metric is free text — for automated evaluation, the proposal
         must have been templated (Days 57-58) where the metric is also stored
         in a structured form. LLM-generated proposals (Day 59) get manual
         evaluation only, flagged in outcome_notes='manual_review_required'.)
      - For templated proposals, run the structured metric query.
      - Set outcome='success'|'failure'|'inconclusive', outcome_notes.
      - If 'failure': create a new Phase 6 analysis finding of type 'proposal_failure'
        (add to relevant finding table) so the failure itself is tracked as an input
        to future proposals — closing the meta-learning loop.
    Returns {"evaluated": N, "success": M, "failure": K, "inconclusive": P}
    """

def get_priority_proposals(db_path: str, limit: int = 5) -> list[dict]:
    """
    Returns proposals ordered by:
      1. priority ('critical' > 'high' > 'medium' > 'low')
      2. created_at ASC (oldest first within same priority)
    WHERE status='proposed'.
    This is what you look at each morning: the to-do list for ARIA's self-improvement.
    """
```

### The proposal → implementation flow

Self-improvement proposals are NOT auto-implemented. This is a deliberate hard line. ARIA generates the proposal; you review it; you implement it. The workflow:

```
1. python -m aria reflect --proposals
   (lists open proposals by priority)

2. python -m aria reflect --proposal <id>
   (detailed view: finding source, proposed change, target_module, success_metric)

3. [You implement the change manually in the target_module]

4. python -m aria reflect --accept <id>
   (set status='accepted', accepted_at=now)
   [You implement the change]

5. python -m aria reflect --implemented <id> --notes "Changed line 47 of prompt_builder.py: moved DIRECTIVE to top"
   (set status='implemented', implemented_at=now, implementation_notes, evaluation_at=now + measurement_window_cycles * avg_cycle_duration)

6. [measurement_window_cycles later, evaluate_implemented_proposals() runs automatically
    during meta-introspection and sets outcome='success'|'failure'|'inconclusive']

7. python -m aria reflect --outcomes
   (shows proposal outcomes — what worked, what didn't)
```

This workflow is the highest-leverage part of Phase 6. It gives you a structured, evidence-backed to-do list for improving ARIA, with automatic outcome measurement. Over time, the outcomes feed back into Phase 5's models (a proposal that succeeded is evidence for the predictor; one that failed is negative evidence) and into Phase 6's own proposal-generation quality (if 70% of `prompt_change` proposals succeed but 30% of `pipeline_change` proposals fail, that's a signal about which types of changes are more predictable).

### The self-reflection synthesis report (Day 60)

```python
# aria/reflection/report.py

def generate_reflection_report(db_path: str, llm_narrative: bool = True) -> dict:
    """
    Synthesizes all Phase 6 findings into one structured report + optional narrative.

    Structure:
    {
        "self_model_trend": {
            "deploy_rate_10_cycle_trend": "improving"|"stable"|"declining",
            "active_failure_patterns_trend": ...,
            "active_weaknesses_trend": ...,
        },
        "active_weaknesses": [...top 5 by severity...],
        "recurring_mistakes": [...active...],
        "ineffective_improvements": [...active...],
        "token_waste": {...estimated_total_per_cycle, top_findings...},
        "bad_prompts": [...active...],
        "priority_proposals": [...top 5 by priority...],
        "proposal_outcomes": {"success": N, "failure": M, "pending": K},
        "narrative": "..." | None
    }

    If llm_narrative=True, ONE LLM call: given all the above structured data,
    write a 2-3 paragraph plain-English answer to: "What are ARIA's most important
    self-improvement opportunities right now, and what should be done first?"

    This is the only free-form LLM output in Phase 6.
    """
```

### CLI (Day 60)

```bash
python -m aria reflect                          # summary: active findings + priority proposals
python -m aria reflect --weaknesses             # architectural weaknesses
python -m aria reflect --mistakes               # recurring mistakes
python -m aria reflect --ineffective            # ineffective improvements
python -m aria reflect --waste                  # token waste findings
python -m aria reflect --prompts                # bad prompt findings
python -m aria reflect --proposals              # priority proposal queue
python -m aria reflect --proposal <id>          # detailed proposal view
python -m aria reflect --accept <id>            # accept proposal
python -m aria reflect --implemented <id>       # mark as implemented
python -m aria reflect --outcomes               # view proposal outcomes
python -m aria reflect --report                 # full synthesis report
python -m aria reflect --report --no-narrative  # structured only, no LLM
python -m aria reflect --trend <metric> <n>     # self-model trend for a metric over last N cycles
```

### Testing checklist (Days 57-60 combined)
- [ ] `generate_proposals_from_weaknesses` with a `category_blind_spot` weakness produces exactly one proposal with correct template fields, non-empty `success_metric` containing a numeric threshold, and `target_module` pointing to an existing file.
- [ ] A second call in the same meta-cycle does NOT produce a duplicate proposal (deduplication check: no existing `proposed`/`accepted`/`in_progress` proposal for the same `source_finding_type`/`source_finding_id`).
- [ ] `generate_proposals_from_complex_findings` rejects LLM-generated proposals where `target_module` points to a Constitution-protected path (e.g., `aria/reflection/proposals.py` itself) — validate this with a mocked LLM that returns such a path.
- [ ] `evaluate_implemented_proposals` correctly sets `outcome='success'` for a templated proposal whose structured metric query passes, and `outcome='failure'` for one that doesn't — test with synthetic pre/post cycle data.
- [ ] `python -m aria reflect` on a DB with no Phase 6 findings prints a clear "no findings yet" summary without error.
- [ ] The proposal lifecycle flows end-to-end: `proposed → accepted → implemented → evaluated` (success or failure) with timestamps set correctly at each transition.

### Definition of Done (Phase 6 complete)
By the end of Day 60:
- At least one `architectural_weakness` and one `recurring_mistake` from real data, both with non-null `evidence_json`.
- At least one `self_improvement_proposal` in `proposed` status, generated from one of the above findings, with a concrete and falsifiable `success_metric`.
- `python -m aria reflect --report` produces a synthesis report that you find genuinely useful — it should feel like a retrospective on ARIA's performance written by someone who has read all the data, not a generic status dump.
- `self_model_snapshots` has a time series of at least 3 entries showing trends.
- `self_model.json` exports correctly from the structured tables, is git-committed, and contains all Phase 1-6 data.

---
