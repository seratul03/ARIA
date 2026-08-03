from __future__ import annotations

import ast
import json
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

    def _extract_math(self, text: str) -> tuple[str, bool]:
        """
        Extracts a mathematical expression and a flag indicating if it's a delta question
        using the LLM. Returns (expression, is_delta_question).
        If the query is nonsensical, returns ('ERROR', False).
        """
        allowed_chars = set("0123456789+-*/(). ^e")
        if all(c in allowed_chars or c.isspace() for c in text):
            return text, False

        try:
            response = self.groq_client.chat.completions.create(
                model=settings.groq_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a strict mathematics extraction engine. "
                            "Extract the exact mathematical expression to solve the user's query. "
                            "If the user is asking how to reach a target amount from a current amount, "
                            "write the expression that calculates the difference (e.g., target - current) "
                            "and set 'is_delta_question' to true. "
                            "CRITICAL: The expression MUST be evaluable by Python's eval(). "
                            "DO NOT include variables (like 'x') or an equals sign ('='). "
                            "For example, output '100 - (40 + 30)', NOT '40 + 30 + x = 100'. "
                            "If the user asks an illogical, non-mathematical question, "
                            "set expression to 'ERROR'. "
                            "Respond ONLY in valid JSON format with keys 'expression' (string) and 'is_delta_question' (boolean)."
                        )
                    },
                    {"role": "user", "content": text}
                ],
                temperature=0.0,
                max_tokens=60,
                response_format={"type": "json_object"}
            )
            
            raw = response.choices[0].message.content.strip()
            data = json.loads(raw)
            return str(data.get("expression", "ERROR")), bool(data.get("is_delta_question", False))
            
        except Exception as exc:
            # Fallback on LLM failure: just pass original text and assume not delta
            return text, False

    def run(self, input: dict) -> ToolResult:
        expression = str(input.get("expression", "")).strip()

        if not expression:
            return ToolResult(success=False, output=None, error="No expression provided.")

        # Extract mathematical expression from natural language
        math_expr, is_delta = self._extract_math(expression)
        
        if math_expr == "ERROR":
            return ToolResult(success=False, output=None, error="Illogical or non-mathematical query.")

        try:
            result = _safe_eval(math_expr)

            if math.isnan(result):
                return ToolResult(success=False, output=None, error="Result is NaN.")
            if math.isinf(result):
                return ToolResult(success=False, output=None, error="Result is infinite (division by zero?).")

            # Format the output if it's a delta question
            if is_delta:
                formatted_result = f"+{result}" if result > 0 else str(result)
                return ToolResult(success=True, output=formatted_result)

            return ToolResult(success=True, output=result)

        except ZeroDivisionError:
            return ToolResult(success=False, output=None, error="Division by zero.")
        except ValueError as exc:
            return ToolResult(success=False, output=None, error=str(exc))
        except Exception as exc:
            return ToolResult(success=False, output=None, error=f"Evaluation error: {exc}")
