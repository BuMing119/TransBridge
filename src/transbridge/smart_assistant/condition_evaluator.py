from __future__ import annotations

import ast
import logging

logger = logging.getLogger(__name__)


class ConditionEvaluator:
    """AST 安全条件求值器。仅允许白名单 AST 节点，防止代码注入。

    从 ExecutionEngine 提取，ADR-008 上帝类拆分 Story 01。
    """

    _MAX_EVAL_DEPTH = 20

    # AST 节点类型 → 求值处理器名称
    _AST_DISPATCH = {
        ast.Constant: "_eval_ast_constant",
        ast.Name: "_eval_ast_name",
        ast.Attribute: "_eval_ast_attribute",
        ast.Subscript: "_eval_ast_subscript",
        ast.Compare: "_eval_ast_compare",
        ast.BoolOp: "_eval_ast_boolop",
        ast.UnaryOp: "_eval_ast_unaryop",
        ast.Call: "_eval_ast_call",
    }

    # M4: 预定义安全类型白名单，供 isinstance 使用
    _SAFE_TYPE_WHITELIST = {
        "str": str,
        "int": int,
        "float": float,
        "bool": bool,
        "list": list,
        "dict": dict,
        "tuple": tuple,
        "NoneType": type(None),
    }

    # ── 公开 API ────────────────────────────────────────────────

    def eval_condition(self, condition: str, results: dict) -> bool:
        """AST 安全条件求值。仅允许 result.success / result.data['key'] 等白名单节点。

        空条件（空字符串或 None）→ 返回 False（fail-closed）。
        AST 解析失败 → 返回 False + 日志警告。
        """
        if not condition or not condition.strip():
            return False
        last_result = None
        for r in results.values():
            if r is not None:
                last_result = r
        try:
            tree = ast.parse(str(condition), mode="eval")
            return bool(self._eval_ast_node(tree.body, last_result))
        except Exception:
            logger.warning("条件求值失败: %s", condition, exc_info=True)
            return False

    # ── AST 节点递归求值 ────────────────────────────────────────

    def _eval_ast_node(self, node, result, depth: int = 0) -> object:
        """递归求值 AST 节点。depth 用于防止恶意嵌套导致栈溢出。"""
        if depth > self._MAX_EVAL_DEPTH:
            raise ValueError(f"AST 求值深度超限: {self._MAX_EVAL_DEPTH}")
        allowed_types = (
            ast.Constant,
            ast.Name,
            ast.Attribute,
            ast.Subscript,
            ast.Compare,
            ast.BoolOp,
            ast.UnaryOp,
            ast.Load,
            ast.Index,
            ast.Tuple,
            ast.Call,
            ast.keyword,
        )
        if not isinstance(node, allowed_types):
            raise ValueError(f"不允许的 AST 节点: {type(node).__name__}")
        handler_name = self._AST_DISPATCH.get(type(node))
        if handler_name is None:
            raise ValueError(f"不支持的 AST 节点: {type(node).__name__}")
        return getattr(self, handler_name)(node, result, depth)

    def _eval_ast_constant(self, node, _result, _depth: int) -> object:
        return node.value

    def _eval_ast_name(self, node, result, _depth: int) -> object:
        if node.id == "result":
            return result
        if node.id in ("True", "False"):
            return node.id == "True"
        if node.id == "None":
            return None
        raise ValueError(f"未知变量: {node.id}")

    def _eval_ast_attribute(self, node, result, depth: int) -> object:
        obj = self._eval_ast_node(node.value, result, depth + 1)
        if obj is None:
            return None
        return getattr(obj, node.attr, None)

    def _eval_ast_subscript(self, node, result, depth: int) -> object:
        obj = self._eval_ast_node(node.value, result, depth + 1)
        if isinstance(node.slice, ast.Constant):
            key = node.slice.value
        elif isinstance(node.slice, ast.Index):
            key = self._eval_ast_node(node.slice.value, result, depth + 1)
        else:
            key = None
        if isinstance(obj, dict) and key is not None:
            return obj.get(key)
        return None

    def _eval_ast_compare(self, node, result, depth: int) -> object:
        """C15: 链式比较 a < b < c 在 AST 中是单个 Compare 节点，
        ops=[Lt, Lt], comparators=[b, c]。逐对求值并 AND 短路。"""
        current = self._eval_ast_node(node.left, result, depth + 1)
        for op, comparator in zip(node.ops, node.comparators):
            right = self._eval_ast_node(comparator, result, depth + 1)
            if not self._eval_compare_op(op, current, right):
                return False
            current = right
        return True

    @staticmethod
    def _eval_compare_op(op, left, right) -> bool:
        """求值单个比较操作符。"""
        if isinstance(op, ast.Eq):
            return left == right
        if isinstance(op, ast.NotEq):
            return left != right
        if isinstance(op, ast.Lt):
            return left is not None and right is not None and left < right
        if isinstance(op, ast.LtE):
            return left is not None and right is not None and left <= right
        if isinstance(op, ast.Gt):
            return left is not None and right is not None and left > right
        if isinstance(op, ast.GtE):
            return left is not None and right is not None and left >= right
        if isinstance(op, ast.In):
            return (left in right) if right is not None else False
        if isinstance(op, ast.NotIn):
            return (left not in right) if right is not None else True
        return False

    def _eval_ast_boolop(self, node, result, depth: int) -> object:
        if isinstance(node.op, ast.And):
            for v in node.values:
                if not self._eval_ast_node(v, result, depth + 1):
                    return False
            return True
        if isinstance(node.op, ast.Or):
            for v in node.values:
                if self._eval_ast_node(v, result, depth + 1):
                    return True
            return False
        raise ValueError(f"不支持的布尔操作符: {type(node.op).__name__}")

    def _eval_ast_unaryop(self, node, result, depth: int) -> object:
        operand = self._eval_ast_node(node.operand, result, depth + 1)
        if isinstance(node.op, ast.Not):
            return not operand
        raise ValueError(f"不支持的一元操作符: {type(node.op).__name__}")

    def _eval_ast_call(self, node, result, depth: int) -> object:
        depth_next = depth + 1

        # ── 方法调用：obj.method(args) ──
        if isinstance(node.func, ast.Attribute):
            obj = self._eval_ast_node(node.func.value, result, depth_next)
            method_name = node.func.attr

            if method_name == "get":
                # dict.get(key, default=None)
                default = None
                if node.args:
                    key = self._eval_ast_node(node.args[0], result, depth_next) if len(node.args) > 0 else None
                    default = self._eval_ast_node(node.args[1], result, depth_next) if len(node.args) > 1 else None
                if isinstance(obj, dict) and key is not None:
                    return obj.get(key, default)
                return default

            if method_name in ("startswith", "endswith"):
                # str.startswith(prefix) / str.endswith(suffix)
                if not node.args:
                    raise ValueError(f"{method_name}() 缺少参数")
                arg = self._eval_ast_node(node.args[0], result, depth_next)
                if isinstance(obj, str) and arg is not None:
                    meth = getattr(obj, method_name)
                    return meth(str(arg))
                return False

            raise ValueError(f"不支持的方法调用: .{method_name}()")

        # ── 内置函数调用：func(args) ──
        if isinstance(node.func, ast.Name):
            func_name = node.func.id

            # 单参数安全内置函数
            if func_name in ("len", "str", "int", "float", "bool", "any", "all"):
                if not node.args:
                    raise ValueError(f"{func_name}() 缺少参数")
                arg = self._eval_ast_node(node.args[0], result, depth_next)
                try:
                    if func_name == "len":
                        return len(arg)
                    elif func_name == "str":
                        return str(arg)
                    elif func_name == "int":
                        return int(arg)
                    elif func_name == "float":
                        return float(arg)
                    elif func_name == "bool":
                        return bool(arg)
                    elif func_name in ("any", "all"):
                        if not hasattr(arg, "__iter__"):
                            raise ValueError(f"{func_name}() 参数必须是可迭代对象")
                        return any(arg) if func_name == "any" else all(arg)
                except (TypeError, ValueError):
                    if func_name in ("any", "all"):
                        return False
                    return None

            # isinstance(obj, type) — 仅允许白名单中的类型
            if func_name == "isinstance":
                if len(node.args) < 2:
                    raise ValueError("isinstance() 需要两个参数")
                obj = self._eval_ast_node(node.args[0], result, depth_next)
                allowed_type = self._resolve_isinstance_type(node.args[1])
                if allowed_type is None:
                    raise ValueError(f"isinstance() 不允许的类型: {ast.dump(node.args[1])}")
                return isinstance(obj, allowed_type)

            raise ValueError(f"不支持的函数调用: {func_name}()")

        raise ValueError(f"不支持的函数调用: {ast.dump(node.func)}")

    def _resolve_isinstance_type(self, type_node) -> type | tuple | None:
        """解析 isinstance 的类型参数，仅返回白名单中的类型或其元组。

        支持: Name(id='str'), Constant(value=None), Tuple(elts=[...])
        """
        if isinstance(type_node, ast.Name):
            type_name = type_node.id
            if type_name == "None":
                return type(None)
            return self._SAFE_TYPE_WHITELIST.get(type_name)
        if isinstance(type_node, ast.Constant) and type_node.value is None:
            return type(None)
        if isinstance(type_node, ast.Tuple):
            resolved = []
            for elt in type_node.elts:
                t = None
                if isinstance(elt, ast.Name):
                    t = self._SAFE_TYPE_WHITELIST.get(elt.id)
                elif isinstance(elt, ast.Constant) and elt.value is None:
                    t = type(None)
                if t is None:
                    return None  # 白名单外的类型，拒绝整个元组
                resolved.append(t)
            return tuple(resolved) if resolved else None
        return None
