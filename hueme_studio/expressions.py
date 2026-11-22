"""Safe, deterministic expression evaluation for ShowDoc clip parameters.

Expressions are plain arithmetic/boolean ASTs.  ``eval``/``exec`` are never
used: only whitelisted node types and functions are accepted, attribute access
and subscripts are rejected, and the result must be a finite number.  The
evaluator is stateless, so scrubbing or rendering offline gives identical
values.
"""

from __future__ import annotations

import ast
import math
import operator
from typing import Any, Callable

TAU = math.tau
MAX_EXPRESSION_LENGTH = 500


def _fract(value: float) -> float:
    return float(value) - math.floor(float(value))


def _noise(*args: float) -> float:
    """Deterministic value noise in 0..1, stable across processes."""
    seed = 0.0
    for index, value in enumerate(args):
        seed += float(value) * (12.9898 + index * 7.233)
    return _fract(math.sin(seed) * 43758.5453123)


def _round(value: float, digits: int = 0) -> float:
    return float(round(float(value), int(digits)))


def _smoothstep(edge0: float, edge1: float, value: float) -> float:
    if abs(float(edge1) - float(edge0)) < 1e-12:
        return 1.0 if float(value) >= float(edge1) else 0.0
    x = max(0.0, min(1.0, (float(value) - float(edge0)) / (float(edge1) - float(edge0))))
    return x * x * (3.0 - 2.0 * x)


def _lerp(a: float, b: float, amount: float) -> float:
    return float(a) + (float(b) - float(a)) * float(amount)


def _step(edge: float, value: float) -> float:
    return 1.0 if float(value) >= float(edge) else 0.0


def _wrap(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    span = float(hi) - float(lo)
    if abs(span) < 1e-12:
        return float(lo)
    return float(lo) + _fract((float(value) - float(lo)) / span) * span


def _pulse(phase: float, width: float = 0.5) -> float:
    return 1.0 if _fract(phase) < max(0.0, min(1.0, float(width))) else 0.0


def _fbm(x: float, y: float, z: float, scale: float = 1.0) -> float:
    total = 0.0
    amplitude = 1.0
    norm = 0.0
    for octave in range(3):
        frequency = (2.0**octave) * float(scale)
        total += amplitude * _noise(
            float(x) * frequency + octave * 17.0,
            float(y) * frequency,
            float(z) * frequency,
        )
        norm += amplitude
        amplitude *= 0.5
    return total / max(1e-9, norm)


def _env_placeholder(name: Any, phase: Any) -> float:
    # Replaced at evaluation time so the function can see the caller's scope.
    raise ExpressionError("env() is only available during evaluation")


FUNCTIONS: dict[str, Callable[..., float]] = {
    "sin": math.sin,
    "cos": math.cos,
    "abs": abs,
    "exp": math.exp,
    "min": min,
    "max": max,
    "floor": math.floor,
    "round": _round,
    "noise": _noise,
    "clamp": lambda value, lo, hi: max(float(lo), min(float(hi), float(value))),
    "smoothstep": _smoothstep,
    "lerp": _lerp,
    "step": _step,
    "wrap": _wrap,
    "pulse": _pulse,
    "env": _env_placeholder,
    "fbm": _fbm,
}

_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.IfExp,
    ast.Call,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.UAdd,
    ast.Not,
    ast.And,
    ast.Or,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
)


class ExpressionError(ValueError):
    pass


def compile_expression(expression: str) -> ast.Expression:
    text = str(expression or "").strip()
    if not text:
        raise ExpressionError("expression is empty")
    if len(text) > MAX_EXPRESSION_LENGTH:
        raise ExpressionError("expression is too long")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"invalid expression: {exc.msg}") from exc
    env_names: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "env"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            env_names.add(id(node.args[0]))
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ExpressionError(
                f"unsupported syntax '{type(node).__name__}' in expression"
            )
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in env_names:
                raise ExpressionError(
                    "string literals are only allowed as the name argument to env()"
                )
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS:
                raise ExpressionError("only whitelisted maths functions are allowed")
            if node.keywords:
                raise ExpressionError("keyword arguments are not supported")
        if isinstance(node, ast.Name):
            continue
    return tree


def expression_names(expression: str) -> set[str]:
    tree = compile_expression(expression)
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    return names - set(FUNCTIONS) - {"TAU", "pi", "true", "false", "none"}


_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY = {ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Not: operator.not_}
_COMPARE = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}


def _eval_node(node: ast.AST, scope: dict[str, Any]) -> Any:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, scope)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, bool)) or node.value is None:
            return node.value
        raise ExpressionError("only numeric constants are allowed")
    if isinstance(node, ast.Name):
        if node.id not in scope:
            raise ExpressionError(f"unknown symbol '{node.id}'")
        return scope[node.id]
    if isinstance(node, ast.BinOp):
        op = _BINARY.get(type(node.op))
        if op is None:
            raise ExpressionError("unsupported binary operator")
        left = _eval_node(node.left, scope)
        right = _eval_node(node.right, scope)
        return op(left, right)
    if isinstance(node, ast.UnaryOp):
        op = _UNARY.get(type(node.op))
        if op is None:
            raise ExpressionError("unsupported unary operator")
        return op(_eval_node(node.operand, scope))
    if isinstance(node, ast.BoolOp):
        values = [_eval_node(value, scope) for value in node.values]
        return all(values) if isinstance(node.op, ast.And) else any(values)
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, scope)
        for op_node, comparator in zip(node.ops, node.comparators):
            op = _COMPARE.get(type(op_node))
            if op is None:
                raise ExpressionError("unsupported comparison")
            right = _eval_node(comparator, scope)
            if not op(left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.IfExp):
        return (
            _eval_node(node.body, scope)
            if _eval_node(node.test, scope)
            else _eval_node(node.orelse, scope)
        )
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS:
            raise ExpressionError("only whitelisted maths functions are allowed")
        if node.func.id == "env":
            if (
                len(node.args) != 2
                or not isinstance(node.args[0], ast.Constant)
                or not isinstance(node.args[0].value, str)
            ):
                raise ExpressionError("env(name, phase) needs a curve name and a phase")
            name = node.args[0].value
            phase = _eval_node(node.args[1], scope)
            value = float(scope.get(name, 0.0))
            return value * math.exp(-_fract(float(phase)) * 3.0)
        args = [_eval_node(arg, scope) for arg in node.args]
        return FUNCTIONS[node.func.id](*args)
    raise ExpressionError(f"unsupported syntax '{type(node).__name__}'")


def evaluate_expression(
    expression: str, context: dict[str, Any] | None = None
) -> float:
    tree = compile_expression(expression)
    scope: dict[str, Any] = {
        "sin": math.sin,
        "cos": math.cos,
        "abs": abs,
        "exp": math.exp,
        "min": min,
        "max": max,
        "floor": math.floor,
        "round": _round,
        "noise": _noise,
        "clamp": FUNCTIONS["clamp"],
        "TAU": TAU,
        "pi": math.pi,
        "true": True,
        "false": False,
        "none": None,
    }
    for key, value in (context or {}).items():
        if isinstance(value, (int, float, bool)) or value is None:
            scope[str(key)] = value
    try:
        result = _eval_node(tree, scope)
    except ExpressionError:
        raise
    except Exception as exc:
        raise ExpressionError(f"expression failed: {type(exc).__name__}: {exc}") from exc
    try:
        number = float(result)
    except (TypeError, ValueError) as exc:
        raise ExpressionError("expression did not return a number") from exc
    if not math.isfinite(number):
        raise ExpressionError("expression returned a non-finite value")
    return number


__all__ = [
    "ExpressionError",
    "FUNCTIONS",
    "TAU",
    "compile_expression",
    "evaluate_expression",
    "expression_names",
]
