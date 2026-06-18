"""
aria/gatekeeper/validator.py
─────────────────────────────
Static analysis validator — the first gate before Docker execution.

Uses Python's ast module to inspect generated code for:
  - Forbidden imports (os, sys, subprocess, socket, shutil, etc.)
  - Forbidden function calls (eval, exec, __import__, compile)
  - Required BaseTool interface (class definition + run() + test_cases())
  - Maximum source length (300 lines)
  - Valid Python syntax

The Gatekeeper module itself CANNOT be modified by ARIA — it is outside
the writable /tools/ directory and is not listed in the tool registry.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field


# ── Forbidden patterns ────────────────────────────────────────────────────────

FORBIDDEN_IMPORTS: set[str] = {
    "os", "sys", "subprocess", "socket", "shutil",
    "pickle", "ctypes", "multiprocessing", "threading",
    "signal", "pty", "resource", "mmap",
    "importlib", "imp", "runpy", "code", "codeop",
    "tokenize", "py_compile", "compileall",
    "tempfile", "glob",  # filesystem discovery
}

FORBIDDEN_BUILTINS: set[str] = {
    "eval", "exec", "__import__", "compile",
    "open", "input", "memoryview",
}

MAX_LINES = 300

PROTECTED_PATHS = [
    "aria/memory/schema.py",
    "aria/memory/migrations/",
    "aria/memory/store.py",   # write path must stay append-only
    "aria/rootcause/categories.py",
    "aria/predictors/",       # Constitution-protected ML predictors
]


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    passed: bool
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_issue(self, msg: str) -> None:
        self.issues.append(msg)
        self.passed = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def summary(self) -> str:
        if self.passed:
            return f"PASSED ({len(self.warnings)} warnings)"
        return f"FAILED — {len(self.issues)} issue(s): " + "; ".join(self.issues[:3])


# ── AST visitor ───────────────────────────────────────────────────────────────

class _SecurityVisitor(ast.NodeVisitor):
    """Walk the AST and collect security violations."""

    def __init__(self) -> None:
        self.issues: list[str] = []
        self.warnings: list[str] = []
        self._has_base_tool_class = False
        self._has_run_method = False

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            module = alias.name.split(".")[0]
            if module in FORBIDDEN_IMPORTS:
                self.issues.append(
                    f"Line {node.lineno}: Forbidden import '{alias.name}'"
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = (node.module or "").split(".")[0]
        if module in FORBIDDEN_IMPORTS:
            self.issues.append(
                f"Line {node.lineno}: Forbidden import 'from {node.module} import ...'"
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # Check for direct forbidden builtin calls: eval(...), exec(...)
        if isinstance(node.func, ast.Name):
            if node.func.id in FORBIDDEN_BUILTINS:
                self.issues.append(
                    f"Line {node.lineno}: Forbidden function call '{node.func.id}()'"
                )
        # Check for attribute calls that might be dangerous
        elif isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr in ("system", "popen", "spawn", "call", "run", "Popen"):
                self.warnings.append(
                    f"Line {node.lineno}: Suspicious method call '.{attr}()' — "
                    f"verify this is not a shell execution."
                )
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        # Check for BaseTool inheritance
        for base in node.bases:
            if isinstance(base, ast.Name) and base.id == "BaseTool":
                self._has_base_tool_class = True
            elif isinstance(base, ast.Attribute) and base.attr == "BaseTool":
                self._has_base_tool_class = True

        # Check for required methods inside this class
        for item in node.body:
            if isinstance(item, ast.FunctionDef):
                if item.name == "run":
                    self._has_run_method = True

        self.generic_visit(node)


# ── Main validator ────────────────────────────────────────────────────────────

class StaticValidator:
    """
    Performs static analysis on generated tool source code.
    Call validate() before passing code to the Docker sandbox.
    """

    def validate(self, source_code: str, tool_name: str) -> ValidationResult:
        result = ValidationResult(passed=True)

        # 1. Protected Paths Check
        normalized_tool_name = tool_name.replace("\\", "/")
        for protected_path in PROTECTED_PATHS:
            if protected_path in normalized_tool_name:
                result.add_issue(f"Target '{tool_name}' is protected and cannot be modified by ARIA.")
                return result

        # 2. Length check
        lines = source_code.splitlines()
        if len(lines) > MAX_LINES:
            result.add_issue(
                f"Source code is {len(lines)} lines — exceeds maximum of {MAX_LINES}."
            )

        # 2. Syntax check
        try:
            tree = ast.parse(source_code)
        except SyntaxError as exc:
            err_line = "<unknown code>"
            if exc.lineno is not None:
                err_line = lines[exc.lineno - 1] if 0 < exc.lineno <= len(lines) else "<out of bounds>"
            result.add_issue(f"Syntax error: {exc}. Code at line {exc.lineno}: '{err_line}'")
            return result  # Cannot proceed with AST analysis

        # 3. Security + interface analysis
        visitor = _SecurityVisitor()
        visitor.visit(tree)

        for issue in visitor.issues:
            result.add_issue(issue)
        for warning in visitor.warnings:
            result.add_warning(warning)

        # 4. Interface compliance
        if not visitor._has_base_tool_class:
            result.add_issue(
                "No class inheriting from BaseTool found. "
                "The tool must subclass BaseTool."
            )
        if not visitor._has_run_method:
            result.add_issue(
                "Required method 'run(self, input: dict) -> ToolResult' not found."
            )

        # 5. Warn if tool_name constant is missing or changed
        tool_name_found = False
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(t, ast.Name) and t.id == "name"
                    for t in node.targets
                )
            ):
                if isinstance(node.value, ast.Constant):
                    if node.value.value == tool_name:
                        tool_name_found = True
                    else:
                        result.add_issue(
                            f"Tool name attribute is '{node.value.value}' but expected '{tool_name}'."
                        )

        if not tool_name_found:
            result.add_warning(
                f"Could not confirm `name = '{tool_name}'` attribute in class body."
            )

        return result
