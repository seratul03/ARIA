"""
aria/core/agent.py
───────────────────
The Agent Core — ARIA's central controller.

Responsibilities:
  1. Receive and dispatch tool calls (via the metrics collector)
  2. Run improvement cycles end-to-end:
       Introspect → Generate → Validate → Deploy/Reject
  3. Enforce the per-hour improvement cycle limit
  4. Publish status events to the TUI via a thread-safe event queue

The Agent Core does NOT:
  - Modify its own source code
  - Modify the Gatekeeper
  - Modify the database schema
  - Access the filesystem beyond aria/tools/
"""

from __future__ import annotations

import logging
import queue
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from threading import Lock
from typing import Any

from aria.config import settings
from aria.core.audit import log_audit_event
from aria.metrics.collector import execute as metrics_execute
from aria.tools.registry import registry
from aria.introspection.self_model import self_model

logger = logging.getLogger(__name__)


# ── Event system (for TUI) ────────────────────────────────────────────────────

class EventType(Enum):
    CYCLE_STARTED = auto()
    CYCLE_SKIPPED = auto()
    WEAKNESS_FOUND = auto()
    GENERATING = auto()
    STATIC_VALIDATION = auto()
    SANDBOX_VALIDATION = auto()
    DEPLOYED = auto()
    REJECTED = auto()
    ROLLED_BACK = auto()
    CYCLE_COMPLETE = auto()
    ERROR = auto()
    TOOL_EXECUTED = auto()
    META_INTROSPECTION_STARTED = auto()
    META_INTROSPECTION_COMPLETE = auto()
    SYNTHESIS_STARTED = auto()
    SYNTHESIS_SUCCESS = auto()
    SYNTHESIS_FAILED = auto()


@dataclass
class AgentEvent:
    type: EventType
    message: str
    data: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


# ── Cycle rate limiter ────────────────────────────────────────────────────────

class _CycleRateLimiter:
    """
    Tracks improvement cycle timestamps and enforces the per-hour cap.
    Separate from the Groq API rate limiter.
    """

    def __init__(self, max_per_hour: int) -> None:
        self._max = max_per_hour
        self._timestamps: deque[float] = deque()
        self._lock = Lock()

    def can_run(self) -> bool:
        with self._lock:
            now = time.time()
            cutoff = now - 3600.0
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()
            return len(self._timestamps) < self._max

    def record(self) -> None:
        with self._lock:
            self._timestamps.append(time.time())

    @property
    def cycles_this_hour(self) -> int:
        with self._lock:
            cutoff = time.time() - 3600.0
            return sum(1 for t in self._timestamps if t >= cutoff)

    @property
    def max_per_hour(self) -> int:
        return self._max


# ── Agent Core ────────────────────────────────────────────────────────────────

class AgentCore:
    """
    The central controller for ARIA. Orchestrates all subsystems.
    """

    def __init__(self) -> None:
        self._event_queue: queue.Queue[AgentEvent] = queue.Queue(maxsize=500)
        self._cycle_limiter = _CycleRateLimiter(
            settings.max_improvement_cycles_per_hour
        )
        self._total_cycles = 0
        self._cycles_since_recompute = 0
        self._running = False
        self._last_meta_introspection_time = time.time()

    # ── Event publishing ───────────────────────────────────────────────────────

    def _emit(self, event_type: EventType, message: str, data: dict | None = None) -> None:
        event = AgentEvent(type=event_type, message=message, data=data or {})
        try:
            self._event_queue.put_nowait(event)
        except queue.Full:
            pass  # TUI is not consuming fast enough — drop old events
            
        # Print internal steps to terminal for the interactive menu
        if event_type != EventType.TOOL_EXECUTED:
            try:
                print(f"   > [{event_type.name}] {message}")
            except UnicodeEncodeError:
                print(f"   > [{event_type.name}] {message.encode('ascii', 'replace').decode('ascii')}")
            
        logger.info(f"[Agent] {event_type.name}: {message}")

    def get_events(self, max_items: int = 20) -> list[AgentEvent]:
        """Drain up to `max_items` events from the queue (called by TUI)."""
        events = []
        for _ in range(max_items):
            try:
                events.append(self._event_queue.get_nowait())
            except queue.Empty:
                break
        return events

    def _check_monitoring_windows(self) -> None:
        """
        Phase 6.3: Post-Deployment Monitoring.
        Checks all deployments within the monitoring window.
        If a tool's performance drops below baseline, rollback.
        """
        from aria.metrics.db import get_connection, get_tool_stats


        now = time.time()
        cutoff = now - settings.monitoring_window_seconds

        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, tool_name, baseline_fitness as old_success_rate 
                FROM improvement_history 
                WHERE result = 'deployed' AND timestamp >= ?
                ORDER BY timestamp ASC
                """,
                (cutoff,)
            ).fetchall()

        for row in rows:
            tool_name = row["tool_name"]
            old_rate = row["old_success_rate"]
            if old_rate is None:
                continue
                
            stats = get_tool_stats(tool_name)
            if not stats:
                continue
                
            # Allow a tiny bit of float leniency, but rollback if clearly degraded
            if stats.success_rate < (old_rate - 0.01):
                err_msg = f"Regression detected in '{tool_name}'! (Success: {stats.success_rate:.0%} < {old_rate:.0%}). Auto-rolling back."
                self._emit(EventType.ERROR, err_msg)
                
                from aria.memory.store import record_improvement, record_failure
                
                failure_id = record_failure(
                    tool_name=tool_name,
                    source="post_deploy_monitor",
                    error_type="PerformanceRegression",
                    error_message=err_msg,
                    stack_trace=""
                )
                
                record_improvement(
                    improvement_type='tool',
                    fix_summary=f"Rolled back {tool_name} due to performance regression.",
                    result='rolled_back',
                    tool_name=tool_name,
                    baseline_fitness=old_rate,
                    candidate_fitness=stats.success_rate,
                    triggering_failure_id=failure_id,
                )
                
                self._rollback(tool_name)

        # Check meta improvements health
        from aria.versioning.git_manager import git_manager
        if git_manager._repo:
            for tag in git_manager._repo.tags:
                if tag.name.startswith("post_meta_deployment_"):
                    try:
                        ts = int(tag.name.split("_")[-1])
                        if ts >= cutoff:
                            with get_connection() as conn:
                                recent_cycles = conn.execute(
                                    "SELECT cycle_outcome FROM cycle_traces WHERE timestamp >= ?",
                                    (ts,)
                                ).fetchall()
                            
                            if recent_cycles:
                                failures = sum(1 for c in recent_cycles if c["cycle_outcome"] not in ("IMPROVED", "DEPLOYED", "NO_IMPROVEMENT", "PENDING_REVIEW"))
                                failure_rate = failures / len(recent_cycles)
                                
                                if failure_rate >= 0.5 and len(recent_cycles) >= 3:
                                    self._emit(
                                        EventType.ERROR,
                                        f"Global degradation detected since meta-improvement (Failure Rate: {failure_rate:.0%}). Auto-rolling back."
                                    )
                                    git_manager.rollback_to_tag(f"pre_meta_deployment_{ts}")
                                    self._emit(
                                        EventType.ROLLED_BACK,
                                        f"↩ Meta-improvement rolled back to 'pre_meta_deployment_{ts}'.",
                                    )
                                    break
                    except Exception as e:
                        logger.error(f"[Agent] Error checking meta-improvement health: {e}")

    # ── Tool execution ─────────────────────────────────────────────────────────

    def run_tool(self, tool_name: str, input_data: dict) -> Any:
        """
        Execute a registered tool with metrics collection.

        Args:
            tool_name:  e.g. "search_tool"
            input_data: Input dict passed to tool.run()

        Returns:
            ToolResult from the tool, or None if tool not found.
        """
        tool = registry.get(tool_name)
        if tool is None:
            logger.warning(f"[Agent] Tool not found: {tool_name}")
            return None

        result = metrics_execute(
            tool_name=tool_name,
            run_fn=lambda: tool.run(input_data),
            input_data=input_data,
        )

        self._emit(
            EventType.TOOL_EXECUTED,
            f"Tool '{tool_name}' executed — "
            f"{'✓' if result.success else '✗'} "
            f"({result.latency_seconds:.2f}s)",
            {"tool": tool_name, "success": result.success},
        )
        return result

    # ── Tool Synthesis ─────────────────────────────────────────────────────────

    def synthesize_new_tool(self, tool_name: str, specification: str) -> bool:
        """
        End-to-end pipeline to generate a brand new tool, test it, and deploy it.
        """
        self._emit(
            EventType.SYNTHESIS_STARTED,
            f"Starting synthesis for new tool: '{tool_name}'",
            {"tool": tool_name}
        )
        
        from aria.improvement.synthesis import ToolSynthesisEngine
        engine = ToolSynthesisEngine()
        
        result = engine.synthesize(tool_name, specification)
        
        if not result.success or not result.generated_code:
            self._emit(
                EventType.SYNTHESIS_FAILED,
                f"Failed to synthesize '{tool_name}' after {result.attempts} attempts. Error: {result.error}",
                {"tool": tool_name}
            )
            return False
            
        # Sandbox passed! Deploy it to host
        try:
            from aria.versioning.git_manager import git_manager
            import time
            from pathlib import Path
            
            tool_path = Path(__file__).parent.parent / "tools" / f"{tool_name}.py"
            
            if tool_path.exists():
                self._emit(EventType.ERROR, f"Tool '{tool_name}' already exists at {tool_path}. Cannot synthesize over it.")
                return False
                
            tool_path.write_text(result.generated_code, encoding="utf-8")
            
            # Git commit
            commit_msg = f"Synthesize new tool: {tool_name}"
            commit_hash = git_manager.commit_file(tool_path, commit_msg)
            
            # Hot reload registry
            try:
                registry.reload_tool(tool_name)
            except Exception as e:
                self._emit(EventType.ERROR, f"Host smoke test failed for newly synthesized '{tool_name}': {e}. Rolling back.")
                git_manager.rollback_tool(tool_name)
                tool_path.unlink(missing_ok=True)
                return False
                
            from aria.memory.store import record_improvement
            record_improvement(
                improvement_type='synthesis',
                tool_name=tool_name,
                problem_description=specification,
                fix_summary=f"Synthesized new tool: {tool_name}",
                result='deployed',
                git_commit_hash=commit_hash,
            )
            
            self._emit(
                EventType.SYNTHESIS_SUCCESS,
                f"✓ Successfully synthesized and deployed '{tool_name}' (commit: {commit_hash})",
                {"tool": tool_name}
            )
            return True
            
        except Exception as e:
            self._emit(EventType.ERROR, f"Exception during synthesis deployment: {e}")
            return False

    # ── Improvement cycle ──────────────────────────────────────────────────────

    def run_improvement_cycle(self, target_tool: str | None = None) -> bool:
        """
        Run one full improvement cycle.

        If `target_tool` is given, improve that specific tool (manual trigger).
        Otherwise, let the Introspection Engine pick the worst-performing tool.

        Returns True if a tool was successfully deployed, False otherwise.
        """
        # ── Post-Deployment Monitoring ─────────────────────────────────────────
        self._check_monitoring_windows()

        # ── Rate limit check ───────────────────────────────────────────────────
        if not self._cycle_limiter.can_run():
            self._emit(
                EventType.CYCLE_SKIPPED,
                f"Cycle limit reached ({self._cycle_limiter.max_per_hour}/hour). "
                f"Cycles this hour: {self._cycle_limiter.cycles_this_hour}.",
            )
            return False

        self._cycle_limiter.record()
        self._total_cycles += 1
        cycle_num = self._total_cycles

        try:
            # Initialize cycle trace
            from aria.core.tracer import CycleTrace
            trace = CycleTrace(component="improvement_engine")
    
            self._emit(
                EventType.CYCLE_STARTED,
                f"Improvement cycle #{cycle_num} started.",
                {"cycle": cycle_num},
            )
    
            # ── Step 1: Introspect ─────────────────────────────────────────────────
            from aria.introspection.engine import IntrospectionEngine
    
            engine = IntrospectionEngine()
    
            if target_tool:
                report = engine.analyze_tool(target_tool)
                if report is None:
                    self._emit(
                        EventType.ERROR,
                        f"Tool '{target_tool}' has no execution history. Run it first.",
                    )
                    trace.record_trigger(f"manual_trigger on {target_tool}")
                    trace.finalize("NO_HISTORY")
                    trace.save()
                    return False
                trace.record_trigger(f"manual_trigger on {target_tool}")
                hypothesis_id = None
            else:
                target_data = engine.select_next_target()
                mode = target_data.get("mode")
                if mode == "none" or target_data.get("report") is None:
                    self._emit(
                        EventType.CYCLE_SKIPPED,
                        "All tools are healthy. No improvement needed.",
                    )
                    trace.record_trigger("auto_trigger")
                    trace.finalize("NO_WEAKNESS_FOUND")
                    trace.save()
                    return False
                report = target_data["report"]
                hypothesis_id = target_data.get("hypothesis_id")
                trace.record_trigger(f"auto_trigger on {report.tool_name} (mode={mode})")
    
            self._emit(
                EventType.WEAKNESS_FOUND,
                report.summary(),
                {
                    "tool": report.tool_name,
                    "success_rate": report.success_rate,
                    "p90_latency": report.p90_latency,
                },
            )
    
            # ── Step 2: Gate 1 - Cycle Viability ───────────────────────────────────
            self._emit(EventType.GENERATING, f"Checking cycle viability for '{report.tool_name}'...")
            
            from aria.predictors.inference import predict_cycle_viability, predict_candidate_success, predict_deployment_risk
            from aria.metrics.db import get_connection

            
            run_context = {
                "tool_name": report.tool_name,
                "hypothesis_id": hypothesis_id,
                "root_cause_category": report.root_cause_category if hasattr(report, 'root_cause_category') else None,
                "trigger_is_hypothesis": 1.0 if hypothesis_id else 0.0,
                "hypothesis_confidence": 0.5,
            }
            
            viability = predict_cycle_viability(report.tool_name, run_context, str(settings.db_path))
            
            with get_connection() as conn:
                cur = conn.execute(
                    "INSERT INTO evolution_runs (tool_name, run_status, started_at, trigger_type, hypothesis_id) VALUES (?, ?, ?, ?, ?)",
                    (report.tool_name, 'skipped_low_viability' if viability['skip'] else 'running', time.time(), 'auto' if hypothesis_id else 'manual', hypothesis_id)
                )
                evolution_run_id = cur.lastrowid
                
            if viability['skip']:
                self._emit(EventType.CYCLE_SKIPPED, f"Skipping cycle. Predicted success prob: {viability['predicted_success_prob']:.2f}")
                trace.finalize("SKIPPED_LOW_VIABILITY")
                trace.save()
                return False
                
            # ── Step 3: Generate improvement ───────────────────────────────────────
            self._emit(EventType.GENERATING, f"Generating candidates via Evolution Engine for '{report.tool_name}'...")
            from aria.evolution.generator import generate_candidates, STRATEGY_ORDER
            
            weakness_context = {"report": report}
            all_candidates = generate_candidates(evolution_run_id, report.tool_name, weakness_context, hypothesis_id, str(settings.db_path), STRATEGY_ORDER)
            
            if not all_candidates:
                self._emit(EventType.ERROR, "Failed to generate any candidates.")
                with get_connection() as conn:
                    conn.execute("UPDATE evolution_runs SET run_status='failed_generation' WHERE id=?", (evolution_run_id,))
                trace.finalize("GENERATION_FAILED")
                trace.save()
                return False
                
            for _ in all_candidates:
                trace.record_candidate_generated()
            
            # ── Step 4: Gate 2 - Candidate Filter ──────────────────────────────────
            self._emit(EventType.STATIC_VALIDATION, f"Filtering {len(all_candidates)} candidates based on predicted success...")
            filtered_candidates = predict_candidate_success(all_candidates, run_context, str(settings.db_path))
            
            # Record static rejections
            filtered_ids = {c.get("id") for c in filtered_candidates}
            for c in all_candidates:
                if c.get("id") not in filtered_ids:
                    trace.record_candidate_rejected(str(c.get("id", "unknown")), "STATIC_VALIDATION_FAILED")
            
            if not filtered_candidates:
                self._emit(EventType.ERROR, "All candidates filtered out.")
                with get_connection() as conn:
                    conn.execute("UPDATE evolution_runs SET run_status='failed_generation' WHERE id=?", (evolution_run_id,))
                trace.finalize("GENERATION_FAILED")
                trace.save()
                return False
                
            # ── Step 5: Arena Combat Protocol ──────────────────────────────────────
            self._emit(EventType.SANDBOX_VALIDATION, f"Running parallel sandbox for {len(filtered_candidates)} candidates...")
            from aria.evolution.arena import run_parallel_sandbox
            
            evaluated = run_parallel_sandbox(
                filtered_candidates, 
                evolution_run_id, 
                report.tool_name, 
                str(settings.db_path), 
                emit_func=lambda t, m: self._emit(EventType.SANDBOX_VALIDATION, m)
            )
            
            from aria.evolution.ranking import rank_candidates
            
            # Record sandbox failures
            for c in evaluated:
                if c.get("sandbox_passed", 0) == 0:
                    trace.record_candidate_rejected(
                        str(c.get("id", "unknown")), 
                        str(c.get("disqualification_reason", "SANDBOX_FAILED"))
                    )
                    
            ranked = rank_candidates(evaluated, str(settings.db_path), evolution_run_id, [], {})
            
            if not ranked:
                self._emit(EventType.ERROR, "No candidates survived ranking.")
                with get_connection() as conn:
                    conn.execute("UPDATE evolution_runs SET run_status='failed_sandbox' WHERE id=?", (evolution_run_id,))
                trace.finalize("SANDBOX_FAILED")
                trace.save()
                return False
                
            winner = ranked[0]
            
            # Record candidates that passed sandbox but lost the ranking battle
            for c in ranked[1:]:
                if c.get("sandbox_passed", 0) == 1:
                    trace.record_candidate_rejected(str(c.get("id", "unknown")), "LOST_RANKING_BATTLE")
                    
            sandbox_result = winner.get("sandbox_result", {})
            combat_report = winner.get("combat_report", {})
            improvement_code = winner.get("source_code")
            pending_rules = winner.get("pending_rule_app_ids", [])
            
            if winner.get("sandbox_passed", 0) == 0:
                self._emit(EventType.ERROR, f"Winner failed sandbox validation: {winner.get('disqualification_reason')}")
                with get_connection() as conn:
                    conn.execute("UPDATE evolution_runs SET run_status='failed_sandbox' WHERE id=?", (evolution_run_id,))
                trace.finalize("SANDBOX_FAILED")
                trace.save()
                return False
                
            with get_connection() as conn:
                conn.execute("UPDATE evolution_runs SET winner_candidate_id=? WHERE id=?", (winner["id"], evolution_run_id))
                
            # ── Step 6: Gate 3 - Deployment Risk ───────────────────────────────────
            self._emit(EventType.STATIC_VALIDATION, f"Predicting deployment risk for winner (ID: {winner.get('id')})...")
            risk = predict_deployment_risk(winner, run_context, str(settings.db_path))
            
            import uuid
            import json
            session_id = uuid.uuid4().hex
            
            from aria.metrics.db import insert_review_queue
            
            if risk['high_risk']:
                self._emit(EventType.DEPLOYMENT, f"Winner flagged as high risk (Prob: {risk['rollback_prob']:.2f}). Redirecting to review queue.")
                
                queue_id = insert_review_queue(
                    session_id=session_id,
                    tool_name=report.tool_name,
                    timestamp=time.time(),
                    combat_report=json.dumps(combat_report),
                    generated_code=improvement_code,
                    status="pending",
                    cycle_id=trace.cycle_id
                )
                
                with get_connection() as conn:
                    conn.execute("UPDATE evolution_runs SET run_status='review_queued' WHERE id=?", (evolution_run_id,))
                trace.finalize("REVIEW_QUEUED")
                trace.save()
                
                if settings.require_human_review:
                    self._emit(EventType.CYCLE_COMPLETE, f"Cycle #{cycle_num} complete. [bold yellow]Pending human review.[/bold yellow]", {"cycle": cycle_num, "status": "pending_review"})
                    return False
                else:
                    from aria.metrics.db import update_review_status
                    update_review_status(queue_id, "auto_approved")
                    
            elif combat_report and combat_report.get("verdict") == "CLONE_WINS":
                queue_id = insert_review_queue(
                    session_id=session_id,
                    tool_name=report.tool_name,
                    timestamp=time.time(),
                    combat_report=json.dumps(combat_report),
                    generated_code=improvement_code,
                    status="pending",
                    cycle_id=trace.cycle_id,
                )
                
                if settings.require_human_review:
                    self._emit(EventType.CYCLE_COMPLETE, f"Cycle #{cycle_num} complete. [bold yellow]Pending human review.[/bold yellow]", {"cycle": cycle_num, "status": "pending_review"})
                    trace.finalize("PENDING_REVIEW")
                    trace.save()
                    return False
                else:
                    from aria.metrics.db import update_review_status
                    update_review_status(queue_id, "auto_approved")
                    
            # ── Step 7: Deploy ─────────────────────────────────────────────────────
            deployed_imp_id = self._deploy(
                tool_name=report.tool_name,
                new_source=improvement_code,
                report=report,
                sandbox_result=sandbox_result,
            )
            deployed = deployed_imp_id is not None
    
            if deployed:
                from aria.knowledge.applications import resolve_rule_applications
                resolve_rule_applications(pending_rules, deployed_imp_id, "success", str(settings.db_path))
                self_model.record_cycle("improvement_engine", success=True)
                trace.record_candidate_deployed()
                trace.finalize("DEPLOYED")
                with get_connection() as conn:
                    conn.execute("UPDATE evolution_runs SET run_status='completed' WHERE id=?", (evolution_run_id,))
            else:
                self_model.record_cycle("improvement_engine", success=False)
                trace.record_candidate_rejected("candidate_1", "DEPLOY_FAILED")
                trace.finalize("DEPLOY_FAILED")
                with get_connection() as conn:
                    conn.execute("UPDATE evolution_runs SET run_status='failed_deployment' WHERE id=?", (evolution_run_id,))
                
            trace.save()
    
            self._emit(
                EventType.CYCLE_COMPLETE,
                f"Cycle #{cycle_num} complete. Deployed: {deployed}.",
                {"cycle": cycle_num, "deployed": deployed},
            )
            return deployed
        finally:
            # ── Memory Ranking Recomputation ───────────────────────────────────────
            self._cycles_since_recompute += 1
            if self._cycles_since_recompute >= settings.memory_recompute_interval:
                try:
                    from aria.memory.ranking import recompute_all_scores
                    recompute_all_scores()
                except Exception as e:
                    logger.error(f"[Agent] Memory ranking recompute failed: {e}")
                self._cycles_since_recompute = 0

            # ── Predictor Retraining ───────────────────────────────────────────────
            if self._total_cycles % settings.predictor_retrain_interval_cycles == 0:
                self._emit(
                    EventType.SYSTEM,
                    "Triggering Predictor Retraining pass over Phase 4 data..."
                )
                try:
                    from aria.predictors.trainer import retrain_all
                    results = retrain_all(str(settings.db_path))
                    self._emit(
                        EventType.SYSTEM,
                        f"Predictor Retraining complete. Status: {results}"
                    )
                except Exception as e:
                    logger.error(f"[Agent] Predictor retraining failed: {e}")
                    
            # ── Meta-Introspection ─────────────────────────────────────────────────
            time_since_meta = (time.time() - self._last_meta_introspection_time) / 3600.0
            if (
                self._total_cycles % settings.meta_introspection_interval == 0 
                or time_since_meta >= settings.meta_introspection_max_hours
            ):
                self._emit(
                    EventType.META_INTROSPECTION_STARTED,
                    "Triggering Meta-Introspection pass over recent cycles..."
                )
                
                from aria.introspection.meta import run_meta_introspection
                run_meta_introspection(settings.meta_introspection_interval)
                
                self._last_meta_introspection_time = time.time()
                
                self._emit(
                    EventType.META_INTROSPECTION_COMPLETE,
                    "Meta-Introspection pass complete. self_model.json updated."
                )

    def _deploy(self, tool_name: str, new_source: str, report, sandbox_result) -> int | None:
        """Write new source to the tool file and commit to Git. Returns improvement_id or None."""
        from aria.versioning.git_manager import git_manager


        timestamp = int(time.time())
        pre_tag = f"pre_meta_improvement_{timestamp}"
        post_tag = f"post_meta_improvement_{timestamp}"

        # 1. Tag current state BEFORE deployment
        git_manager.tag_commit(pre_tag)

        tool_path = Path(__file__).parent.parent / "tools" / f"{tool_name}.py"

        try:
            # 2. Write improved code
            tool_path.write_text(new_source, encoding="utf-8")

            # 3. Host Smoke Test (import and initialization check)
            try:
                registry.reload_tool(tool_name)
            except Exception as e:
                self._emit(
                    EventType.ERROR,
                    f"Host smoke test failed for '{tool_name}': {e}. Rolling back."
                )
                git_manager.rollback_to_tag(pre_tag)
                try:
                    registry.reload_tool(tool_name)
                except Exception:
                    pass
                return None

            # 4. Git commit
            commit_msg = (
                f"Improve {tool_name} — "
                f"success_rate: {report.success_rate:.0%} → expected ↑, "
                f"sandbox: {sandbox_result.get('tests_passed', 0)}/{sandbox_result.get('tests_total', 0)} tests"
            )
            commit_hash = git_manager.commit_tool(tool_name, commit_msg)

            # 5. Tag post deployment
            if commit_hash:
                git_manager.tag_commit(post_tag, commit_hash)

            # 6. Record in DB
            from aria.memory.store import record_improvement
            c_fitness = sandbox_result.get("combat_report", {}).get("clone", {}).get("overall_score")
            from aria.core.tracer import get_active_cycle_id
            improvement_id = record_improvement(
                improvement_type='tool',
                tool_name=tool_name,
                fix_summary=commit_msg,
                result='deployed',
                git_commit_hash=commit_hash,
                baseline_fitness=report.success_rate,
                candidate_fitness=c_fitness,
                cycle_id=get_active_cycle_id()
            )
            
            hypothesis_id = getattr(report, "hypothesis", {}).get("id") if getattr(report, "hypothesis", None) else None
            if hypothesis_id is not None:
                from aria.rootcause.hypotheses import mark_hypothesis_outcome
                mark_hypothesis_outcome(hypothesis_id, improvement_id, True)

            self._emit(
                EventType.DEPLOYED,
                f"✓ Deployed improved '{tool_name}' (commit: {commit_hash or 'N/A'})",
                {"tool": tool_name, "commit": commit_hash},
            )
            log_audit_event("DEPLOYMENT", {"tool": tool_name, "commit": commit_hash})
            return improvement_id

        except Exception as exc:
            logger.exception(f"[Agent] Deploy failed: {exc}")
            git_manager.rollback_to_tag(pre_tag)
            try:
                registry.reload_tool(tool_name)
            except Exception:
                pass
            return None

    def _rollback(self, tool_name: str) -> None:
        """Roll back a tool to its last known good version."""
        from aria.versioning.git_manager import git_manager

        success = git_manager.rollback_tool(tool_name)
        if success:
            registry.reload_tool(tool_name)
            self._emit(
                EventType.ROLLED_BACK,
                f"↩ '{tool_name}' rolled back to previous version.",
                {"tool": tool_name},
            )
            log_audit_event("ROLLBACK", {"tool": tool_name, "status": "success"})
        else:
            self._emit(
                EventType.ERROR,
                f"Rollback failed for '{tool_name}'. Manual inspection required.",
                {"tool": tool_name},
            )
            log_audit_event("ROLLBACK", {"tool": tool_name, "status": "failed"})

    def _record_rejection(
        self,
        tool_name: str,
        reason: str,
        old_success_rate: float | None = None,
    ) -> int:
        """Record a rejection in the improvement history."""
        from aria.memory.store import record_improvement

        imp_id = record_improvement(
            improvement_type='tool',
            tool_name=tool_name,
            result="rejected",
            rejection_reason=reason,
            fix_summary=reason,
            baseline_fitness=old_success_rate,
        )
        self._emit(
            EventType.REJECTED,
            f"✗ Improvement rejected for '{tool_name}': {reason}",
            {"tool": tool_name, "reason": reason},
        )
        log_audit_event("REJECTION", {"tool": tool_name, "reason": reason})
        return imp_id

    # ── Status ─────────────────────────────────────────────────────────────────

    @property
    def cycles_this_hour(self) -> int:
        return self._cycle_limiter.cycles_this_hour

    @property
    def max_cycles_per_hour(self) -> int:
        return self._cycle_limiter.max_per_hour

    @property
    def total_cycles(self) -> int:
        return self._total_cycles


# ── Shared singleton ──────────────────────────────────────────────────────────

agent = AgentCore()
