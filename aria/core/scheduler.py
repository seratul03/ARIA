"""
aria/core/scheduler.py
───────────────────────
Background scheduler that periodically triggers improvement cycles.

Runs as a daemon thread. Every SCHEDULER_INTERVAL_MINUTES, it checks
if any tools are weak and, if so, triggers one improvement cycle.

The scheduler respects the per-hour cycle limit enforced by the Agent Core.
It can be stopped cleanly via the stop() method.
"""

from __future__ import annotations

import logging
import threading
import time

from aria.config import settings

logger = logging.getLogger(__name__)


class ImprovementScheduler:
    """
    Daemon thread that auto-triggers improvement cycles on a schedule.
    """

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._last_run: float = 0.0

    def start(self) -> None:
        """Start the background scheduler thread."""
        if self._thread and self._thread.is_alive():
            logger.warning("[Scheduler] Already running.")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="aria-scheduler",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            f"[Scheduler] Started. Interval: {settings.scheduler_interval_minutes} min."
        )

    def stop(self) -> None:
        """Signal the scheduler to stop and wait for it to finish."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5.0)
        logger.info("[Scheduler] Stopped.")

    def _loop(self) -> None:
        """Main scheduler loop."""
        interval_seconds = settings.scheduler_interval_minutes * 60

        while not self._stop_event.is_set():
            # Wait for the configured interval (interruptible every second)
            elapsed = time.monotonic() - self._last_run
            if elapsed < interval_seconds:
                remaining = interval_seconds - elapsed
                # Sleep in 1-second chunks so we can respond to stop signal
                for _ in range(int(remaining)):
                    if self._stop_event.is_set():
                        return
                    time.sleep(1)

            if self._stop_event.is_set():
                return

            self._last_run = time.monotonic()
            
            try:
                from aria.core.tracer import emit_trace
                emit_trace("scheduler", "wakeup", {"interval_seconds": interval_seconds})
            except ImportError:
                pass

            self._run_cycle()

    def _run_cycle(self) -> None:
        """Trigger one improvement cycle via the Agent Core."""
        try:
            from aria.core.agent import agent

            logger.info("[Scheduler] Triggering scheduled improvement cycle...")
            try:
                from aria.core.tracer import emit_trace
                emit_trace("scheduler", "component_invocation", {"target": "agent.run_improvement_cycle"})
            except ImportError:
                pass
            agent.run_improvement_cycle()
        except Exception as exc:
            logger.error(f"[Scheduler] Cycle error: {exc}")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def next_run_in_seconds(self) -> float:
        interval_seconds = settings.scheduler_interval_minutes * 60
        elapsed = time.monotonic() - self._last_run
        return max(0.0, interval_seconds - elapsed)


# ── Shared singleton ──────────────────────────────────────────────────────────

scheduler = ImprovementScheduler()
