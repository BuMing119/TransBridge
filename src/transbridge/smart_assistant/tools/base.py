"""工具系统基础设施：ToolResult, ExecutionContext, HITL 协议, GuardChain, 装饰器。

装饰器推荐顺序：
    @require_collection    ← 最外层（集合前置检查，失败直接返回）
    @validate_params(...)   ← 内层（参数校验，失败直接返回）

示例：
    @require_collection
    @validate_params({"entry_id": {"type": "str", "required": True}})
    def _tool_edit_translation(args: dict, ctx, collection) -> ToolResult:
        ...
"""

from __future__ import annotations

import copy
import functools
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection
    from src.transbridge.converter.translation_entry import TranslationEntry

logger = logging.getLogger(__name__)


# ── ToolResult v2 ──────────────────────────────────────────────

@dataclass
class ToolResult:
    """工具执行结果。所有工具 SHALL 返回此类型。

    v2 变更:
    - success 从三态改为 bool + 独立 partial 字段 (B3)
    - 新增 get()/__getitem__ 字典兼容方法 (B2)
    """
    success: bool
    message: str
    data: dict[str, Any] | None = None
    failed_items: list[dict[str, Any]] | None = None
    truncated: bool = False
    partial: bool = False
    # C10: 错误分类字段
    error_category: str | None = None    # "network" | "auth" | "input" | "permission" | "config" | "internal"
    error_code: str | None = None        # e.g. "API_KEY_MISSING", "TIMEOUT"
    recovery_action: str | None = None   # 建议的恢复操作
    warnings: list[str] | None = None    # 非致命警告

    def to_dict(self) -> dict[str, Any]:
        """转为字典。success 保持为 bool，向后兼容。"""
        result: dict[str, Any] = {"success": self.success, "message": self.message}
        if self.partial:
            result["partial"] = True
        if self.data is not None:
            result["data"] = self.data
        if self.failed_items is not None:
            result["failed_items"] = self.failed_items
        if self.truncated:
            result["truncated"] = self.truncated
        if self.error_category:
            result["error_category"] = self.error_category
        if self.error_code:
            result["error_code"] = self.error_code
        if self.recovery_action:
            result["recovery_action"] = self.recovery_action
        if self.warnings:
            result["warnings"] = self.warnings
        return result

    # B2: 字典兼容方法
    def get(self, key: str, default: Any = None) -> Any:
        if key == "success":
            return self.success
        if key == "message":
            return self.message
        if key == "data":
            return self.data
        if key == "partial":
            return self.partial
        if key == "failed_items":
            return self.failed_items
        if key == "truncated":
            return self.truncated
        if key == "error_category":
            return self.error_category
        if key == "error_code":
            return self.error_code
        if key == "recovery_action":
            return self.recovery_action
        if key == "warnings":
            return self.warnings
        return default

    def __getitem__(self, key: str) -> Any:
        d = self.to_dict()
        return d[key]

    @classmethod
    def ok(cls, message: str = "操作成功", data: dict | None = None,
           warnings: list[str] | None = None) -> "ToolResult":
        return cls(success=True, message=message, data=data, warnings=warnings)

    @classmethod
    def fail(cls, message: str, failed_items: list | None = None, *,
             error_category: str | None = None,
             error_code: str | None = None,
             recovery_action: str | None = None) -> "ToolResult":
        return cls(success=False, message=message, failed_items=failed_items,
                   error_category=error_category, error_code=error_code,
                   recovery_action=recovery_action)

    @classmethod
    def partial_ok(cls, message: str, data: dict | None = None,
                   failed_items: list | None = None) -> "ToolResult":
        """部分成功 —— success=True, partial=True (B3 变更)"""
        return cls(success=True, partial=True, message=message, data=data, failed_items=failed_items)


# ── ExecutionContext (B4 + H9) ──────────────────────────────────

@dataclass
class ExecutionContext:
    """工具执行上下文，包装 AppContext + TaskManager。

    __getattr__ 代理：未命中属性自动转发到内部 AppContext，
    使 v1 工具（接收裸 AppContext）零改动兼容新 ExecutionContext。(H9)
    """
    app_context: Any = None
    task_manager: Any = None

    def __getattr__(self, name: str) -> Any:
        if name.startswith('_'):
            raise AttributeError(name)
        app_ctx = self.__dict__.get('app_context')
        if app_ctx is not None and hasattr(app_ctx, name):
            return getattr(app_ctx, name)
        raise AttributeError(
            f"'{type(self).__name__}' 和 'AppContext' 均无属性 '{name}'"
        )


# ── HITL 协议 (H5) ─────────────────────────────────────────────

class HITLType(Enum):
    CONFIRM = "confirm"
    FILE_SELECT = "file_select"
    COMPARE_CONFIRM = "compare_confirm"


@dataclass
class HITLRequest:
    """人机交互请求。"""
    type: HITLType
    title: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)
    timeout: int | None = None  # E11: None=无限等待，可配置


@dataclass
class HITLResponse:
    """人机交互响应。"""
    approved: bool
    data: dict[str, Any] | None = None


# ── execute_with_guardrails (B6) ────────────────────────────────

def _build_guard_chain() -> list | None:
    """构建标准护栏中间件链。B1: 消除 GUI/MCP 双重路径分歧。

    ExecutionEngine 复用此函数，确保所有工具执行路径使用相同的护栏链。
    返回 None 表示护栏模块不可用。
    """
    try:
        from src.transbridge.smart_assistant.guardrails.permission import PermissionGuard
        from src.transbridge.smart_assistant.guardrails.input_validator import InputValidationGuard
        from src.transbridge.smart_assistant.guardrails.output_validator import OutputValidationGuard
        return [PermissionGuard(), InputValidationGuard(), OutputValidationGuard()]
    except ImportError:
        return None


def execute_with_guardrails(spec, args: dict, ctx: ExecutionContext,
                            middlewares: list | None = None) -> ToolResult:
    """统一工具执行入口，GUI 和 MCP 共享同一条中间件链。

    链: PermissionGuard → InputValidationGuard → 工具执行 → OutputValidationGuard
    B6+B1: 消除 GUI/MCP 安全分叉，支持自定义护栏链。
    middlewares=None 时使用默认链；传入 [] 则跳过所有护栏。
    """
    guards = middlewares if middlewares is not None else _build_guard_chain()
    if guards is None:
        logger.critical("护栏模块导入失败，拒绝执行工具: %s", spec.name)
        return ToolResult.fail("安全护栏不可用，工具执行被拒绝")

    step = {"tool": spec.name, "args": args}

    # 1. Before 中间件链
    for mw in guards:
        guard_result = mw.before_execute(step, ctx)
        if not guard_result.allowed:
            return ToolResult.fail(f"护栏拒绝: {guard_result.reason}")
        if guard_result.modified_args is not None:
            step["args"] = guard_result.modified_args

    # 2. 执行
    raw_result = spec.execute(step.get("args", args), ctx)

    # 3. After 中间件链（逆序 — 洋葱模型）
    if isinstance(raw_result, ToolResult):
        from src.transbridge.smart_assistant.execution_engine import StepResult
        temp_result = StepResult(
            step_id="", tool=spec.name, success=raw_result.success,
            message=raw_result.message, data=raw_result.data, duration_ms=0,
        )
        for mw in reversed(guards):
            guard_result = mw.after_execute(step, temp_result, ctx)
            if not guard_result.allowed:
                return ToolResult.fail(f"输出校验拒绝: {guard_result.reason}")
        return raw_result
    elif isinstance(raw_result, dict):
        return ToolResult(
            success=raw_result.get("success", True),
            message=raw_result.get("message", ""),
            data=raw_result.get("data"),
        )
    return raw_result


# ── 装饰器 ──────────────────────────────────────────────────────

def require_collection(func):
    """M3: 集合前置检查装饰器。统一替换 6 个文件中的手动检查。"""
    import functools

    @functools.wraps(func)
    def wrapper(args: dict, ctx, *a, **kw) -> ToolResult:
        collection = getattr(ctx, 'collection', None)
        if collection is None:
            return ToolResult.fail(
                "当前没有加载翻译集合",
                error_category="input", error_code="COLLECTION_NOT_LOADED",
            )
        return func(args, ctx, *a, **kw)
    return wrapper


# ── filter_entries (H8) ────────────────────────────────────────

def filter_entries(collection: "TranslationEntryCollection",
                    filter_state: dict,
                    entry_labels: dict[str, set[str]] | None = None) -> list["TranslationEntry"]:
    """公共筛选函数。根据 filter_state 从 collection 中筛选条目。

    供 Story 04/08/10 复用，统一筛选行为。(H8)
    entry_labels: entry_id → set[label_name]，用于标签筛选（M1 修复）。
    """
    results = list(collection)

    stages = filter_state.get("stage")
    if stages:
        results = [e for e in results if e.stage in stages]

    categories = filter_state.get("category")
    if categories:
        results = [
            e for e in results
            if e.context and any(
                e.context.startswith(c + ":") or e.context.startswith(c + "_")
                for c in categories
            )
        ]

    labels = filter_state.get("labels") or filter_state.get("label")  # M1: 兼容两种 key 名
    if labels and entry_labels:
        results = [e for e in results if any(
            lbl in entry_labels.get(e.id, set()) for lbl in labels
        )]

    search_query = filter_state.get("search_query")
    search_field = filter_state.get("search_field", "text")
    if search_query:
        q = search_query.lower()
        if search_field == "id":
            results = [e for e in results if q in (e.id or "").lower()]
        elif search_field == "key":
            results = [e for e in results if q in (e.key or "").lower()]
        else:  # "text" or default
            results = [e for e in results if q in (e.original or "").lower()]

    return results


# ── @require_collection ─────────────────────────────────────────

def require_collection(func: Callable) -> Callable:
    """装饰器：从 ctx 提取 collection 并检查有效性。

    装饰后的函数签名: func(args: dict, ctx, collection: TranslationEntryCollection) -> ToolResult
    失败时直接返回 ToolResult(success=False)，不进入函数体。
    """
    @functools.wraps(func)
    def wrapper(args: dict, ctx) -> ToolResult:
        slot = getattr(ctx, 'active_slot', None)
        collection = getattr(slot, 'collection', None) if slot else getattr(ctx, 'collection', None)
        if not collection or len(collection) == 0:
            return ToolResult.fail("当前没有加载翻译集合",
                error_category="input", error_code="COLLECTION_NOT_LOADED")
        return func(args, ctx, collection)
    return wrapper


# ── @validate_params ────────────────────────────────────────────

def validate_params(schema: dict) -> Callable:
    """装饰器工厂：按 ToolSpec.parameters 格式校验 args。

    schema 格式: {"param_name": {"type": "str", "required": True, "description": "..."}}
    校验失败时返回 ToolResult(success=False)，不进入函数体。
    支持类型: str, int, float, bool, list, dict
    """
    _TYPE_MAP = {
        "str": str, "string": str,
        "int": int, "integer": int,
        "float": float, "number": float,
        "bool": bool, "boolean": bool,
        "list": list, "array": list,
        "dict": dict, "object": dict,
    }

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(args: dict, *rest) -> ToolResult:
            if not isinstance(args, dict):
                return ToolResult.fail(f"参数类型错误: 期望 dict，实际 {type(args).__name__}")
            errors = []
            for param_name, param_info in schema.items():
                expected_type_str = param_info.get("type", "str")
                expected_type = _TYPE_MAP.get(expected_type_str)
                required = param_info.get("required", True)

                if param_name not in args or args[param_name] is None:
                    if required:
                        errors.append(f"缺少必需参数: {param_name}")
                    continue

                value = args[param_name]
                if expected_type and not isinstance(value, expected_type):
                    errors.append(
                        f"参数类型错误: {param_name} 期望 {expected_type_str}，实际 {type(value).__name__}"
                    )

            if errors:
                return ToolResult.fail(f"参数校验失败: {'; '.join(errors)}")

            return func(args, *rest)
        return wrapper
    return decorator
