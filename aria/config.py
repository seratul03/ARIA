"""
aria/config.py
──────────────
Centralized configuration loader for ARIA.
Reads from .env file using python-dotenv.
Exposes a singleton `settings` object used throughout the application.

SECURITY: API keys are loaded once here and never logged, printed,
or passed to any external service other than the intended API client.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root (two levels up from this file)
_ENV_PATH = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH, override=False)


def _require(key: str) -> str:
    """Raise a clear error if a required env variable is missing."""
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(
            f"[ARIA Config] Missing required environment variable: {key}\n"
            f"  → Copy .env.example to .env and fill in your values."
        )
    return value


def _get(key: str, default: str) -> str:
    return os.getenv(key, default)


def _get_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default


def _get_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # ── Groq LLM ──────────────────────────────────────────────
    groq_api_key: str
    groq_model: str

    # ── Security ──────────────────────────────────────────────
    test_signing_key: str
    require_human_review: bool

    # ── Introspection thresholds ───────────────────────────────
    success_rate_threshold: float       # e.g. 0.70
    latency_threshold_seconds: float    # e.g. 5.0
    min_executions_for_analysis: int    # e.g. 10
    confidence_score_threshold: float   # e.g. 9.0
    
    # ── Fitness Score Weights ──────────────────────────────────
    fitness_threshold: float            # e.g. 0.5
    weight_pass_rate: float             # e.g. 1.0
    weight_latency: float               # e.g. 0.1
    weight_memory: float                # e.g. 0.01
    weight_tokens: float                # e.g. 0.001

    # ── Safety limits ──────────────────────────────────────────
    max_improvement_cycles_per_hour: int  # e.g. 5

    # ── Scheduler ──────────────────────────────────────────────
    scheduler_interval_minutes: int     # e.g. 30

    # ── Meta-Introspection ─────────────────────────────────────
    meta_introspection_interval: int    # e.g. 10
    meta_introspection_max_hours: float # e.g. 4.0
    clone_base_dir: Path
    clone_keep_on_failure: bool

    # ── Docker sandbox ─────────────────────────────────────────
    sandbox_memory_limit: str           # e.g. "256m"
    sandbox_cpu_limit: float            # e.g. 0.5
    sandbox_timeout_seconds: int        # e.g. 30

    # ── Groq rate limiting ─────────────────────────────────────
    groq_min_request_interval_seconds: float  # e.g. 3.0
    groq_max_calls_per_minute: int            # e.g. 15

    # ── Fields with defaults MUST come last ───────────────────
    # ── File reader tool ───────────────────────────────────────
    file_reader_allowed_dirs: list[str] = field(default_factory=list)

    # ── Database ───────────────────────────────────────────────
    db_path: Path = field(default_factory=lambda: Path("aria.db"))
    
    # ── Self-Model ─────────────────────────────────────────────
    self_model_path: Path = field(default_factory=lambda: Path("self_model.json"))


def _load_settings() -> Settings:
    allowed_dirs_raw = _get("FILE_READER_ALLOWED_DIRS", "./workspace,./data")
    allowed_dirs = [d.strip() for d in allowed_dirs_raw.split(",") if d.strip()]

    return Settings(
        groq_api_key=_require("GROQ_API_KEY"),
        groq_model=_get("GROQ_MODEL", "llama3-8b-8192"),
        test_signing_key=_get("TEST_SIGNING_KEY", "YOUR_16_CHAR_KEY"),
        require_human_review=_get("REQUIRE_HUMAN_REVIEW", "true").lower() == "true",

        success_rate_threshold=_get_float("SUCCESS_RATE_THRESHOLD", 0.70),
        latency_threshold_seconds=_get_float("LATENCY_THRESHOLD_SECONDS", 5.0),
        min_executions_for_analysis=_get_int("MIN_EXECUTIONS_FOR_ANALYSIS", 10),
        confidence_score_threshold=_get_float("CONFIDENCE_SCORE_THRESHOLD", 9.0),

        fitness_threshold=_get_float("FITNESS_THRESHOLD", 0.5),
        weight_pass_rate=_get_float("WEIGHT_PASS_RATE", 1.0),
        weight_latency=_get_float("WEIGHT_LATENCY", 0.1),
        weight_memory=_get_float("WEIGHT_MEMORY", 0.01),
        weight_tokens=_get_float("WEIGHT_TOKENS", 0.001),

        max_improvement_cycles_per_hour=_get_int("MAX_IMPROVEMENT_CYCLES_PER_HOUR", 5),

        scheduler_interval_minutes=_get_int("SCHEDULER_INTERVAL_MINUTES", 30),

        meta_introspection_interval=_get_int("META_INTROSPECTION_INTERVAL", 10),
        meta_introspection_max_hours=_get_float("META_INTROSPECTION_MAX_HOURS", 4.0),
        clone_base_dir=Path(_get("CLONE_BASE_DIR", "/tmp/aria_clones")),
        clone_keep_on_failure=_get("CLONE_KEEP_ON_FAILURE", "true").lower() == "true",

        sandbox_memory_limit=_get("SANDBOX_MEMORY_LIMIT", "256m"),
        sandbox_cpu_limit=_get_float("SANDBOX_CPU_LIMIT", 0.5),
        sandbox_timeout_seconds=_get_int("SANDBOX_TIMEOUT_SECONDS", 30),

        groq_min_request_interval_seconds=_get_float(
            "GROQ_MIN_REQUEST_INTERVAL_SECONDS", 3.0
        ),
        groq_max_calls_per_minute=_get_int("GROQ_MAX_CALLS_PER_MINUTE", 15),

        # Fields with defaults
        file_reader_allowed_dirs=allowed_dirs,
        db_path=Path(_get("DB_PATH", "aria.db")),
        self_model_path=Path(_get("SELF_MODEL_PATH", "self_model.json")),
    )


# Singleton — imported by all modules
settings: Settings = _load_settings()
