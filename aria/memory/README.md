# ARIA Memory Subsystem

This directory contains the core modules for ARIA's long-term memory, clustering, and ranking logic.

## 1. The Append-Only Guarantee
By design, all historical data in ARIA (especially `tool_executions`, `cycle_traces`, `failure_history`, and `improvement_history`) is append-only. We do not delete historical records. This ensures ARIA maintains an immutable ledger of all its past states, successes, and failures.

## 2. The UPDATE Allow-List
The **one strict exception** to the append-only rule is updating derived analytics counters. 
Because `failure_history` acts as both an immutable log and a unified cluster record (the "canonical" failure row), the `occurrence_count`, `last_seen`, and `memory_score` columns must be updated.

This is strictly enforced in `aria/memory/store.py` via Python call-stack inspection. Only the background compression engine (`compress_failure_history`) is allowed to execute `UPDATE` statements on these counters. Standard execution logs must use `INSERT`.

## 3. Phase 2 Hooks (Context Structuring)
As ARIA moves into Phase 2, we will expand memory from simple traceback clustering to structural categorization. The database schema has been pre-equipped with hooks for Phase 2:

- **`weakness_category`**: Currently populated as 'unknown'. Phase 2 will introduce an LLM-based classifier to tag failures (e.g., 'API_TIMEOUT', 'LOGIC_ERROR', 'HALLUCINATION') before insertion.
- **`triggering_failure_id`**: Located in `improvement_history`. This creates a hard foreign-key link between a fix and the exact failure cluster it resolved, allowing Phase 2 to map structural weaknesses directly to their highest-ROI fixes.
