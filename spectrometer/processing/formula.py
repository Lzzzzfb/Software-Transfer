"""不使用 eval 的受限光谱表达式解析器。"""

import ast
import operator
from typing import Dict, Mapping

import numpy as np


class FormulaError(ValueError):
    pass


_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_UNARY_OPERATORS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCTIONS = {
    "log10": np.log10,
    "log": np.log,
    "loge": np.log,
    "abs": np.abs,
    "sqrt": np.sqrt,
}
_NAMES = {"I", "Idark", "Ib", "I0", "x"}


def _parse(formula: str) -> ast.Expression:
    if not formula or not formula.strip():
        raise FormulaError("公式不能为空")
    if len(formula) > 512:
        raise FormulaError("公式过长")
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as exc:
        raise FormulaError(f"公式语法错误: {exc.msg}") from exc
    nodes = list(ast.walk(tree))
    if len(nodes) > 100:
        raise FormulaError("公式过于复杂")
    return tree


def validate_formula(formula: str) -> None:
    tree = _parse(formula)
    _validate_node(tree.body)


def _validate_node(node: ast.AST) -> None:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise FormulaError("只允许数字常量")
        return
    if isinstance(node, ast.Name):
        if node.id not in _NAMES:
            raise FormulaError(f"不允许的变量: {node.id}")
        return
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Pow):
            if not isinstance(node.right, ast.Constant) or isinstance(node.right.value, bool):
                raise FormulaError("幂指数必须是 -10 到 10 的数字常量")
            exponent = float(node.right.value)
            if abs(exponent) > 10:
                raise FormulaError("幂指数范围为 -10 到 10")
        elif type(node.op) not in _BINARY_OPERATORS:
            raise FormulaError(f"不允许的运算符: {type(node.op).__name__}")
        _validate_node(node.left)
        _validate_node(node.right)
        return
    if isinstance(node, ast.UnaryOp):
        if type(node.op) not in _UNARY_OPERATORS:
            raise FormulaError(f"不允许的一元运算符: {type(node.op).__name__}")
        _validate_node(node.operand)
        return
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
            raise FormulaError("只允许 log10、log、loge、abs、sqrt 函数")
        if len(node.args) != 1 or node.keywords:
            raise FormulaError("函数必须且只能有一个位置参数")
        _validate_node(node.args[0])
        return
    # Attribute、Subscript、Lambda、比较、推导式等全部在这里拒绝。
    raise FormulaError(f"不允许的表达式: {type(node).__name__}")


def evaluate_formula(formula: str, variables: Mapping[str, np.ndarray]) -> np.ndarray:
    tree = _parse(formula)
    _validate_node(tree.body)

    missing = _NAMES.difference(variables)
    if missing:
        raise FormulaError(f"缺少变量: {', '.join(sorted(missing))}")
    arrays: Dict[str, np.ndarray] = {
        name: np.asarray(variables[name], dtype=np.float64) for name in _NAMES
    }
    shape = arrays["I"].shape
    if len(shape) != 1:
        raise FormulaError("光谱变量必须是一维数组")
    if any(value.shape != shape for value in arrays.values()):
        raise FormulaError("I、Idark、Ib、I0、x 的形状必须一致")

    with np.errstate(all="ignore"):
        value = _evaluate_node(tree.body, arrays)
    result = np.asarray(value, dtype=np.float64)
    if result.ndim == 0:
        result = np.full(shape, float(result), dtype=np.float64)
    if result.shape != shape:
        raise FormulaError("公式结果形状与光谱不一致")
    if not np.all(np.isfinite(result)):
        raise FormulaError("公式结果包含无穷大或无效值，请检查除零和函数定义域")
    return result


def _evaluate_node(node: ast.AST, variables: Mapping[str, np.ndarray]):
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        return variables[node.id]
    if isinstance(node, ast.UnaryOp):
        return _UNARY_OPERATORS[type(node.op)](_evaluate_node(node.operand, variables))
    if isinstance(node, ast.BinOp):
        left = _evaluate_node(node.left, variables)
        right = _evaluate_node(node.right, variables)
        if isinstance(node.op, ast.Pow):
            return np.power(left, right)
        return _BINARY_OPERATORS[type(node.op)](left, right)
    if isinstance(node, ast.Call):
        return _FUNCTIONS[node.func.id](_evaluate_node(node.args[0], variables))
    raise FormulaError(f"无法计算表达式: {type(node).__name__}")
