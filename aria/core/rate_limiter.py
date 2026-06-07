"""
aria/core/rate_limiter.py
─────────────────────────
Groq API rate limiter using a sliding-window algorithm.

Prevents ARIA from sending too many requests to Groq, which would cause
HTTP 429 (Too Many Requests) errors. This is especially important because
the Improvement Engine and Summarizer tool may both call Groq independently.

All Groq callers must acquire a permit from the shared `groq_limiter` singleton
before making any API request.
"""

from __future__ import annotations

import threading
import time
from collections import deque

from aria.config import settings


class SlidingWindowRateLimiter:
    """
    Thread-safe sliding-window rate limiter.

    Tracks the timestamps of recent API calls in a deque.
    Before each call, checks:
      1. That the minimum interval since the last call has elapsed.
      2. That the call count in the last 60 seconds is under the max.

    If either condition fails, blocks (sleeps) until it can proceed.
    """

    def __init__(
        self,
        min_interval_seconds: float,
        max_calls_per_minute: int,
    ) -> None:
        self._min_interval = min_interval_seconds
        self._max_per_minute = max_calls_per_minute
        self._window: deque[float] = deque()
        self._lock = threading.Lock()
        self._last_call_time: float = 0.0

    def acquire(self) -> None:
        """
        Block until a Groq API call is permitted.
        Call this before every Groq API request.
        """
        with self._lock:
            now = time.monotonic()

            # 1. Enforce minimum interval between calls
            since_last = now - self._last_call_time
            if since_last < self._min_interval:
                wait = self._min_interval - since_last
                time.sleep(wait)
                now = time.monotonic()

            # 2. Enforce per-minute limit using sliding window
            cutoff = now - 60.0
            while self._window and self._window[0] < cutoff:
                self._window.popleft()

            if len(self._window) >= self._max_per_minute:
                # Wait until the oldest call falls out of the 60s window
                oldest = self._window[0]
                wait = (oldest + 60.0) - now + 0.1
                time.sleep(max(wait, 0))
                now = time.monotonic()

            # Record this call
            self._window.append(now)
            self._last_call_time = now

    @property
    def calls_in_last_minute(self) -> int:
        """How many Groq calls have been made in the last 60 seconds."""
        with self._lock:
            cutoff = time.monotonic() - 60.0
            return sum(1 for t in self._window if t >= cutoff)


# ── Shared singleton ──────────────────────────────────────────────────────────

groq_limiter = SlidingWindowRateLimiter(
    min_interval_seconds=settings.groq_min_request_interval_seconds,
    max_calls_per_minute=settings.groq_max_calls_per_minute,
)
