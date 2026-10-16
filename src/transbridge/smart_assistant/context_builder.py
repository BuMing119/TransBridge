from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from transbridge.ui.context import AppContext


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
        total = len(collection)

        # M21: 单次遍历 — 合并 translated 计数与分类分布统计
        translated = 0
        cat_counter: Counter[str] = Counter()
        for entry in collection:
            if entry.translation:
                translated += 1
            ctx_str = entry.context or ""
            base = ctx_str.split("|")[0] if "|" in ctx_str else ctx_str
            rec = base.split(":")[0]
            if rec in ("INFO", "DIAL"):
                cat_counter["Dialogue"] += 1
            else:
                cat_counter[base] += 1
        untranslated = total - translated

        cat_lines = "\n".join(
            f"  - {cat}: {cnt} entries" for cat, cnt in sorted(cat_counter.items(), key=lambda x: -x[1])
        )

        return (
            f"Current workspace context:\n"
            f"- Plugin: {esp_name}\n"
            f"- Collection summary: {total} total, {translated} translated, {untranslated} untranslated\n"
            f"- Category distribution:\n{cat_lines}"
        )
