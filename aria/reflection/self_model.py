import json
import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

def build_self_model_snapshot(db_path: str, cycle_number: int) -> int:
    """
    Queries all Phase 1-5 tables, computes aggregates, and INSERTs one row
    into self_model_snapshots. Returns the new row id.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Phase 1: Improvement History & Failure Patterns
        total_imp = conn.execute("SELECT COUNT(*) as c FROM improvement_history").fetchone()["c"]
        dep_imp = conn.execute("SELECT COUNT(*) as c FROM improvement_history WHERE result = 'deployed'").fetchone()["c"]
        rb_imp = conn.execute("SELECT COUNT(*) as c FROM improvement_history WHERE result = 'rolled_back'").fetchone()["c"]
        overall_deploy_rate = (dep_imp / total_imp) if total_imp > 0 else 0.0

        try:
            active_fail = conn.execute("SELECT COUNT(*) as c FROM failure_patterns WHERE status = 'active'").fetchone()["c"]
            resolved_fail = conn.execute("SELECT COUNT(*) as c FROM failure_patterns WHERE status = 'resolved'").fetchone()["c"]
        except sqlite3.OperationalError:
            active_fail = resolved_fail = 0

        # Phase 2: Root Cause & Hypotheses
        try:
            from aria.rootcause.statistics import root_cause_breakdown
            rc_breakdown = root_cause_breakdown(weight_by="occurrence_count")
            rc_breakdown_json = json.dumps(rc_breakdown)
        except Exception:
            rc_breakdown_json = "{}"

        try:
            active_arch = conn.execute("SELECT COUNT(*) as c FROM architectural_patterns WHERE status = 'active'").fetchone()["c"]
            resolved_arch = conn.execute("SELECT COUNT(*) as c FROM architectural_patterns WHERE status = 'resolved'").fetchone()["c"]
            open_hypo = conn.execute("SELECT COUNT(*) as c FROM hypotheses WHERE status = 'open'").fetchone()["c"]
            impl_hypo = conn.execute("SELECT COUNT(*) as c FROM hypotheses WHERE status = 'implemented'").fetchone()["c"]
        except sqlite3.OperationalError:
            active_arch = resolved_arch = open_hypo = impl_hypo = 0

        # Phase 3: Knowledge (Engineering Rules)
        try:
            active_rules = conn.execute("SELECT COUNT(*) as c FROM engineering_rules WHERE status = 'active'").fetchone()["c"]
            cand_rules = conn.execute("SELECT COUNT(*) as c FROM engineering_rules WHERE status = 'candidate'").fetchone()["c"]
            dep_rules = conn.execute("SELECT COUNT(*) as c FROM engineering_rules WHERE status = 'deprecated'").fetchone()["c"]
            top_rule_conf = conn.execute("SELECT MAX(confidence) as m FROM engineering_rules WHERE status = 'active'").fetchone()["m"] or 0.0
            refine_chains = conn.execute("SELECT COUNT(*) as c FROM engineering_rules WHERE source_type = 'refinement'").fetchone()["c"]
        except sqlite3.OperationalError:
            active_rules = cand_rules = dep_rules = top_rule_conf = refine_chains = 0

        # Phase 4: Evolution
        try:
            total_evo = conn.execute("SELECT COUNT(*) as c FROM evolution_runs").fetchone()["c"]
            avg_cand = conn.execute("SELECT AVG(candidates_generated) as a FROM cycle_traces").fetchone()["a"] or 0.0
            
            # Simple aggregations for json cols
            strat_json = "{}"
            mut_json = "{}"
            pop_json = "{}"
        except sqlite3.OperationalError:
            total_evo = 0
            avg_cand = 0.0
            strat_json = mut_json = pop_json = "{}"

        # Phase 5: Predictors
        try:
            from aria.predictors.inference import predictor_health_report
            health = predictor_health_report(db_path)
            pred_summary_json = json.dumps(health)
            active_pred = conn.execute("SELECT COUNT(*) as c FROM predictors WHERE status = 'active'").fetchone()["c"]
        except Exception:
            pred_summary_json = "{}"
            active_pred = 0

        # Insert Snapshot
        cursor = conn.execute('''
            INSERT INTO self_model_snapshots (
                cycle_number, schema_version,
                total_improvement_cycles, deployed_improvements, rolled_back_improvements, overall_deploy_rate,
                active_failure_patterns, resolved_failure_patterns,
                root_cause_breakdown_json, active_architectural_patterns, resolved_architectural_patterns,
                open_hypotheses, implemented_hypotheses,
                active_rules, candidate_rules, deprecated_rules, top_rule_confidence, refinement_chains_active,
                total_evolution_runs, strategy_win_rates_json, mutation_win_rates_json, avg_candidates_per_run, population_sizes_json,
                predictor_summary_json, active_predictor_count
            ) VALUES (
                ?, ?,
                ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?
            )
        ''', (
            cycle_number, 1,
            total_imp, dep_imp, rb_imp, overall_deploy_rate,
            active_fail, resolved_fail,
            rc_breakdown_json, active_arch, resolved_arch,
            open_hypo, impl_hypo,
            active_rules, cand_rules, dep_rules, top_rule_conf, refine_chains,
            total_evo, strat_json, mut_json, avg_cand, pop_json,
            pred_summary_json, active_pred
        ))
        
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()

def get_latest_snapshot(db_path: str) -> dict:
    """SELECT * FROM self_model_snapshots ORDER BY cycle_number DESC LIMIT 1."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM self_model_snapshots ORDER BY cycle_number DESC LIMIT 1").fetchone()
        if row:
            return dict(row)
        return {}
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()

def get_snapshot_trend(db_path: str, metric: str, last_n: int = 10) -> list[tuple[int, float]]:
    """
    Returns [(cycle_number, value), ...] for a named metric column over the
    last N snapshots.
    """
    conn = sqlite3.connect(db_path)
    try:
        # Prevent basic SQL injection by enforcing metric name matches \w+
        import re
        if not re.match(r"^[a-zA-Z0-9_]+$", metric):
            return []
            
        rows = conn.execute(f"SELECT cycle_number, {metric} FROM self_model_snapshots ORDER BY cycle_number DESC LIMIT ?", (last_n,)).fetchall()
        # The rows are in descending order, we want ascending for a trend line
        return [(r[0], r[1] if r[1] is not None else 0.0) for r in reversed(rows)]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()

def export_self_model_json(db_path: str, output_path: str = "self_model.json") -> dict:
    """
    Deterministic export of the latest snapshot + Phase 6 findings
    into the existing self_model.json format.
    Commits to git via aria/versioning if content differs.
    """
    snapshot = get_latest_snapshot(db_path)
    
    # Read Phase 6 data from the legacy self_model object if needed, 
    # or just export what we have.
    # To maintain backward compatibility, we will combine the new snapshot
    # with the components dict.
    from aria.introspection.self_model import self_model
    
    export_data = {
        "snapshot": snapshot,
        "components": self_model.components,
        "system_wide_patterns": self_model.introspection_data.get("system_wide_patterns", [])
    }
    
    new_json_str = json.dumps(export_data, indent=2, sort_keys=True)
    
    output_file = Path(output_path)
    content_changed = True
    if output_file.exists():
        existing_content = output_file.read_text(encoding="utf-8")
        if existing_content == new_json_str:
            content_changed = False
            
    if content_changed:
        output_file.write_text(new_json_str, encoding="utf-8")
        try:
            from aria.versioning.git_manager import git_manager
            git_manager.commit_file(output_file, "Auto-update self_model.json from DB snapshot")
        except Exception as e:
            logger.warning(f"Failed to commit self_model.json to git: {e}")
            
    return export_data
