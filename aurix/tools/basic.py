import ast
import math
import operator
from datetime import datetime

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}
_NAMES = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
_NAMES.update(abs=abs, round=round, min=min, max=max)


def _eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 10000:
            raise ValueError("exponent too large")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Name) and node.id in _NAMES:
        return _NAMES[node.id]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _NAMES:
        return _NAMES[node.func.id](*[_eval(a) for a in node.args])
    raise ValueError(f"unsupported expression element: {ast.dump(node)[:60]}")


def calculator(expression: str) -> str:
    try:
        return str(_eval(ast.parse(expression.replace("^", "**"), mode="eval").body))
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"


def get_time() -> str:
    return datetime.now().astimezone().strftime("%A, %d %B %Y, %H:%M %Z")
