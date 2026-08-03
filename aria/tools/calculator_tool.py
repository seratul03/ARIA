from __future__ import annotations

import ast
import math
import operator
from typing import Any

from aria.tools.base import BaseTool, TestCase, ToolResult
from aria.config import settings
from groq import Groq

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

class _SafeEvaluator(ast.NodeVisitor):
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
    expression = expression.strip().replace("^", "**")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Invalid expression syntax: {exc}") from exc
    evaluator = _SafeEvaluator()
    result = evaluator.visit(tree)
    return float(result)


class CalculatorTool(BaseTool):
    name = "calculator_tool"

    def __init__(self):
        # We instantiate Groq with the actual API key from settings
        # so it doesn't fail when attempting real requests.
        self.groq_client = Groq(api_key=settings.groq_api_key)

    def _extract_math(self, text: str) -> str:
        """
        Extracts a pure mathematical expression from natural language using the LLM.
        If the query is nonsensical or impossible to evaluate mathematically, returns 'ERROR'.
        """
        # If it's already a simple math expression, just return it.
        # This prevents unnecessary LLM calls for standard input.
        allowed_chars = set("0123456789+-*/(). ^e")
        if all(c in allowed_chars or c.isspace() for c in text):
            return text

        try:
            response = self.groq_client.chat.completions.create(
                model=settings.groq_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a strict mathematics extraction engine. "
                            "Extract the exact mathematical expression from the user's query. "
                            "Respond ONLY with the mathematical expression (e.g., '3 - 2' or '5 * 2'). "
                            "If the user asks an illogical, non-mathematical question (like 'how many bananas?'), "
                            "you MUST respond with exactly the word 'ERROR'. Do not explain."
                        )
                    },
                    {"role": "user", "content": text}
                ],
                temperature=0.0,
                max_tokens=30,
            )
            result = response.choices[0].message.content.strip()
            return result
        except Exception:
            # Fallback on LLM failure: just pass original text and let safe_eval handle/fail
            return text

    def run(self, input: dict) -> ToolResult:
        expression = str(input.get("expression", "")).strip()

        if not expression:
            return ToolResult(success=False, output=None, error="No expression provided.")

        # Extract mathematical expression from natural language
        math_expr = self._extract_math(expression)
        
        if math_expr == "ERROR":
            return ToolResult(success=False, output=None, error="Illogical or non-mathematical query.")

        try:
            result = _safe_eval(math_expr)

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
