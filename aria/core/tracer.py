"""
aria/core/tracer.py
───────────────────
Provides the tracing capabilities for ARIA components, allowing
the system to observe its own cycles and execution paths.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field

import threading
from aria.metrics.db import insert_cycle_trace

_tracer_local = threading.local()

def get_active_cycle_id() -> str | None:
    return getattr(_tracer_local, "cycle_id", None)

def set_active_cycle_id(cycle_id: str | None) -> None:
    _tracer_local.cycle_id = cycle_id

def emit_trace(component: str, event_type: str, details: dict) -> None:
    """Emit a granular trace event, automatically attaching the active cycle_id if any."""
    from aria.metrics.db import insert_detailed_trace
    insert_detailed_trace(
        cycle_id=get_active_cycle_id(),
        timestamp=time.time(),
        component=component,
        event_type=event_type,
        details=json.dumps(details)
    )

@dataclass
class CycleTrace:
    """
    A structured trace that captures the lifecycle of a single improvement cycle.
    """
    cycle_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)
    component: str = "improvement_engine"
    trigger: str | None = None
    llm_prompt_tokens: int = 0
    llm_response_tokens: int = 0
    candidates_generated: int = 0
    candidates_rejected: dict[str, str] = field(default_factory=dict)
    candidates_deployed: int = 0
    _start_time: float = field(default_factory=time.monotonic, repr=False)

    def __post_init__(self):
        set_active_cycle_id(self.cycle_id)

    def record_trigger(self, reason: str) -> None:
        self.trigger = reason

    def record_llm_usage(self, prompt_tokens: int, response_tokens: int) -> None:
        self.llm_prompt_tokens = prompt_tokens
        self.llm_response_tokens = response_tokens

    def record_candidate_generated(self) -> None:
        self.candidates_generated += 1

    def record_candidate_rejected(self, candidate_id: str, reason: str) -> None:
        self.candidates_rejected[candidate_id] = reason

    def record_candidate_deployed(self) -> None:
        self.candidates_deployed += 1

    def finalize(self, outcome: str) -> None:
        self.cycle_outcome = outcome
        self.duration_seconds = time.monotonic() - self._start_time
        set_active_cycle_id(None)

    def save(self) -> None:
        """Persist this trace to the SQLite database."""
        insert_cycle_trace(
            cycle_id=self.cycle_id,
            timestamp=self.timestamp,
            component=self.component,
            trigger=self.trigger,
            llm_prompt_tokens=self.llm_prompt_tokens,
            llm_response_tokens=self.llm_response_tokens,
            candidates_generated=self.candidates_generated,
            candidates_rejected=json.dumps(self.candidates_rejected),
            candidates_deployed=self.candidates_deployed,
            cycle_outcome=self.cycle_outcome,
            duration_seconds=self.duration_seconds,
        )
