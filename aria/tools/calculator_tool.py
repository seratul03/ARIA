"""
aria/tools/calculator_tool.py
──────────────────────────────
Safe mathematical expression evaluator.

Uses Python's ast module to parse and evaluate expressions WITHOUT using
eval() or exec(). Supports arithmetic, comparison, trig, log, and sqrt.

This tool is intentionally improvable by ARIA's Improvement Engine.
"""

from __future__ import annotations

import ast
import math
import operator
from typing import Any

from aria.tools.base import BaseTool, TestCase, ToolResult


# Whitelisted operators
_OPERATORS: dict[type, Any] = {
    ast.Add:      operator.add,
    ast.Sub:      operator.sub,
    ast.Mult:     operator.mul,
    ast.Div:      operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod:      operator.mod,
    ast.Pow:      operator.pow,
    ast.USub:     operator.neg,
    ast.UAdd:     operator.pos,
    ast.BitAnd:   operator.and_,
    ast.BitOr:    operator.or_,
    ast.BitXor:   operator.xor,
    ast.Invert:   operator.invert,
    ast.LShift:   operator.lshift,
    ast.RShift:   operator.rshift,
}

# Whitelisted math functions
_FUNCTIONS: dict[str, Any] = {
    "abs":   abs,
    "round": round,
    "sin":   math.sin,
    "cos":   math.cos,
    "tan":   math.tan,
    "sqrt":  math.sqrt,
    "log":   math.log,
    "log2":  math.log2,
    "log10": math.log10,
    "exp":   math.exp,
    "ceil":  math.ceil,
    "floor": math.floor,
    "pi":    math.pi,
    "e":     math.e,
}


class _SafeEvaluator(ast.NodeVisitor):
    """AST visitor that safely evaluates mathematical expressions."""

    def visit_Expression(self, node: ast.Expression) -> Any:
        return self.visit(node.body)

    def visit_Constant(self, node: ast.Constant) -> Any:
        if isinstance(node.value, (int, float, complex)):
            return node.value
        raise ValueError(f"Unsupported constant type: {type(node.value)}")

    def visit_BinOp(self, node: ast.BinOp) -> Any:
        op_fn = _OPERATORS.get(type(node.op))
        if op_fn is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        left = self.visit(node.left)
        right = self.visit(node.right)
        return op_fn(left, right)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> Any:
        op_fn = _OPERATORS.get(type(node.op))
        if op_fn is None:
            raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")
        return op_fn(self.visit(node.operand))

    def visit_Call(self, node: ast.Call) -> Any:
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only direct function calls are allowed.")
        fn = _FUNCTIONS.get(node.func.id)
        if fn is None:
            raise ValueError(f"Unsupported function: {node.func.id}")
        args = [self.visit(a) for a in node.args]
        return fn(*args)

    def visit_Name(self, node: ast.Name) -> Any:
        if node.id in _FUNCTIONS:
            return _FUNCTIONS[node.id]
        raise ValueError(f"Unsupported name: {node.id}")

    def generic_visit(self, node: ast.AST) -> Any:
        raise ValueError(f"Unsupported AST node: {type(node).__name__}")


def _safe_eval(expression: str) -> float:
    """Parse and evaluate a math expression safely using the AST."""
    expression = expression.strip()
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Invalid expression syntax: {exc}") from exc
    evaluator = _SafeEvaluator()
    result = evaluator.visit(tree)
    return float(result)


class CalculatorTool(BaseTool):
    """
    Safely evaluates mathematical expressions.

    Input:
        expression (str): A math expression string (e.g., "2 + 3 * sin(pi/2)")

    Output:
        The numeric result as a float.
    """

    name = "calculator_tool"

    def run(self, input: dict) -> ToolResult:
        expression = str(input.get("expression", "")).strip()

        if not expression:
            return ToolResult(success=False, output=None, error="No expression provided.")

        try:
            result = _safe_eval(expression)

            # Handle special float cases
            if math.isnan(result):
                return ToolResult(success=False, output=None, error="Result is NaN.")
            if math.isinf(result):
                return ToolResult(success=False, output=None, error="Result is infinite (division by zero?).")

            return ToolResult(success=True, output=result)

        except ZeroDivisionError:
            return ToolResult(success=False, output=None, error="Division by zero.")
        except ValueError as exc:
            return ToolResult(success=False, output=None, error=str(exc))
        except Exception as exc:
            return ToolResult(success=False, output=None, error=f"Evaluation error: {exc}")

    def test_cases(self) -> list[TestCase]:
        return [
            TestCase(
                name="basic_arithmetic",
                input={"expression": "2 + 3 * 4"},
                expected_success=True,
                description="Standard precedence: should return 14.0",
            ),
            TestCase(
                name="trig_function",
                input={"expression": "sin(pi / 2)"},
                expected_success=True,
                description="sin(π/2) = 1.0",
            ),
            TestCase(
                name="bitwise_xor",
                input={"expression": "5 ^ 3"},
                expected_success=True,
                description="5 ^ 3 = 6.0",
            ),
            TestCase(
                name="sqrt",
                input={"expression": "sqrt(144)"},
                expected_success=True,
                description="sqrt(144) = 12.0",
            ),
            TestCase(
                name="empty_expression",
                input={"expression": ""},
                expected_success=False,
                description="Empty expression must fail.",
            ),
            TestCase(
                name="division_by_zero",
                input={"expression": "1 / 0"},
                expected_success=False,
                description="Division by zero must fail gracefully.",
            ),
            TestCase(
                name="nested_expression",
                input={"expression": "log(exp(1))"},
                expected_success=True,
                description="log(e^1) = 1.0",
            ),
            TestCase(
                name="forbidden_import",
                input={"expression": "__import__('os').system('whoami')"},
                expected_success=False,
                description="Must reject any non-math expression.",
            ),
        ]
