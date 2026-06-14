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
    _verify_gatekeeper_integrity()
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
    from aria.memory.schema import run_migrations

    db_path = settings.db_path
    init_db(db_path)
    run_migrations(db_path)
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


def _verify_gatekeeper_integrity() -> None:
    """Verify that gatekeeper files match their expected hashes and are immutable."""
    import hashlib
    import json
    import os

    gatekeeper_dir = Path(__file__).parent / "gatekeeper"
    manifest_path = gatekeeper_dir / "manifest.json"

    if not manifest_path.exists():
        print("\n[ARIA] Gatekeeper Error: manifest.json is missing.\n", file=sys.stderr)
        sys.exit(1)

    with open(manifest_path, "r", encoding="utf-8") as f:
        try:
            manifest = json.load(f)
        except json.JSONDecodeError:
            print("\n[ARIA] Gatekeeper Error: manifest.json is invalid.\n", file=sys.stderr)
            sys.exit(1)

    for filename in ["validator.py", "sandbox.py", "test_verifier.py", "cli.py"]:
        filepath = gatekeeper_dir / filename
        if not filepath.exists():
            print(f"\n[ARIA] Gatekeeper Error: {filename} is missing.\n", file=sys.stderr)
            sys.exit(1)

        # Compute SHA256
        sha256 = hashlib.sha256()
        with open(filepath, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                sha256.update(chunk)
        
        computed_hash = sha256.hexdigest()
        expected_hash = manifest.get(filename)

        if computed_hash != expected_hash:
            print(f"\n[ARIA] Gatekeeper Integrity Error: {filename} hash mismatch!\nExpected: {expected_hash}\nGot:      {computed_hash}\n", file=sys.stderr)
            sys.exit(1)

        # Check immutability (enforced in Docker, warned on Windows local)
        if os.access(filepath, os.W_OK):
            if sys.platform != "win32":
                print(f"\n[ARIA] Gatekeeper Security Error: {filename} is writable!\nGatekeeper must be mounted as read-only.\n", file=sys.stderr)
                sys.exit(1)
            else:
                logger.warning(f"[Gatekeeper] {filename} is writable. Ensure read-only on production.")

    if sys.platform != "win32" and os.access(gatekeeper_dir, os.W_OK):
        print("\n[ARIA] Gatekeeper Security Error: Gatekeeper directory is writable!\n", file=sys.stderr)
        sys.exit(1)

    logger.info("[Bootstrap] Gatekeeper integrity verified.")

