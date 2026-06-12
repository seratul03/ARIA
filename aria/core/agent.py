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
        import time

        now = time.time()
        cutoff = now - settings.monitoring_window_seconds

        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, tool_name, old_success_rate 
                FROM improvement_history 
                WHERE status = 'deployed' AND timestamp >= ?
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
                self._emit(
                    EventType.ERROR,
                    f"Regression detected in '{tool_name}'! (Success: {stats.success_rate:.0%} < {old_rate:.0%}). Auto-rolling back."
                )
                self._rollback(tool_name)
                with get_connection() as conn:
                    conn.execute("UPDATE improvement_history SET status = 'rolled_back' WHERE id = ?", (row["id"],))

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
            else:
                reports = engine.analyze_all()
                if not reports:
                    self._emit(
                        EventType.CYCLE_SKIPPED,
                        "All tools are healthy. No improvement needed.",
                    )
                    trace.record_trigger("auto_trigger")
                    trace.finalize("NO_WEAKNESS_FOUND")
                    trace.save()
                    return False
                report = reports[0]  # Worst-performing tool
                trace.record_trigger(f"low_success_rate on {report.tool_name}")
    
            self._emit(
                EventType.WEAKNESS_FOUND,
                report.summary(),
                {
                    "tool": report.tool_name,
                    "success_rate": report.success_rate,
                    "p90_latency": report.p90_latency,
                },
            )
    
            # ── Step 2: Generate improvement ───────────────────────────────────────
            self._emit(EventType.GENERATING, f"Calling Groq LLM to improve '{report.tool_name}'...")
    
            from aria.improvement.engine import ImprovementEngine
    
            imp_engine = ImprovementEngine()
            improvement = imp_engine.generate_improvement(report)
    
            trace.record_llm_usage(prompt_tokens=0, response_tokens=improvement.tokens_used)
    
            if not improvement.success:
                self._emit(
                    EventType.ERROR,
                    f"LLM generation failed: {improvement.error}",
                    {"tool": report.tool_name},
                )
                log_audit_event("IMPROVEMENT_ATTEMPT", {"tool": report.tool_name, "success": False, "error": improvement.error})
                trace.finalize("GENERATION_FAILED")
                trace.save()
                
                self_model.record_cycle("improvement_engine", success=False)
                self_model.add_failure_pattern("improvement_engine", "LLM generation failed")
                return False
                
            trace.record_candidate_generated()
            log_audit_event("IMPROVEMENT_ATTEMPT", {"tool": report.tool_name, "success": True, "tokens_used": improvement.tokens_used})
    
            # ── Step 3: Arena Combat Protocol ──────────────────────────────────────
            import subprocess
            import tempfile
            import json
            import sys
            from aria.improvement.adversarial import AdversarialGenerator
            
            self._emit(EventType.STATIC_VALIDATION, f"Generating Tier 3 adversarial tests...")
            adv_gen = AdversarialGenerator()
            session_tests, session_token = adv_gen.generate_session_tests(report.tool_name)
    
            self._emit(EventType.STATIC_VALIDATION, f"Running isolated Gatekeeper on baseline...")
    
            # We need to run baseline with the current source
            from aria.tools.registry import registry
            tool_path = Path(__file__).parent.parent / "tools" / f"{report.tool_name}.py"
            try:
                current_source = tool_path.read_text(encoding="utf-8")
            except Exception as e:
                self._emit(EventType.ERROR, f"Failed to read baseline source: {e}")
                trace.finalize("BASELINE_READ_ERROR")
                trace.save()
                return False

            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as temp_file_clone, \
                 tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as temp_file_base, \
                 tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as temp_session_tests, \
                 tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as temp_baseline_res:
                
                temp_file_clone.write(improvement.generated_code)
                clone_path = temp_file_clone.name
                
                temp_file_base.write(current_source)
                base_path = temp_file_base.name
                
                json.dump(session_tests, temp_session_tests)
                session_tests_path = temp_session_tests.name
                
                baseline_res_path = temp_baseline_res.name

            try:
                # Sandbox Run 1: Baseline
                res_base = subprocess.run(
                    [
                        sys.executable, "-m", "aria.gatekeeper.cli", 
                        "--tool", report.tool_name, 
                        "--source", base_path, 
                        "--raw-results-only",
                        "--session-tests-file", session_tests_path
                    ],
                    capture_output=True, text=True, check=False
                )
                
                baseline_output = None
                for line in reversed(res_base.stdout.strip().splitlines()):
                    if line.startswith("[") or line.startswith("{"):
                        baseline_output = line
                        break
                        
                if not baseline_output:
                    raise ValueError(f"Baseline run failed to return JSON: {res_base.stderr or res_base.stdout}")
                    
                baseline_results = json.loads(baseline_output)
                if isinstance(baseline_results, dict) and not baseline_results.get("approved", True):
                    # It failed static validation or something
                    raise ValueError(f"Baseline failed static validation: {baseline_results.get('rejection_reason')}")
                    
                # Save baseline results to file
                with open(baseline_res_path, "w", encoding="utf-8") as f:
                    json.dump(baseline_results, f)

                self._emit(EventType.SANDBOX_VALIDATION, f"Running isolated Gatekeeper on clone...")

                # Sandbox Run 2: Clone
                res_clone = subprocess.run(
                    [
                        sys.executable, "-m", "aria.gatekeeper.cli", 
                        "--tool", report.tool_name, 
                        "--source", clone_path,
                        "--session-tests-file", session_tests_path,
                        "--session-token", session_token,
                        "--baseline-results-file", baseline_res_path
                    ],
                    capture_output=True, text=True, check=False
                )
                
                gatekeeper_output = None
                for line in reversed(res_clone.stdout.strip().splitlines()):
                    if line.startswith("{"):
                        gatekeeper_output = line
                        break
                        
                if not gatekeeper_output:
                    err_msg = res_clone.stderr.strip() if res_clone.stderr else res_clone.stdout.strip()
                    if not err_msg:
                        err_msg = "Gatekeeper output was empty."
                    sandbox_result = {"approved": False, "rejection_reason": f"Gatekeeper failed to return JSON: {err_msg}"}
                else:
                    sandbox_result = json.loads(gatekeeper_output)
                    
            except Exception as exc:
                sandbox_result = {"approved": False, "rejection_reason": f"Gatekeeper subprocess crashed: {exc}"}
            finally:
                Path(clone_path).unlink(missing_ok=True)
                Path(base_path).unlink(missing_ok=True)
                Path(session_tests_path).unlink(missing_ok=True)
                Path(baseline_res_path).unlink(missing_ok=True)
    
            self._emit(
                EventType.SANDBOX_VALIDATION,
                f"Gatekeeper Result: Approved={sandbox_result.get('approved', False)}",
                sandbox_result
            )
            
            combat_report = sandbox_result.get("combat_report")
            if combat_report:
                import uuid
                from datetime import datetime
                from rich.console import Console
                from rich.table import Table
                
                console = Console()
                session_id = uuid.uuid4().hex
                date_iso = datetime.utcnow().isoformat() + "Z"
                
                console.print(f"\n[bold]Combat Session:[/bold] {session_id}")
                console.print(f"[bold]Date:[/bold] {date_iso}")
                console.print(f"[bold]Proposed Change:[/bold] \"Improved {report.tool_name}\"\n")
                
                table = Table(show_header=True, header_style="bold")
                table.add_column("")
                table.add_column("Current ARIA", justify="center")
                table.add_column("Clone", justify="center")
                table.add_column("Delta", justify="right")
                
                b = combat_report["baseline"]
                c = combat_report["clone"]
                
                def fmt_delta(delta: float, is_ms=False) -> str:
                    # Floating point comparisons can be tricky
                    if abs(delta) < 0.001:
                        prefix = ""
                        is_positive = False
                    else:
                        prefix = "+" if delta > 0 else ""
                        is_positive = delta > 0
                        
                    if is_ms:
                        val = f"{prefix}{delta:.0f}ms"
                    else:
                        val = f"{prefix}{delta:.2f}"
                        
                    if abs(delta) < 0.001:
                        return f"{val} ➖"
                    elif is_positive and not is_ms:
                        return f"[green]{val}[/green] ✅"
                    elif not is_positive and is_ms:
                        return f"[green]{val}[/green] ✅"
                    else:
                        return f"[red]{val}[/red] ❌"
                        
                c_delta = c["correctness"] - b["correctness"]
                r_delta = c["robustness"] - b["robustness"]
                l_delta = (c["latency_p90"] - b["latency_p90"]) * 1000
                s_delta = c["overall_score"] - b["overall_score"]
                
                table.add_row("Correctness:", f"{b['correctness']:.2f}", f"{c['correctness']:.2f}", fmt_delta(c_delta))
                table.add_row("Latency P90 (ms):", f"{b['latency_p90']*1000:.0f}", f"{c['latency_p90']*1000:.0f}", fmt_delta(l_delta, is_ms=True))
                table.add_row("Robustness:", f"{b['robustness']:.2f}", f"{c['robustness']:.2f}", fmt_delta(r_delta))
                
                sg = combat_report.get("safety_gate", "PASS")
                sg_fmt = "✅" if sg == "PASS" else "❌"
                table.add_row("Safety Gate:", sg, sg, sg_fmt)
                
                table.add_row("Overall Score:", f"{b['overall_score']:.2f}", f"{c['overall_score']:.2f}", fmt_delta(s_delta))
                
                console.print(table)
                
                verdict = combat_report.get("verdict", "ARIA_WINS")
                if verdict == "CLONE_WINS":
                    console.print("\n[bold green]Verdict: CLONE WINS[/bold green]\n")
                else:
                    console.print("\n[bold red]Verdict: ARIA WINS — discarding clone[/bold red]\n")
                    
            log_audit_event("VALIDATION_RESULT", {"tool": report.tool_name, "sandbox_result": sandbox_result})
    
            # Check Gatekeeper health
            rejection_reason = sandbox_result.get("rejection_reason") or ""
            if any(err in rejection_reason for err in [
                "Gatekeeper failed to return JSON", 
                "Gatekeeper subprocess crashed", 
                "Gatekeeper DB error", 
                "Failed to read source file"
            ]):
                self_model.record_cycle("gatekeeper", success=False)
            elif "Gatekeeper signature verification failed" in rejection_reason:
                self_model.record_cycle("gatekeeper", success=False, safety_violation=True)
            else:
                self_model.record_cycle("gatekeeper", success=True)
    
            if not sandbox_result.get("approved", False):
                reason = f"Gatekeeper failed: {rejection_reason or 'Unknown'}"
                
                is_safety_violation = "Static validation failed" in reason
                self_model.record_cycle("improvement_engine", success=False, safety_violation=is_safety_violation)
                if is_safety_violation:
                    self_model.add_failure_pattern("improvement_engine", "AST Static Validation Failure")
                    
                self._record_rejection(
                    tool_name=report.tool_name,
                    reason=reason,
                    old_success_rate=report.success_rate,
                )
                trace.record_candidate_rejected("candidate_1", reason)
                trace.finalize("NO_IMPROVEMENT")
                trace.save()
                return False

            # ── Step 5: Human Review Gate ──────────────────────────────────────────
            if combat_report and combat_report.get("verdict") == "CLONE_WINS":
                from aria.metrics.db import insert_review_queue
                
                # Insert the queue item
                queue_id = insert_review_queue(
                    session_id=session_id if 'session_id' in locals() else 'unknown',
                    tool_name=report.tool_name,
                    timestamp=time.time(),
                    combat_report=json.dumps(combat_report),
                    generated_code=improvement.generated_code,
                    status="pending"
                )
                
                if settings.require_human_review:
                    # TUI is running, so we shouldn't prompt interactively here.
                    # Exit gracefully and let user review it later via the CLI.
                    self._emit(
                        EventType.CYCLE_COMPLETE,
                        f"Cycle #{cycle_num} complete. [bold yellow]Pending human review.[/bold yellow] Run `aria review` to deploy.",
                        {"cycle": cycle_num, "status": "pending_review"}
                    )
                    trace.finalize("PENDING_REVIEW")
                    trace.save()
                    return False
                else:
                    from aria.metrics.db import update_review_status
                    update_review_status(queue_id, "auto_approved")
    
            # ── Step 6: Deploy ─────────────────────────────────────────────────────
            deployed = self._deploy(
                tool_name=report.tool_name,
                new_source=improvement.generated_code,
                report=report,
                sandbox_result=sandbox_result,
            )
    
            if deployed:
                self_model.record_cycle("improvement_engine", success=True)
                trace.record_candidate_deployed()
                trace.finalize("DEPLOYED")
            else:
                self_model.record_cycle("improvement_engine", success=False)
                trace.record_candidate_rejected("candidate_1", "DEPLOY_FAILED")
                trace.finalize("DEPLOY_FAILED")
                
            trace.save()
    
            self._emit(
                EventType.CYCLE_COMPLETE,
                f"Cycle #{cycle_num} complete. Deployed: {deployed}.",
                {"cycle": cycle_num, "deployed": deployed},
            )
            return deployed
        finally:
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

    def _deploy(self, tool_name: str, new_source: str, report, sandbox_result) -> bool:
        """Write new source to the tool file and commit to Git."""
        from aria.metrics.db import insert_improvement
        from aria.versioning.git_manager import git_manager
        import time

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
                return False

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
            insert_improvement(
                tool_name=tool_name,
                timestamp=time.time(),
                status="deployed",
                reason=commit_msg,
                git_commit_hash=commit_hash,
                old_success_rate=report.success_rate,
                old_latency_p90=report.p90_latency,
            )

            self._emit(
                EventType.DEPLOYED,
                f"✓ Deployed improved '{tool_name}' (commit: {commit_hash or 'N/A'})",
                {"tool": tool_name, "commit": commit_hash},
            )
            log_audit_event("DEPLOYMENT", {"tool": tool_name, "commit": commit_hash})
            return True

        except Exception as exc:
            logger.error(f"[Agent] Deploy failed: {exc}")
            git_manager.rollback_to_tag(pre_tag)
            try:
                registry.reload_tool(tool_name)
            except Exception:
                pass
            return False

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
    ) -> None:
        """Record a rejection in the improvement history."""
        from aria.metrics.db import insert_improvement

        insert_improvement(
            tool_name=tool_name,
            timestamp=time.time(),
            status="rejected",
            reason=reason,
            old_success_rate=old_success_rate,
        )
        self._emit(
            EventType.REJECTED,
            f"✗ Improvement rejected for '{tool_name}': {reason}",
            {"tool": tool_name, "reason": reason},
        )
        log_audit_event("REJECTION", {"tool": tool_name, "reason": reason})

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
