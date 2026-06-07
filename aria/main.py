"""
aria/main.py
─────────────
Application bootstrap — called by all CLI commands.

Responsibilities:
  1. Initialize the SQLite database
  2. Register all tools in the registry
  3. Initialize the Git manager and create initial commit if needed
  4. Create the workspace directory if it doesn't exist

This module is imported by every CLI subcommand to ensure the system
is in a consistent state before any operations are performed.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Configure logging before any other imports
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler("aria.log", encoding="utf-8"),
    ],
)

logger = logging.getLogger(__name__)


def bootstrap() -> None:
    """
    Initialize all ARIA subsystems. Idempotent — safe to call multiple times.
    """
    _ensure_env()
    _init_database()
    _init_tools()
    _init_git()
    _ensure_workspace()
    logger.info("[Bootstrap] ARIA subsystems ready.")


def _ensure_env() -> None:
    """Validate that the .env file and required keys exist."""
    try:
        from aria.config import settings  # noqa — triggers validation
    except EnvironmentError as exc:
        print(f"\n[ARIA] Configuration Error:\n{exc}\n", file=sys.stderr)
        sys.exit(1)


def _init_database() -> None:
    """Initialize SQLite database schema."""
    from aria.config import settings
    from aria.metrics.db import init_db

    db_path = settings.db_path
    init_db(db_path)
    logger.info(f"[Bootstrap] Database ready: {db_path}")


def _init_tools() -> None:
    """Register all tools in the tool registry."""
    from aria.tools.registry import registry

    registry.load_all_from_directory()
    logger.info(f"[Bootstrap] Registered {len(registry.names())} tools: {registry.names()}")


def _init_git() -> None:
    """Initialize Git repo and create baseline commit if needed."""
    try:
        from aria.versioning.git_manager import git_manager
        git_manager.initial_commit_tools()
        logger.info("[Bootstrap] Git version control ready.")
    except Exception as exc:
        logger.warning(f"[Bootstrap] Git initialization skipped: {exc}")


def _ensure_workspace() -> None:
    """Create the workspace directory for file reader tool."""
    workspace = Path("workspace")
    workspace.mkdir(exist_ok=True)
    data = Path("data")
    data.mkdir(exist_ok=True)
    logger.info("[Bootstrap] Workspace directories ready.")
