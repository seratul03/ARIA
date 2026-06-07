"""
aria/tools/base.py
──────────────────
Base interface that ALL ARIA tools must implement.

Every tool in aria/tools/ is a Python module containing exactly one class
that inherits from BaseTool. The Gatekeeper will verify this contract
before accepting generated improvements.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """
    Standardized result object returned by every tool.
    The metrics collector reads `success`, `output`, `error`, and `latency_seconds`.
    """
    success: bool
    output: Any                    # The tool's actual output
    error: str | None = None       # Error message if success=False
    latency_seconds: float = 0.0  # Populated by the metrics collector


@dataclass
class TestCase:
    """
    A single input/output test case embedded in a tool.
    Used by the Gatekeeper sandbox to validate generated improvements.
    """
    name: str
    input: dict                        # Passed to tool.run()
    expected_success: bool = True      # Whether we expect success=True
    # Optional: check if specific substring appears in output
    output_contains: str | None = None
    # Optional: custom validator function (not used in sandbox — too risky)
    description: str = ""


class BaseTool(ABC):
    """
    Abstract base class for all ARIA tools.

    Every tool must:
      1. Define a unique `name` string attribute.
      2. Implement `run(input: dict) -> ToolResult`.
      3. Provide at least 3 test cases via `test_cases()`.

    Tools MUST NOT:
      - Import os, sys, subprocess, socket, shutil, or pathlib at the module level
        in ways that access the host filesystem or network outside their purpose.
      - Use eval(), exec(), or __import__().
      - Exceed 300 lines of source code.
    """

    name: str = "base_tool"

    @abstractmethod
    def run(self, input: dict) -> ToolResult:
        """
        Execute the tool with the given input dictionary.

        Args:
            input: A dict of parameters specific to this tool.

        Returns:
            A ToolResult with success flag, output, and optional error.
        """
        ...

    @abstractmethod
    def test_cases(self) -> list[TestCase]:
        """
        Return a list of TestCase objects used for automated validation.
        At least 3 test cases are required — more is better.
        Test cases must be self-contained and not require network access.
        """
        ...

    def describe(self) -> dict:
        """Return a human-readable description of this tool."""
        return {
            "name": self.name,
            "class": type(self).__name__,
            "test_count": len(self.test_cases()),
        }
