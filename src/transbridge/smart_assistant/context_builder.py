# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .tools.workbench_rows import tool_scope_statistics

if TYPE_CHECKING:
    from transbridge.ui.context import AppContext

logger = logging.getLogger(__name__)


class ContextBuilder:
    """构建追加到 system prompt 的当前工作环境上下文信息。

    C1: 移除对 ui/ 的直接依赖。调用方通过构造函数或 build() 参数传入 AppContext。
    """

    def __init__(self, ctx: AppContext | None = None):
        self._ctx = ctx

    def build(self, ctx=None) -> str:
        ctx = ctx or self._ctx
        if ctx is None:
            return "(Not initialized — load a translation collection first)\n"
        collection = ctx.collection

        if collection is None:
            return (
                "Current workspace context:\n"
                "- No collection is loaded\n"
                "- Parse a plugin or import JSON from the File menu first"
            )

        esp_name = Path(ctx.esp_path).stem if ctx.esp_path else "No plugin selected"
        try:
            stats = tool_scope_statistics(ctx)
        except (ValueError, RuntimeError, OSError) as error:
            logger.warning("Could not read current workbench statistics (%s)", type(error).__name__)
            return "Current workspace statistics are unavailable. Refresh the workbench and query get_statistics again."
        summary = stats.summary
        cat_lines = "\n".join(f"  - {cat}: {cnt} entries" for cat, cnt in stats.categories.most_common())

        return (
            f"Current workspace context:\n"
            f"- Plugin: {esp_name}\n"
            f"- Collection summary: {summary.total} total, {summary.completed} translated, "
            f"{summary.untranslated} untranslated, {summary.needs_review} in question, {summary.hidden} hidden\n"
            f"- Category distribution:\n{cat_lines}"
        )
