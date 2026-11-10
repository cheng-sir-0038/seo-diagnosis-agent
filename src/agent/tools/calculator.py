from __future__ import annotations

import ast
import math
import operator
from collections.abc import Callable

from src.agent.tools.base import AgentTool, ToolResult, ToolRuntimeContext

_BINARY: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY: dict[type[ast.unaryop], Callable[[float], float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_FUNCTIONS: dict[str, Callable[..., float]] = {
    "abs": abs,
    "round": round,
    "sqrt": math.sqrt,
}


class SafeExpressionEvaluator:
    def __init__(self, max_length: int = 200, max_nodes: int = 64) -> None:
        self.max_length = max_length
        self.max_nodes = max_nodes

    def evaluate(self, expression: str) -> int | float:
        if not expression or len(expression) > self.max_length:
            raise ValueError("表达式为空或过长")
        tree = ast.parse(expression, mode="eval")
        if sum(1 for _ in ast.walk(tree)) > self.max_nodes:
            raise ValueError("表达式过于复杂")
        value = self._visit(tree.body)
        if not math.isfinite(float(value)):
            raise ValueError("结果不是有限数值")
        if abs(float(value)) > 1e100:
            raise ValueError("结果超出允许范围")
        return int(value) if isinstance(value, float) and value.is_integer() else value

    def _visit(self, node: ast.AST) -> int | float:
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool)
        ):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            left = self._visit(node.left)
            right = self._visit(node.right)
            if isinstance(node.op, ast.Pow) and abs(float(right)) > 100:
                raise ValueError("指数过大")
            return _BINARY[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            return _UNARY[type(node.op)](self._visit(node.operand))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _FUNCTIONS
        ):
            if node.keywords:
                raise ValueError("不支持关键字参数")
            args = [self._visit(arg) for arg in node.args]
            return _FUNCTIONS[node.func.id](*args)
        raise ValueError("表达式包含不允许的语法")


class CalculatorTool(AgentTool):
    name = "calculator"
    description = "安全计算数学表达式，支持四则运算、括号、幂、abs、round 和 sqrt。"
    parameters_schema = {
        "type": "object",
        "properties": {"expression": {"type": "string", "description": "需要计算的数学表达式"}},
        "required": ["expression"],
        "additionalProperties": False,
    }

    def __init__(self) -> None:
        self._evaluator = SafeExpressionEvaluator()

    async def execute(
        self,
        arguments: dict,
        runtime: ToolRuntimeContext | None = None,
    ) -> ToolResult:
        expression = str(arguments.get("expression", "")).strip()
        try:
            result = self._evaluator.evaluate(expression)
        except (SyntaxError, ValueError, ZeroDivisionError, OverflowError, TypeError) as exc:
            return ToolResult(success=False, error=str(exc))
        return ToolResult(success=True, data={"expression": expression, "result": result})
