"""
aria/tools/file_reader_tool.py
───────────────────────────────
Reads text files from an allowlisted set of directories.

Access is strictly limited to directories listed in FILE_READER_ALLOWED_DIRS.
Path traversal attacks (../../etc/passwd) are blocked by resolving paths
to their canonical form and checking against allowed roots.

This tool is intentionally improvable by ARIA's Improvement Engine.
"""

from __future__ import annotations

from pathlib import Path

from aria.config import settings
from aria.tools.base import BaseTool, TestCase, ToolResult


class FileReaderTool(BaseTool):
    """
    Reads the contents of a text file from an allowed directory.

    Input:
        path (str): Relative or absolute path to the file.
        max_lines (int, optional): Maximum lines to return. Default: 200.
        encoding (str, optional): File encoding. Default: 'utf-8'.

    Output:
        A dict with 'path', 'content', 'lines', and 'size_bytes' keys.
    """

    name = "file_reader_tool"

    def __init__(self) -> None:
        self._allowed_roots: list[Path] = [
            Path(d).resolve() for d in settings.file_reader_allowed_dirs
        ]

    def _is_allowed(self, target: Path) -> bool:
        """Return True only if `target` is inside one of the allowed roots."""
        try:
            resolved = target.resolve()
        except Exception:
            return False
        return any(
            resolved == root or root in resolved.parents
            for root in self._allowed_roots
        )

    def run(self, input: dict) -> ToolResult:
        raw_path = str(input.get("path", "")).strip()
        max_lines = int(input.get("max_lines", 200))
        encoding = str(input.get("encoding", "utf-8"))

        if not raw_path:
            return ToolResult(success=False, output=None, error="No path provided.")

        target = Path(raw_path)
        if not target.is_absolute():
            # Try to resolve relative to cwd
            target = Path.cwd() / target

        # Security: reject if not within allowed directories
        if not self._is_allowed(target):
            return ToolResult(
                success=False,
                output=None,
                error=(
                    f"Access denied: '{raw_path}' is not within allowed directories. "
                    f"Allowed: {[str(r) for r in self._allowed_roots]}"
                ),
            )

        if not target.exists():
            return ToolResult(
                success=False,
                output=None,
                error=f"File not found: '{raw_path}'",
            )

        if not target.is_file():
            return ToolResult(
                success=False,
                output=None,
                error=f"Path is not a file: '{raw_path}'",
            )

        try:
            size_bytes = target.stat().st_size

            # Refuse to read extremely large files
            if size_bytes > 5 * 1024 * 1024:  # 5 MB
                return ToolResult(
                    success=False,
                    output=None,
                    error=f"File too large ({size_bytes} bytes). Max 5 MB.",
                )

            with target.open(encoding=encoding, errors="replace") as f:
                lines = f.readlines()

            truncated = len(lines) > max_lines
            content_lines = lines[:max_lines]
            content = "".join(content_lines)

            return ToolResult(
                success=True,
                output={
                    "path": str(target),
                    "content": content,
                    "lines": len(content_lines),
                    "total_lines": len(lines),
                    "size_bytes": size_bytes,
                    "truncated": truncated,
                    "encoding": encoding,
                },
            )
        except UnicodeDecodeError as exc:
            return ToolResult(
                success=False,
                output=None,
                error=f"Encoding error reading '{raw_path}': {exc}",
            )
        except PermissionError:
            return ToolResult(
                success=False,
                output=None,
                error=f"Permission denied reading '{raw_path}'.",
            )
        except Exception as exc:
            return ToolResult(success=False, output=None, error=str(exc))

    def test_cases(self) -> list[TestCase]:
        return [
            TestCase(
                name="empty_path",
                input={"path": ""},
                expected_success=False,
                description="Empty path must fail.",
            ),
            TestCase(
                name="nonexistent_file",
                input={"path": "./workspace/does_not_exist_aria_test.txt"},
                expected_success=False,
                description="Missing file must fail gracefully.",
            ),
            TestCase(
                name="path_traversal_attack",
                input={"path": "../../etc/passwd"},
                expected_success=False,
                description="Path traversal must be blocked.",
            ),
            TestCase(
                name="absolute_outside_allowed",
                input={"path": "C:/Windows/System32/drivers/etc/hosts"},
                expected_success=False,
                description="Absolute paths outside allowed dirs must be blocked.",
            ),
        ]
