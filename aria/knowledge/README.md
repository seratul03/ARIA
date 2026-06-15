# ARIA Knowledge Subsystem

The Knowledge Subsystem is responsible for extracting, generalizing, and managing engineering principles learned by ARIA over its lifecycle. It transitions specific bug fixes into broad rules, evaluates them against real-world applications, and refines them recursively.

## Core Modules (Phase 3)

1. **Extraction (`extraction.py`)**: Mines durable fixes from `improvement_history` and uses the LLM to generate candidate `engineering_rules`.
2. **Confidence (`confidence.py`)**: Computes initial confidence bounds and updates confidence dynamically based on real-world outcomes via `update_rule_status()`.
3. **Export (`export.py`)**: Generates deterministic JSON snapshots of the rules (`engineering_rules.json`) and commits them to Git.
4. **Applications (`applications.py`)**: Selects `active` and `candidate` rules to inject into LLM prompts. Also handles outcome resolution at the end of cycles.
5. **Pruning (`pruning.py`)**: Deprecates stagnant candidate rules or rules with decaying confidence below the threshold.
6. **Merging (`merging.py`)**: Detects semantic duplicates using TF-IDF / fuzzy matching and union-find disjoint sets to consolidate duplicate rules into a single winner.
7. **Generation (`generation.py`)**: Proactively generates candidate rules from recurring `architectural_patterns` that have not yet been resolved.
8. **Refinement (`refinement.py`)**: Detects active rules with mixed outcomes, gathers success/failure contexts, and narrows the rule's scope to improve effectiveness.

## Schema: `engineering_rules`

The `engineering_rules` table defines the core structure of a principle.

### Mutable vs Immutable Columns

| Column | Type | Mutable | Description |
|--------|------|---------|-------------|
| `id` | INTEGER | NO | Primary Key |
| `rule_text` | TEXT | NO | The core principle or rule |
| `category` | TEXT | NO | High-level grouping (e.g. 'Validation', 'State Management') |
| `scope` | TEXT | NO | Specific boundary condition where this applies |
| `source_type` | TEXT | NO | Origin of the rule (hypothesis, refinement, proactive) |
| `source_id` | INTEGER | NO | FK to origin record |
| `initial_confidence`| REAL | NO | Starting confidence score |
| `confidence` | REAL | YES | Current dynamic confidence score |
| `success_count` | INTEGER | YES | Number of successful applications |
| `applications_count`| INTEGER | YES | Total number of applications |
| `status` | TEXT | YES | candidate, active, deprecated, superseded, merged |
| `deprecation_reason`| TEXT | YES | Reason for deprecation |
| `superseded_by` | INTEGER | YES | If rule was replaced by refinement/merge |

*Note: Migrations and schema definitions are protected under the ARIA Constitution.*
