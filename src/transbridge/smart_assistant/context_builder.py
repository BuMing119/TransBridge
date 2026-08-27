from __future__ import annotations

from pathlib import Path
from collections import Counter
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from transbridge.ui.context import AppContext


class ContextBuilder:
    """构建追加到 system prompt 的当前工作环境上下文信息。

    C1: 移除对 ui/ 的直接依赖。调用方通过构造函数或 build() 参数传入 AppContext。
    """

    def __init__(self, ctx: "AppContext | None" = None):
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
            f"  - {cat}: {cnt} entries"
            for cat, cnt in sorted(cat_counter.items(), key=lambda x: -x[1])
        )

        # 已上传参考文件 — C6: 仅注入摘要信息，不注入原始内容
        docs_lines = ""
        if hasattr(ctx, "_uploaded_docs") and ctx._uploaded_docs:
            docs_lines = "Uploaded reference files:\n"
            for name, doc in ctx._uploaded_docs.items():
                char_count = len(doc.raw_text) if hasattr(doc, 'raw_text') else 0
                docs_lines += f"  - {name} ({doc.format}, {char_count} characters) — available by reference in the conversation context\n"

        return (
            f"Current workspace context:\n"
            f"- Plugin: {esp_name}\n"
            f"- Collection summary: {total} total, {translated} translated, {untranslated} untranslated\n"
            f"- Category distribution:\n{cat_lines}"
            + (f"\n{docs_lines}" if docs_lines else "")
        )
