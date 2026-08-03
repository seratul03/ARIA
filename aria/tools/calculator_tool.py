"""
aria/tools/calculator_tool.py
──────────────────────────────
Safe mathematical expression evaluator with natural language support.

Uses Python's ast module to parse and evaluate expressions WITHOUT using
eval() or exec(). Supports arithmetic, comparison, trig, log, and sqrt.

Natural-language input ("add 3 and 5", "five times ten") is translated
into symbolic math via a rule-based preprocessor.  If the rules can't
handle it, a Groq LLM fallback extracts the expression.

This tool is intentionally improvable by ARIA's Improvement Engine.
"""

from __future__ import annotations

import ast
import logging
import math
import operator
import re
from typing import Any

from aria.tools.base import BaseTool, TestCase, ToolResult

logger = logging.getLogger(__name__)

# ── Whitelisted operators ─────────────────────────────────────────────────────

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

# ── Whitelisted math functions ────────────────────────────────────────────────

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
    "factorial": math.factorial,
    "pi":    math.pi,
    "e":     math.e,
}


# ══════════════════════════════════════════════════════════════════════════════
# TIER 2 — LLM fallback for complex natural-language expressions
# ══════════════════════════════════════════════════════════════════════════════

def _llm_extract_expression(text: str) -> str | None:
    """
    Use Groq LLM to extract a mathematical expression from free-form text.
    Returns a clean symbolic expression string, or None on failure.
    """
    try:
        from aria.core.rate_limiter import groq_limiter
        from aria.config import settings
        from groq import Groq

        groq_limiter.acquire()
        client = Groq(api_key=settings.groq_api_key)

        response = client.chat.completions.create(
            model=settings.groq_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a math expression extractor. The user will give you a "
                        "sentence containing a math problem in natural language. "
                        "Extract ONLY the mathematical expression in Python syntax. "
                        "Return ONLY the expression, nothing else. No words, no "
                        "explanation, no equals sign, no code fences.\n\n"
                        "Examples:\n"
                        '  "add 3 and 5" → 3 + 5\n'
                        '  "what is twenty percent of 250" → (20 / 100) * 250\n'
                        '  "square root of sixty four" → sqrt(64)\n'
                        '  "five factorial" → factorial(5)\n'
                        '  "2 to the power of 10" → 2 ** 10\n'
                        "Available functions: sin, cos, tan, sqrt, log, log2, "
                        "log10, exp, ceil, floor, factorial, abs, round\n"
                        "Available constants: pi, e"
                    ),
                },
                {"role": "user", "content": text[:500]},
            ],
            max_tokens=60,
            temperature=0.0,
        )
        result = response.choices[0].message.content.strip()

        # Basic sanity: reject if the LLM returned prose instead of math
        if len(result) > 100 or "\n" in result:
            return None

        # Strip markdown code fences if the LLM wrapped the answer
        result = re.sub(r"^```\w*\s*", "", result)
        result = re.sub(r"\s*```$", "", result)

        return result.strip()

    except Exception as exc:
        logger.debug("LLM expression extraction failed: %s", exc)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Safe AST evaluator (unchanged from original)
# ══════════════════════════════════════════════════════════════════════════════

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
    expression = expression.strip().replace("^", "**")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Invalid expression syntax: {exc}") from exc
    evaluator = _SafeEvaluator()
    result = evaluator.visit(tree)
    return float(result)


# ══════════════════════════════════════════════════════════════════════════════
# Main tool class
# ══════════════════════════════════════════════════════════════════════════════

class CalculatorTool(BaseTool):
    """
    Safely evaluates mathematical expressions, including natural language.

    Input:
        expression (str): A math expression — symbolic ("2 + 3 * sin(pi/2)")
                          or natural language ("add 3 and 5", "five times ten").

    Output:
        The numeric result as a float.
    """

    name = "calculator_tool"

    def run(self, input: dict) -> ToolResult:
        expression = str(input.get("expression", "")).strip()

        if not expression:
            return ToolResult(success=False, output=None, error="No expression provided.")

        # ── Step 1: Try direct symbolic evaluation ────────────────────────
        first_error = None
        try:
            result = _safe_eval(expression)
            return self._format_result(result)
        except (ValueError, ZeroDivisionError) as exc:
            first_error = exc

        # ── Step 2: LLM fallback ─────────────────────────────────────────
        llm_expr = _llm_extract_expression(expression)
        if llm_expr:
            try:
                result = _safe_eval(llm_expr)
                return self._format_result(result)
            except (ValueError, ZeroDivisionError):
                pass  # LLM produced something unparseable

        error_msg = str(first_error) if first_error else "Could not parse expression."
        return ToolResult(success=False, output=None, error=error_msg)

    @staticmethod
    def _format_result(result: float) -> ToolResult:
        """Validate and wrap a numeric result."""
        if math.isnan(result):
            return ToolResult(success=False, output=None, error="Result is NaN.")
        if math.isinf(result):
            return ToolResult(success=False, output=None, error="Result is infinite (division by zero?).")
        return ToolResult(success=True, output=result)

    def test_cases(self) -> list[TestCase]:
        return [
            TestCase(
                name="word_problem_valid",
                input={"expression": "If I give you 3 candy and you ate 2 candy, how mnay you are left with ?"},
                expected_output=1.0,
                expected_success=True,
            ),
            TestCase(
                name="word_problem_mad",
                input={"expression": "If I give you 5 chocolates and you had previously 10 candy(s), how many banana should I eat for breakfast?"},
                expected_output=None,
                expected_success=False,
            ),

            # ── Original symbolic tests ──────────────────────────────────
            TestCase(
                name="basic_math",
                input={"expression": "2 + 3 * sin(pi/2)"},
                expected_output=5.0,
                expected_success=True,
            ),
            TestCase(
                name="simple_division",
                input={"expression": "10 / 2"},
                expected_output=5.0,
                expected_success=True,
            ),
            TestCase(
                name="division_by_zero",
                input={"expression": "10 / 0"},
                expected_output=None,
                expected_success=False,
            ),
            TestCase(
                name="trigonometry",
                input={"expression": "sin(2)"},
                expected_output=0.9092974268256817,
                expected_success=True,
            ),
            TestCase(
                name="large_number",
                input={"expression": "1e100"},
                expected_output=1e+100,
                expected_success=True,
            ),
            TestCase(
                name="negative_number",
                input={"expression": "-1e100"},
                expected_output=-1e+100,
                expected_success=True,
            ),
            TestCase(
                name="nested_expressions",
                input={"expression": "(2 + 3) * sin(pi/2)"},
                expected_output=5.0,
                expected_success=True,
            ),
            TestCase(
                name="mixed_operations",
                input={"expression": "2 + 3 * sin(pi/2) - 1"},
                expected_output=4.0,
                expected_success=True,
            ),
            # ── Natural language tests ───────────────────────────────────
            TestCase(
                name="nl_add",
                input={"expression": "add 3 and 5"},
                expected_output=8.0,
                expected_success=True,
            ),
            TestCase(
                name="nl_subtract",
                input={"expression": "subtract 3 from 10"},
                expected_output=7.0,
                expected_success=True,
            ),
            TestCase(
                name="nl_multiply",
                input={"expression": "multiply 4 by 6"},
                expected_output=24.0,
                expected_success=True,
            ),
            TestCase(
                name="nl_divide",
                input={"expression": "divide 20 by 4"},
                expected_output=5.0,
                expected_success=True,
            ),
            TestCase(
                name="nl_plus",
                input={"expression": "3 plus 5"},
                expected_output=8.0,
                expected_success=True,
            ),
            TestCase(
                name="nl_times",
                input={"expression": "7 times 8"},
                expected_output=56.0,
                expected_success=True,
            ),
            TestCase(
                name="nl_word_numbers",
                input={"expression": "five plus three"},
                expected_output=8.0,
                expected_success=True,
            ),
            TestCase(
                name="nl_word_multiply",
                input={"expression": "twenty times three"},
                expected_output=60.0,
                expected_success=True,
            ),
            TestCase(
                name="nl_sqrt",
                input={"expression": "square root of 16"},
                expected_output=4.0,
                expected_success=True,
            ),
            TestCase(
                name="nl_power",
                input={"expression": "2 to the power of 10"},
                expected_output=1024.0,
                expected_success=True,
            ),
            TestCase(
                name="nl_what_is",
                input={"expression": "what is 10 plus 5"},
                expected_output=15.0,
                expected_success=True,
            ),
            TestCase(
                name="nl_percent",
                input={"expression": "20 percent of 250"},
                expected_output=50.0,
                expected_success=True,
            ),
        ]