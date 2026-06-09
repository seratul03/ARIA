"""
aria/versioning/git_manager.py
────────────────────────────────
Manages Git version control for ARIA's tool improvements.

Every tool deployment and rollback is recorded as a Git commit, providing:
  - A complete audit trail of every change
  - Easy rollback to any previous version
  - Visualization of how each tool evolved over time

Uses the `gitpython` library. If the project is not a Git repo yet,
it is automatically initialized on first use.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class CommitInfo:
    hash: str
    message: str
    timestamp: float
    author: str


class GitManager:
    """
    Wraps GitPython for ARIA-specific version control operations.
    All commits use the author "ARIA System <aria@local>".
    """

    _ARIA_AUTHOR = "ARIA System"
    _ARIA_EMAIL = "aria@local"

    def __init__(self, repo_root: Path | None = None) -> None:
        self._root = repo_root or Path.cwd()
        self._repo = self._get_or_init_repo()

    def _get_or_init_repo(self):
        """Get the existing Git repo or initialize a new one."""
        try:
            import git  # type: ignore
            try:
                return git.Repo(self._root, search_parent_directories=True)
            except git.InvalidGitRepositoryError:
                logger.info("[GitManager] No Git repo found. Initializing...")
                repo = git.Repo.init(self._root)
                # Configure identity
                repo.config_writer().set_value("user", "name", self._ARIA_AUTHOR).release()
                repo.config_writer().set_value("user", "email", self._ARIA_EMAIL).release()
                return repo
        except ImportError:
            logger.warning("[GitManager] gitpython not installed. Version control disabled.")
            return None

    def commit_tool(
        self,
        tool_name: str,
        message: str,
        extra_files: list[Path] | None = None,
    ) -> str | None:
        """
        Stage the tool file and commit it.

        Args:
            tool_name:   e.g. "search_tool"
            message:     Commit message
            extra_files: Additional files to include in the commit

        Returns:
            The short commit hash, or None if git is unavailable.
        """
        if self._repo is None:
            return None

        try:
            import git  # type: ignore

            tool_rel_path = f"aria/tools/{tool_name}.py"
            tool_abs_path = self._root / tool_rel_path

            if not tool_abs_path.exists():
                logger.warning(f"[GitManager] Tool file not found: {tool_rel_path}")
                return None

            # Stage tool file
            self._repo.index.add([str(tool_abs_path)])

            # Stage extra files if provided
            if extra_files:
                for f in extra_files:
                    if f.exists():
                        self._repo.index.add([str(f)])

            # Configure author
            author = git.Actor(self._ARIA_AUTHOR, self._ARIA_EMAIL)

            commit = self._repo.index.commit(
                f"[ARIA] {message}",
                author=author,
                committer=author,
            )

            short_hash = commit.hexsha[:8]
            logger.info(f"[GitManager] Committed: {short_hash} — {message}")
            try:
                from aria.core.tracer import emit_trace
                emit_trace("versioning", "commit", {"tool": tool_name, "hash": short_hash, "message": message})
            except ImportError:
                pass
            return short_hash

        except Exception as exc:
            logger.error(f"[GitManager] Commit failed: {exc}")
            return None

    def rollback_tool(self, tool_name: str) -> bool:
        """
        Revert the tool file to the previous commit's version.

        Returns True if rollback succeeded, False otherwise.
        """
        if self._repo is None:
            return False

        try:
            tool_rel_path = f"aria/tools/{tool_name}.py"
            tool_abs_path = self._root / tool_rel_path

            # Find the previous commit that touched this file
            commits = list(self._repo.iter_commits(paths=tool_rel_path, max_count=2))

            if len(commits) < 2:
                logger.warning(
                    f"[GitManager] No previous commit found for '{tool_name}'. "
                    f"Cannot rollback."
                )
                return False

            previous_commit = commits[1]  # commits[0] is HEAD

            # Restore file content from previous commit
            blob = previous_commit.tree[tool_rel_path]
            content = blob.data_stream.read().decode("utf-8")
            tool_abs_path.write_text(content, encoding="utf-8")

            logger.info(
                f"[GitManager] Rolled back '{tool_name}' to "
                f"commit {previous_commit.hexsha[:8]}"
            )
            try:
                from aria.core.tracer import emit_trace
                emit_trace("versioning", "rollback", {"tool": tool_name, "success": True, "target_hash": previous_commit.hexsha[:8]})
            except ImportError:
                pass
            return True

        except Exception as exc:
            logger.error(f"[GitManager] Rollback failed for '{tool_name}': {exc}")
            try:
                from aria.core.tracer import emit_trace
                emit_trace("versioning", "rollback", {"tool": tool_name, "success": False, "error": str(exc)})
            except ImportError:
                pass
            return False

    def get_tool_history(self, tool_name: str, limit: int = 10) -> list[CommitInfo]:
        """Return the commit history for a specific tool file."""
        if self._repo is None:
            return []

        try:
            tool_rel_path = f"aria/tools/{tool_name}.py"
            commits = list(
                self._repo.iter_commits(paths=tool_rel_path, max_count=limit)
            )
            return [
                CommitInfo(
                    hash=c.hexsha[:8],
                    message=c.message.strip(),
                    timestamp=float(c.authored_date),
                    author=str(c.author),
                )
                for c in commits
            ]
        except Exception as exc:
            logger.error(f"[GitManager] History lookup failed: {exc}")
            return []

    def initial_commit_tools(self) -> None:
        """
        Make an initial commit of all tool files if the repo is brand new.
        Called once at startup.
        """
        if self._repo is None:
            return

        try:
            import git  # type: ignore

            tools_dir = self._root / "aria" / "tools"
            if not tools_dir.exists():
                return

            tool_files = list(tools_dir.glob("*.py"))
            if not tool_files:
                return

            # Check if there are any commits yet
            try:
                self._repo.head.commit
                # Repo already has commits — don't overwrite
                return
            except (git.BadName, ValueError):
                pass  # No commits yet — proceed

            # Stage all tool files
            for f in tool_files:
                self._repo.index.add([str(f)])

            author = git.Actor(self._ARIA_AUTHOR, self._ARIA_EMAIL)
            self._repo.index.commit(
                "[ARIA] Initial commit — baseline tool versions",
                author=author,
                committer=author,
            )
            logger.info("[GitManager] Initial commit created.")

        except Exception as exc:
            logger.warning(f"[GitManager] Initial commit failed: {exc}")


# ── Shared singleton ──────────────────────────────────────────────────────────

git_manager = GitManager()
