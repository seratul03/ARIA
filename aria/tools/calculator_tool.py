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
# TIER 1 — Rule-based natural-language → symbolic-math preprocessor
# ══════════════════════════════════════════════════════════════════════════════

# English word → digit mapping (covers zero through ninety-nine + large scales)
_WORD_TO_NUM: dict[str, int] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90, "hundred": 100, "thousand": 1_000,
    "million": 1_000_000, "billion": 1_000_000_000,
    # common spoken aliases
    "a dozen": 12, "a hundred": 100, "a thousand": 1_000,
}

# Scale words that multiply the running total
_SCALES = {"hundred", "thousand", "million", "billion"}


def _words_to_number(text: str) -> float | None:
    """
    Convert an English number phrase into a numeric value.

    Handles compound forms like "twenty three", "one hundred forty five",
    and "three thousand two hundred".  Returns None if parsing fails.
    """
    text = text.strip().lower()

    # Already a plain number?
    try:
        return float(text)
    except ValueError:
        pass

    # Normalise hyphens ("twenty-three" → "twenty three")
    text = text.replace("-", " ")
    words = text.split()

    current = 0.0   # accumulator for the current group
    result = 0.0    # final total
    found_any = False

    for word in words:
        if word == "and":
            continue  # skip filler

        if word in _WORD_TO_NUM:
            val = _WORD_TO_NUM[word]
            found_any = True

            if word in _SCALES:
                if current == 0:
                    current = 1  # "hundred" alone means 1 × 100
                if val >= 1_000:
                    current *= val
                    result += current
                    current = 0
                else:
                    current *= val
            else:
                current += val
        else:
            # Try as a raw number token (e.g. "3" mixed in with words)
            try:
                current += float(word)
                found_any = True
            except ValueError:
                return None  # unrecognised token → can't parse

    if not found_any:
        return None

    result += current
    return result


# ── Verbal pattern → operator mapping ─────────────────────────────────────────

# Each tuple: (compiled regex, replacement template using \1 / \2 groups)
# Groups capture the operands (which may be word-numbers or digits).
_NUM = r"([\w\s./-]+?)"  # lazy capture for an operand (words or digits)

# Prefix-stripping patterns (must come first so "what is X plus Y" is
# reduced to "X plus Y" before the operator patterns try to match).
_PREFIX_PATTERNS: list[tuple[re.Pattern, str]] = [
    # "what is X …" — strip the preamble and re-process
    (re.compile(r"^what\s+is\s+(.+)$", re.I),                                r"\1"),
    # "calculate X" — strip the verb and re-process
    (re.compile(r"^(?:calculate|compute|evaluate|solve)\s+(.+)$", re.I),      r"\1"),
]

# Operator patterns (each produces a symbolic expression from operands)
_OPERATOR_PATTERNS: list[tuple[re.Pattern, str]] = [
    # "add X and Y", "sum of X and Y"
    (re.compile(rf"^(?:add|sum\s+of)\s+{_NUM}\s+and\s+{_NUM}$", re.I),      r"\1 + \2"),
    # "X plus Y"
    (re.compile(rf"^{_NUM}\s+plus\s+{_NUM}$", re.I),                          r"\1 + \2"),
    # "subtract X from Y"  → Y - X
    (re.compile(rf"^subtract\s+{_NUM}\s+from\s+{_NUM}$", re.I),               r"\2 - \1"),
    # "X minus Y"
    (re.compile(rf"^{_NUM}\s+minus\s+{_NUM}$", re.I),                         r"\1 - \2"),
    # "multiply X and Y", "multiply X by Y"
    (re.compile(rf"^multiply\s+{_NUM}\s+(?:and|by)\s+{_NUM}$", re.I),         r"\1 * \2"),
    # "X times Y"
    (re.compile(rf"^{_NUM}\s+times\s+{_NUM}$", re.I),                         r"\1 * \2"),
    # "X multiplied by Y"
    (re.compile(rf"^{_NUM}\s+multiplied\s+by\s+{_NUM}$", re.I),               r"\1 * \2"),
    # "divide X by Y"
    (re.compile(rf"^divide\s+{_NUM}\s+by\s+{_NUM}$", re.I),                   r"\1 / \2"),
    # "X divided by Y"
    (re.compile(rf"^{_NUM}\s+divided\s+by\s+{_NUM}$", re.I),                  r"\1 / \2"),
    # "X to the power of Y", "X raised to Y"
    (re.compile(rf"^{_NUM}\s+(?:to\s+the\s+power\s+of|raised\s+to)\s+{_NUM}$", re.I),
     r"\1 ** \2"),
    # "square root of X"
    (re.compile(rf"^(?:square\s+root\s+of|sqrt\s+of|sqrt)\s+{_NUM}$", re.I),  r"sqrt(\1)"),
    # "cube root of X"
    (re.compile(rf"^cube\s+root\s+of\s+{_NUM}$", re.I),                       r"\1 ** (1/3)"),
    # "X modulo Y", "X mod Y"
    (re.compile(rf"^{_NUM}\s+(?:modulo|mod)\s+{_NUM}$", re.I),                r"\1 % \2"),
    # "X percent of Y"
    (re.compile(rf"^{_NUM}\s+percent\s+of\s+{_NUM}$", re.I),                  r"(\1 / 100) * \2"),
    # "factorial of X"
    (re.compile(rf"^factorial\s+of\s+{_NUM}$", re.I),                          r"factorial(\1)"),
]


def _resolve_operand(text: str) -> str:
    """
    Convert a single operand token to a numeric string.
    Tries word-to-number first, then returns the raw text for AST parsing.
    """
    text = text.strip()
    num = _words_to_number(text)
    if num is not None:
        # Return an integer string if the value is whole
        return str(int(num)) if num == int(num) else str(num)
    return text  # already symbolic (e.g. "pi", "3.14")


def _apply_operator_patterns(expr: str) -> str | None:
    """
    Try each operator pattern against the expression.
    Returns the resolved symbolic string, or None if nothing matched.
    """
    for pattern, template in _OPERATOR_PATTERNS:
        m = pattern.match(expr)
        if m:
            replaced = pattern.sub(template, expr)
            # Resolve word-number operands in the replaced expression
            # Split on the operator symbols, resolve each operand, rejoin
            parts = re.split(r"(\s*[+\-*/%()]+\s*|\s*\*\*\s*)", replaced)
            resolved = []
            for part in parts:
                stripped = part.strip()
                if stripped and not re.match(r"^[+\-*/%()]+$|^\*\*$", stripped):
                    resolved.append(_resolve_operand(stripped))
                else:
                    resolved.append(part)
            return "".join(resolved)
    return None


def _preprocess_natural_language(expression: str) -> str:
    """
    Attempt to translate a natural-language math phrase into a symbolic
    expression string.  Falls through to the original string if no pattern
    matches.

    Pipeline:
      1. Strip prefix phrases ("what is …", "calculate …") via recursion.
      2. Try operator patterns ("X plus Y", "add X and Y", etc.).
      3. Replace any stray English number words ("three" → "3").
    """
    expr = expression.strip()

    # Pass 1 — strip prefix phrases and recurse
    for pattern, template in _PREFIX_PATTERNS:
        m = pattern.match(expr)
        if m:
            inner = pattern.sub(template, expr).strip()
            return _preprocess_natural_language(inner)

    # Pass 2 — try operator patterns
    result = _apply_operator_patterns(expr)
    if result is not None:
        return result

    # Pass 3 — even if no pattern matched, try to replace any English
    # number words that appear in an otherwise symbolic expression.
    # e.g. "three + five" → "3 + 5"
    tokens = re.split(r"(\s+)", expr)
    changed = False
    result_tokens = []
    for token in tokens:
        num = _words_to_number(token)
        if num is not None and not token.replace(".", "").replace("-", "").isdigit():
            result_tokens.append(str(int(num)) if num == int(num) else str(num))
            changed = True
        else:
            result_tokens.append(token)
    if changed:
        return "".join(result_tokens)

    return expr  # no transformation possible


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

        # ── Step 2: Rule-based natural-language → symbolic conversion ─────
        preprocessed = _preprocess_natural_language(expression)
        if preprocessed != expression:
            try:
                result = _safe_eval(preprocessed)
                return self._format_result(result)
            except (ValueError, ZeroDivisionError):
                pass  # fall through to LLM

        # ── Step 3: LLM fallback ─────────────────────────────────────────
        llm_expr = _llm_extract_expression(expression)
        if llm_expr:
            try:
                result = _safe_eval(llm_expr)
                return self._format_result(result)
            except (ValueError, ZeroDivisionError):
                pass  # LLM produced something unparseable

        # ── All tiers failed ──────────────────────────────────────────────
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