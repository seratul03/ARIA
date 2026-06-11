"""
aria/introspection/engine.py
─────────────────────────────
The Introspection Engine analyzes collected metrics to detect underperforming
tools and produce a structured WeaknessReport used by the Improvement Engine.

A tool is flagged as "weak" if ANY of the following are true:
  - Rolling success rate (last 100 executions) < SUCCESS_RATE_THRESHOLD
  - p90 latency (last 100 executions) > LATENCY_THRESHOLD_SECONDS

Tools with fewer than MIN_EXECUTIONS_FOR_ANALYSIS are skipped.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from aria.config import settings
from aria.metrics.db import (
    ToolStats,
    get_all_tool_stats,
    get_recent_failures,
    get_tool_stats,
    get_improvement_history,
)
from aria.introspection.self_model import self_model


@dataclass
class WeaknessReport:
    """
    Structured analysis of a weak tool, passed to the Improvement Engine.
    """
    tool_name: str
    success_rate: float
    p90_latency: float
    total_executions: int
    failure_count: int
    fitness_score: float                        # Overall fitness score
    reasons: list[str]                          # Human-readable reasons for flagging
    recent_failures: list[dict]                 # Last N failure records for LLM context
    source_code: str                            # Current tool source code
    recent_improvement_failures: list[dict] = field(default_factory=list) # Past rejection reasons
    timestamp: float = field(default_factory=time.time)

    @property
    def severity(self) -> str:
        """Rough severity based on how bad the stats are."""
        if self.success_rate < 0.5:
            return "critical"
        if self.success_rate < 0.70:
            return "high"
        if self.p90_latency > self.success_rate * 10:  # unusually slow
            return "medium"
        return "low"

    def summary(self) -> str:
        return (
            f"[{self.severity.upper()}] {self.tool_name}: "
            f"fitness={self.fitness_score:.2f}, "
            f"success={self.success_rate:.0%}, "
            f"p90_latency={self.p90_latency:.2f}s, "
            f"failures={self.failure_count}/{self.total_executions}"
        )


def _load_source(tool_name: str) -> str:
    """Load the current source code of a tool from the tools directory."""
    tools_dir = Path(__file__).parent.parent / "tools"
    tool_file = tools_dir / f"{tool_name}.py"
    if tool_file.exists():
        return tool_file.read_text(encoding="utf-8")
    return "# Source code not available."


def _is_weak(stats: ToolStats) -> tuple[bool, list[str], float]:
    """
    Determine if a tool's stats cross the weakness thresholds using a multi-objective fitness score.
    Returns (is_weak, list_of_reasons, fitness_score).
    """
    reasons = []

    if stats.total_executions < settings.min_executions_for_analysis:
        return False, [], 0.0

    fitness = (
        settings.weight_pass_rate * stats.success_rate
        - settings.weight_latency * stats.avg_latency
        - settings.weight_memory * stats.avg_memory_mb
        - settings.weight_tokens * stats.avg_tokens_used
    )

    if fitness < settings.fitness_threshold:
        reasons.append(f"Fitness score {fitness:.2f} is below threshold {settings.fitness_threshold:.2f}")
        
        if stats.success_rate < settings.success_rate_threshold:
            reasons.append(
                f"Success rate {stats.success_rate:.0%} is below threshold "
                f"{settings.success_rate_threshold:.0%}"
            )
        if stats.p90_latency > settings.latency_threshold_seconds:
            reasons.append(
                f"p90 latency {stats.p90_latency:.2f}s exceeds threshold "
                f"{settings.latency_threshold_seconds:.1f}s"
            )
        if stats.avg_memory_mb > 50.0:
            reasons.append(f"Memory allocation ({stats.avg_memory_mb:.2f}MB) is excessive.")
        if stats.avg_tokens_used > 1000:
            reasons.append(f"LLM token usage ({stats.avg_tokens_used:.0f}) is too expensive.")

    return len(reasons) > 0, reasons, fitness


class IntrospectionEngine:
    """
    Analyzes SQLite metrics to identify weak tools.
    """

    def analyze_all(self) -> list[WeaknessReport]:
        """
        Analyze all known tools and return a list of WeaknessReports
        for any that are flagged as weak, sorted by severity (worst first).
        """
        all_stats = get_all_tool_stats(window=100)
        reports: list[WeaknessReport] = []

        for stats in all_stats:
            weak, reasons, fitness = _is_weak(stats)
            if not weak:
                continue

            failures = get_recent_failures(stats.tool_name, limit=5)
            source = _load_source(stats.tool_name)
            
            history = get_improvement_history(stats.tool_name, limit=5)
            rejected_history = [h for h in history if h["status"] == "rejected"]

            report = WeaknessReport(
                tool_name=stats.tool_name,
                success_rate=stats.success_rate,
                p90_latency=stats.p90_latency,
                total_executions=stats.total_executions,
                failure_count=stats.failure_count,
                fitness_score=fitness,
                reasons=reasons,
                recent_failures=failures,
                recent_improvement_failures=rejected_history,
                source_code=source,
            )
            
            try:
                from aria.core.tracer import emit_trace
                emit_trace("introspection", "weakness_detected", {"tool": stats.tool_name, "fitness": fitness, "reasons": reasons})
            except ImportError:
                pass
                
            reports.append(report)

        # Sort: critical first, then by success rate ascending (worst first)
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        reports.sort(key=lambda r: (severity_order[r.severity], r.success_rate))
        self_model.record_cycle("introspection_engine", success=True)
        return reports

    def analyze_tool(self, tool_name: str) -> WeaknessReport | None:
        """
        Analyze a specific tool by name.
        Returns a WeaknessReport even if it's not technically weak
        (used for manual improvement triggers).
        """
        stats = get_tool_stats(tool_name, window=100)
        if stats is None:
            return None

        _, reasons, fitness = _is_weak(stats)
        if not reasons:
            reasons = ["Manual improvement requested by user."]

        failures = get_recent_failures(tool_name, limit=5)
        source = _load_source(tool_name)
        
        history = get_improvement_history(tool_name, limit=5)
        rejected_history = [h for h in history if h["status"] == "rejected"]

        try:
            from aria.core.tracer import emit_trace
            emit_trace("introspection", "weakness_detected", {"tool": tool_name, "fitness": fitness, "reasons": reasons})
        except ImportError:
            pass
            
        self_model.record_cycle("introspection_engine", success=True)

        return WeaknessReport(
            tool_name=tool_name,
            success_rate=stats.success_rate,
            p90_latency=stats.p90_latency,
            total_executions=stats.total_executions,
            failure_count=stats.failure_count,
            fitness_score=fitness,
            reasons=reasons,
            recent_failures=failures,
            recent_improvement_failures=rejected_history,
            source_code=source,
        )

    def get_health_summary(self) -> dict[str, dict]:
        """
        Return a health summary for all tools — used by the TUI dashboard.
        Keys: tool_name → dict with stats and health status.
        """
        all_stats = get_all_tool_stats(window=100)
        summary = {}

        for stats in all_stats:
            weak, reasons, fitness = _is_weak(stats)
            summary[stats.tool_name] = {
                "success_rate": stats.success_rate,
                "p90_latency": stats.p90_latency,
                "avg_memory_mb": stats.avg_memory_mb,
                "avg_tokens_used": stats.avg_tokens_used,
                "fitness_score": fitness,
                "total_executions": stats.total_executions,
                "failure_count": stats.failure_count,
                "is_weak": weak,
                "reasons": reasons,
                "last_seen": stats.last_seen,
            }

        return summary
