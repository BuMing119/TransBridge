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
import json
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
    # FR7.17: 扩展字段
    pagination: dict[str, Any] | None = None       # {"page": 1, "total_pages": 5, "has_more": true, "total_count": 200}
    execution_meta: dict[str, Any] | None = None   # {"duration_ms": 850, "attempt": 2}
    tool_suggestions: list[str] | None = None      # ["get_visible_entries", "edit_translation"]

    def to_observation(self, tool_name: str, max_chars: int = 2000) -> str:
        """序列化为 LLM 可解析的观察文本。

        格式:
          [OK] tool_name: 人读摘要
            data: {"key": "value", ...}
            pagination: {...}
            meta: {...}
            suggest: tool1, tool2
        """
        if self.partial:
            prefix = "[PARTIAL]"
        elif self.success:
            prefix = "[OK]"
        else:
            prefix = "[FAIL]"
        lines = [f"{prefix} {tool_name}: {self.message or ('完成' if self.success else '失败')}"]
        if self.data:
            data_budget = max(100, int(max_chars * 0.6))
            data_str = self._serialize_data(data_budget)
            if data_str:
                lines.append(f"  data: {data_str}")
        if self.warnings:
            lines.append(f"  warnings: {json.dumps(self.warnings, ensure_ascii=False)}")
        if self.pagination:
            lines.append(f"  pagination: {json.dumps(self.pagination, ensure_ascii=False, default=str)}")
        if self.execution_meta:
            lines.append(f"  meta: {json.dumps(self.execution_meta, ensure_ascii=False, default=str)}")
        if self.tool_suggestions:
            lines.append(f"  suggest: {', '.join(self.tool_suggestions)}")
        if self.failed_items:
            lines.append(f"  failed: {len(self.failed_items)} items")
            if len(self.failed_items) <= 3:
                lines.append(f"  failed_details: {json.dumps(self.failed_items, ensure_ascii=False, default=str)}")
        if self.truncated:
            lines.append("  truncated: true")
        result = "\n".join(lines)
        if len(result) > max_chars:
            keep = max_chars - 30
            cut = result.rfind("\n", 0, keep)
            if cut < max_chars // 2:
                cut = keep
            result = result[:cut] + "\n  ...(truncated)"
        return result

    def _serialize_data(self, max_chars: int) -> str:
        """智能序列化 data 字典。大数据自动摘要（列表→count+sample）。"""
        if not self.data:
            return ""
        try:
            full = json.dumps(self.data, ensure_ascii=False, default=str, separators=(",", ":"))
        except (TypeError, ValueError):
            full = json.dumps(self.data, ensure_ascii=False, default=str)
        if len(full) <= max_chars:
            return full
        # 大数据：智能摘要
        summary: dict[str, Any] = {}
        large_list_keys = {"entries", "projects", "tasks", "collections", "history", "details"}
        for list_key in large_list_keys:
            if list_key in self.data and isinstance(self.data[list_key], list):
                lst = self.data[list_key]
                summary[f"{list_key}_count"] = len(lst)
                if lst:
                    sample = []
                    for item in lst[:2]:
                        if isinstance(item, dict):
                            sample.append({k: (str(v)[:80] if isinstance(v, str) and len(str(v)) > 80 else v)
                                           for k, v in list(item.items())[:5]})
                        else:
                            sample.append(str(item)[:120])
                    summary[f"{list_key}_sample"] = sample
                continue
        for k, v in self.data.items():
            if k in large_list_keys:
                continue
            if isinstance(v, (str, int, float, bool, type(None))):
                if isinstance(v, str) and len(v) > 100:
                    summary[k] = v[:100] + "..."
                else:
                    summary[k] = v
            elif isinstance(v, list):
                summary[f"{k}_count"] = len(v)
                if v and len(v) <= 3:
                    summary[k] = [str(x)[:80] for x in v]
            elif isinstance(v, dict):
                summary[f"{k}_keys"] = list(v.keys())[:10]
                summary[f"{k}_size"] = len(v)
            else:
                summary[k] = str(v)[:100]
        return json.dumps(summary, ensure_ascii=False, default=str, separators=(",", ":"))[:max_chars]

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
        if self.pagination:
            result["pagination"] = self.pagination
        if self.execution_meta:
            result["execution_meta"] = self.execution_meta
        if self.tool_suggestions:
            result["tool_suggestions"] = self.tool_suggestions
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
        if key == "pagination":
            return self.pagination
        if key == "execution_meta":
            return self.execution_meta
        if key == "tool_suggestions":
            return self.tool_suggestions
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


# ── ExecutionContext (B4 + H9 + C10) ─────────────────────────

# C10: 需要通过 AppContext property setter 转发的属性
# 直接赋值 ctx.xxx = yyy 会绕过 property setter 并丢失信号发射，
# 通过 __setattr__ 代理将这些写入重定向到 AppContext.safe_mutate，
# 确保变更在主线程安全执行。
_FORWARDED_ATTRS = frozenset({
    'translation_scope',
    'entry_labels',
    'label_library',
    'filter_state',
})


@dataclass
class ExecutionContext:
    """工具执行上下文，包装 AppContext + TaskManager。

    __getattr__ 代理：未命中属性自动转发到内部 AppContext，
    使 v1 工具（接收裸 AppContext）零改动兼容新 ExecutionContext。(H9)

    C10: __setattr__ 拦截对 translation_scope / entry_labels / label_library
    的赋值，通过 safe_mutate 调度到主线程执行，消除跨线程竞态。

    C10: safe_mutate(fn) 将回调调度到主线程执行。内部设置 _in_dispatch
    标志，使得 safe_mutate 回调内的 ctx.xxx = yyy 写入直接作用到 AppContext
    而非再次排队，避免延迟赋值导致的顺序错误。
    """
    app_context: Any = None
    task_manager: Any = None

    # ── C10: 线程安全状态变更 ──────────────────────────────────

    def safe_mutate(self, fn: Callable[[], None]) -> None:
        """将状态变更回调调度到主线程执行。(C10)

        优先通过 AppContext.safe_mutate（信号队列 + QueuedConnection）调度，
        确保 worker 线程对共享状态的写入不会与 UI 读取产生竞态。
        在非 GUI 上下文中（app_context 未提供 safe_mutate），直接调用 fn。

        同时设置 _in_dispatch 标志，确保函数内部通过 ctx.xxx = yyy
        （__setattr__）写入 _FORWARDED_ATTRS 时，直接作用到 AppContext
        而非再次排队，避免延迟赋值导致的属性未就绪错误。
        """
        app_ctx = self.__dict__.get('app_context')

        def _run() -> None:
            object.__setattr__(self, '_in_dispatch', True)
            try:
                fn()
            finally:
                object.__setattr__(self, '_in_dispatch', False)

        if app_ctx is not None and hasattr(app_ctx, 'safe_mutate'):
            app_ctx.safe_mutate(_run)
        else:
            _run()

    def notify_collection_modified(self) -> None:
        """C10: 在主线程发射 collection_changed 信号，触发 UI 表格刷新。

        应在 safe_mutate 回调中调用，确保信号在主线程发射。
        用于 entry.translation / entry.stage 等 dataclass 字段被修改后
        通知 UI 更新显示。
        """
        app_ctx = self.__dict__.get('app_context')
        if app_ctx is not None and hasattr(app_ctx, 'collection_changed'):
            # 发射 collection_changed 信号，step2 表格监听此信号刷新
            collection = app_ctx.collection if hasattr(app_ctx, 'collection') else None
            app_ctx.collection_changed.emit(collection)

    # ── 属性代理 ────────────────────────────────────────────────

    def __getattr__(self, name: str) -> Any:
        if name.startswith('_'):
            raise AttributeError(name)
        # 使用 object.__getattribute__ 访问 __dict__，避免与 metaclass 冲突
        app_ctx = object.__getattribute__(self, '__dict__').get('app_context')
        if app_ctx is not None and hasattr(app_ctx, name):
            return getattr(app_ctx, name)
        raise AttributeError(
            f"'{type(self).__name__}' 和 'AppContext' 均无属性 '{name}'"
        )

    def __setattr__(self, name: str, value: Any) -> None:
        """C10: 转发对共享 AppContext 属性的写入到 safe_mutate。

        对 _FORWARDED_ATTRS 中的属性，写入通过 safe_mutate 调度到主线程执行，
        避免 worker 线程直接修改 QObject 状态。
        若已在 safe_mutate 派发中（_in_dispatch=True），则直接写入 AppContext，
        避免嵌套排队导致赋值顺序错误。
        对 app_context / task_manager 等自身字段，正常写入实例 __dict__。
        """
        if name in ('app_context', 'task_manager'):
            object.__setattr__(self, name, value)
            return
        app_ctx = self.__dict__.get('app_context')
        if app_ctx is not None and name in _FORWARDED_ATTRS:
            # C10: 已在 safe_mutate 派发中（主线程）→ 直接写入 AppContext
            if self.__dict__.get('_in_dispatch'):
                setattr(app_ctx, name, value)
                return
            # 通过 safe_mutate 调度到主线程执行 property setter
            if hasattr(app_ctx, 'safe_mutate'):
                app_ctx.safe_mutate(lambda n=name, v=value, a=app_ctx: setattr(a, n, v))
                return
        object.__setattr__(self, name, value)


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
            if guard_result.modified_result is not None:
                temp_result.data = guard_result.modified_result
        raw_result.message = temp_result.message
        if temp_result.data is not raw_result.data:
            raw_result.data = temp_result.data
        return raw_result
    elif isinstance(raw_result, dict):
        result = ToolResult(
            success=raw_result.get("success", True),
            message=raw_result.get("message", ""),
            data=raw_result.get("data"),
        )
        # M1: dict 分支也需要走 after 中间件链（脱敏/截断）
        from src.transbridge.smart_assistant.execution_engine import StepResult
        temp_result = StepResult(
            step_id="", tool=spec.name, success=result.success,
            message=result.message, data=result.data, duration_ms=0,
        )
        for mw in reversed(guards):
            guard_result = mw.after_execute(step, temp_result, ctx)
            if not guard_result.allowed:
                return ToolResult.fail(f"输出校验拒绝: {guard_result.reason}")
            if guard_result.modified_result is not None:
                temp_result.data = guard_result.modified_result
        result.message = temp_result.message
        if temp_result.data is not result.data:
            result.data = temp_result.data
        return result
    return raw_result


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
    search_field = filter_state.get("search_field", "original")
    if search_query:
        q = search_query.lower()
        if search_field == "id":
            results = [e for e in results if q in (e.id or "").lower()]
        elif search_field == "key":
            results = [e for e in results if q in (e.key or "").lower()]
        elif search_field in ("original", "text"):
            results = [e for e in results if q in (e.original or "").lower()]
        elif search_field == "translation":
            results = [e for e in results if q in (e.translation or "").lower()]
        elif search_field == "context":
            results = [e for e in results if q in (e.context or "").lower()]
        elif search_field == "all":
            results = [
                e for e in results
                if q in (e.key or "").lower()
                or q in (e.original or "").lower()
                or q in (e.translation or "").lower()
                or q in (e.context or "").lower()
            ]

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


# ── _TYPE_MAP (M48: 移至模块级避免每次调用重建) ──────────────

_TYPE_MAP: dict[str, type] = {
    "str": str, "string": str,
    "int": int, "integer": int,
    "float": float, "number": float,
    "bool": bool, "boolean": bool,
    "list": list, "array": list,
    "dict": dict, "object": dict,
}


# ── @validate_params ────────────────────────────────────────────

def validate_params(schema: dict) -> Callable:
    """装饰器工厂：按 ToolSpec.parameters 格式校验 args。

    schema 格式: {"param_name": {"type": "str", "required": True, "description": "..."}}
    校验失败时返回 ToolResult(success=False)，不进入函数体。
    支持类型: str, int, float, bool, list, dict
    """

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
