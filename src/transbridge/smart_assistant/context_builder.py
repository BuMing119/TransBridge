from pathlib import Path
from collections import Counter


class ContextBuilder:
    """构建追加到 system prompt 的当前工作环境上下文信息。

    C1: 移除对 ui/ 的直接依赖。调用方通过构造函数或 build() 参数传入 AppContext。
    """

    def __init__(self, ctx=None):
        self._ctx = ctx

    def build(self, ctx=None) -> str:
        ctx = ctx or self._ctx
        if ctx is None:
            return "(未初始化 — 请先加载翻译集合)\n"
        collection = ctx.collection

        if collection is None:
            return (
                "当前工作环境:\n"
                "- 未加载任何集合\n"
                "- 请先在文件菜单中解析插件或导入 JSON"
            )

        esp_name = Path(ctx.esp_path).stem if ctx.esp_path else "未选择插件"
        total = len(collection)

        translated = sum(1 for e in collection if e.translation)
        untranslated = total - translated

        # 按 context 分类计数
        cat_counter: Counter[str] = Counter()
        for entry in collection:
            ctx_str = entry.context or ""
            base = ctx_str.split("|")[0] if "|" in ctx_str else ctx_str
            rec = base.split(":")[0]
            if rec in ("INFO", "DIAL"):
                cat_counter["对话"] += 1
            else:
                cat_counter[base] += 1

        cat_lines = "\n".join(
            f"  - {cat}: {cnt} 条"
            for cat, cnt in sorted(cat_counter.items(), key=lambda x: -x[1])
        )

        # 已上传参考文件 — C6: 仅注入摘要信息，不注入原始内容
        docs_lines = ""
        if hasattr(ctx, "_uploaded_docs") and ctx._uploaded_docs:
            docs_lines = "已上传参考文件:\n"
            for name, doc in ctx._uploaded_docs.items():
                char_count = len(doc.raw_text) if hasattr(doc, 'raw_text') else 0
                docs_lines += f"  - {name} ({doc.format}, {char_count}字符) — 使用 search_memory 工具检索内容\n"

        return (
            f"当前工作环境:\n"
            f"- 插件: {esp_name}\n"
            f"- 集合概况: 总计 {total} 条, 已翻译 {translated} 条, 待翻译 {untranslated} 条\n"
            f"- 分类分布:\n{cat_lines}"
            + (f"\n{docs_lines}" if docs_lines else "")
        )
