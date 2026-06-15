# Root Cause Analysis Module (Phase 2)

This module adds a high-level heuristic and LLM-based root cause analysis layer over ARIA's raw traces and memory.

## Taxonomy
The failure taxonomy is strictly frozen into the following high-level categories (defined in `aria.rootcause.categories.CATEGORY_DESCRIPTIONS`):
- `Network`
- `Authentication`
- `Resource Limit`
- `Syntax/Type Error`
- `Logic Error`
- `Dependency Error`
- `Timeout`
- `Data Format`

This taxonomy allows the system to bucket highly divergent stack traces into meaningful clusters.

## Gatekeeper Rules
To maintain the integrity of the taxonomy and historical data, the Gatekeeper enforces the following rules:
- `aria/rootcause/categories.py` is protected from agentic modification.
- All schema migrations (e.g. `aria/memory/migrations/005_root_cause_schema.sql` to `008_hypotheses.sql`) are protected.
- The `improvement_history` table allows `UPDATE` statements exclusively for appending `weakness_category`, but prevents modifications to the core historical result metrics.

## Environment Variables
The module introduces new control surfaces via `aria/config.py`:
- `MAX_ROOTCAUSE_LLM_CALLS_PER_CYCLE` (default: 5): Binds the maximum number of Groq API calls the classifier can make per meta-introspection cycle to prevent runaway costs on unclassified traces.
- `HYPOTHESIS_CONFIDENCE_THRESHOLD` (default: 0.60): The minimum confidence score required for an LLM-generated hypothesis to hijack the Introspection Engine. If the highest confidence hypothesis is below this, the engine falls back to standard "worst-performing tool" selection.

## Hypothesis Lifecycle
Hypotheses are actionable directives generated from systemic `architectural_patterns`. 
1. **Creation**: Generated via `aria.rootcause.hypotheses.generate_hypotheses()` with `status='proposed'`, `attempt_count=0`.
2. **Execution**: Picked up by the Introspection Engine. The LLM receives an explicit `## DIRECTIVE` prompting it to implement the proposed fix.
3. **Success**: If the generated code passes Gatekeeper and Sandbox tests, it is deployed. The hypothesis is marked `status='implemented'` and linked to the `improvement_history` row.
4. **Failure**: If generation, Gatekeeper, or Sandbox fails, `attempt_count` increments. 
5. **Rejection**: If a hypothesis reaches 3 consecutive failures without deployment, it is marked `status='rejected'` and permanently ignored by the Introspection Engine. This prevents ARIA from endlessly looping on an impossible architectural fix.
