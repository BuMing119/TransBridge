"""Story 01: Token 精确测量脚本 — 测量 system prompt 各段 token 数。

使用 tiktoken cl100k_base 编码（DeepSeek-v4 近似 tokenizer）。
"""
import sys
import io
sys.path.insert(0, "src")
sys.path.insert(0, ".")  # 部分模块使用 from src.transbridge.xxx 导入

# Fix encoding for Windows console
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import tiktoken
enc = tiktoken.get_encoding("cl100k_base")


def count(text: str) -> int:
    return len(enc.encode(text))


def main():
    # 触发全部工具注册（模块导入时自动执行 _register_xxx_tools()）
    # NOTE: 工具模块内部使用 from src.transbridge... 导入，因此需要保持一致使用 src. 前缀
    import src.transbridge.smart_assistant.tools.tool_default  # noqa
    import src.transbridge.smart_assistant.tools.tool_editor  # noqa
    import src.transbridge.smart_assistant.tools.tool_translator  # noqa
    import src.transbridge.smart_assistant.tools.tool_parser  # noqa
    import src.transbridge.smart_assistant.tools.tool_proofreader  # noqa
    import src.transbridge.smart_assistant.tools.tool_paratranz  # noqa
    import src.transbridge.smart_assistant.tools.tool_writer  # noqa

    # 使用 src.transbridge.xxx 一致的导入路径获取 ToolRegistry
    from src.transbridge.smart_assistant.tool_registry import ToolRegistry
    from src.transbridge.smart_assistant.prompts import (
        HYBRID_SYSTEM_PROMPT,
        _build_preloaded_tools,
        _build_get_tool_help_schema,
        _build_routing_table,
    )

    # Debug
    all_tools = ToolRegistry.list_all()
    print(f"[DEBUG] Total tools registered: {len(all_tools)}")
    all_ns = ToolRegistry.list_all_namespaces()
    for ns in sorted(all_ns.keys()):
        tools = all_ns[ns]
        print(f"[DEBUG]   ns={ns}: {len(tools)} tools")
    if len(all_tools) == 0:
        print("[DEBUG] WARNING: No tools registered! Import errors may have occurred.")
    print()

    # ============================================================
    # 1. 各段测量
    # ============================================================
    template_tokens = count(HYBRID_SYSTEM_PROMPT)

    preloaded = _build_preloaded_tools()
    preloaded_tokens = count(preloaded)

    help_schema = _build_get_tool_help_schema()
    help_schema_tokens = count(help_schema)

    routing = _build_routing_table()
    routing_tokens = count(routing)

    directory = ToolRegistry.build_tool_directory()
    directory_tokens = count(directory)

    tools_section = f"{preloaded}\n\n{help_schema}\n\n{routing}\n\n{directory}"
    tools_total = count(tools_section)

    # Full system prompt (no context)
    full_prompt = HYBRID_SYSTEM_PROMPT.format(context="", tools_desc=tools_section)
    full_tokens = count(full_prompt)

    # With typical context
    ctx = "当前加载的文件: test.esp (85 条目)\n当前项目: MyProject"
    full_with_ctx = HYBRID_SYSTEM_PROMPT.format(context=ctx, tools_desc=tools_section)
    full_with_ctx_tokens = count(full_with_ctx)
    ctx_tokens = full_with_ctx_tokens - full_tokens

    # ============================================================
    # 2. 工具段细拆：每个工具 Schema token 排名
    # ============================================================
    all_tools = ToolRegistry.list_all()
    tool_token_list = []
    for spec in all_tools:
        schema_text = ToolRegistry._format_tool_schema(spec)
        t = count(schema_text)
        tool_token_list.append((spec.name, t, spec.summary or spec.description[:60]))

    tool_token_list.sort(key=lambda x: -x[1])

    # ============================================================
    # 3. 全量 Schema 对比（如果是旧方案）
    # ============================================================
    full_schema_lines = ["可用工具列表："]
    for spec in sorted(all_tools, key=lambda s: s.name):
        full_schema_lines.append(f"- {spec.name}: {spec.description}")
        full_schema_lines.append(f"  参数: {spec.parameters}")
    full_schema_text = "\n".join(full_schema_lines)
    full_schema_tokens = count(full_schema_text)

    # Full prompt with OLD approach (full schema)
    old_prompt = HYBRID_SYSTEM_PROMPT.format(context=ctx, tools_desc=full_schema_text)
    old_full_tokens = count(old_prompt)

    savings = old_full_tokens - full_with_ctx_tokens
    savings_pct = (savings / old_full_tokens * 100) if old_full_tokens > 0 else 0

    # ============================================================
    # 4. 输出报告
    # ============================================================
    print("=" * 70)
    print("Token 测量报告 — tool-prompt-layering Story 01 (Phase 0)")
    print("=" * 70)
    print(f"Tokenizer: tiktoken cl100k_base (DeepSeek-v4 近似)")
    print(f"测量日期: 2026-08-05")
    print()

    print("--- 1. System Prompt 各段 Token ---")
    print(f"{'段名':<25} {'Token 数':>8} {'占比':>8}")
    print("-" * 45)
    print(f"{'Template (正文)':<25} {template_tokens:>8} {template_tokens/full_with_ctx_tokens*100:>7.1f}%")
    print(f"{'Context (上下文)':<25} {ctx_tokens:>8} {ctx_tokens/full_with_ctx_tokens*100:>7.1f}%")
    print(f"{'Tools section':<25} {tools_total:>8} {tools_total/full_with_ctx_tokens*100:>7.1f}%")
    print(f"{'TOTAL (with context)':<25} {full_with_ctx_tokens:>8}")
    print(f"{'TOTAL (no context)':<25} {full_tokens:>8}")
    print()

    print("--- 2. 工具段细分 ---")
    print(f"{'子段':<30} {'Token 数':>8}")
    print("-" * 45)
    print(f"{'Preloaded (2 tools schema)':<30} {preloaded_tokens:>8}")
    print(f"{'get_tool_help schema':<30} {help_schema_tokens:>8}")
    print(f"{'Routing table':<30} {routing_tokens:>8}")
    print(f"{'Tool directory':<30} {directory_tokens:>8}")
    print(f"{'Tools section TOTAL':<30} {tools_total:>8}")
    print()

    print("--- 3. 各工具 Schema Token (top 20) ---")
    print(f"{'工具名':<30} {'Schema Tokens':>14} {'摘要':<50}")
    print("-" * 95)
    for name, t, summary in tool_token_list[:20]:
        print(f"{name:<30} {t:>14} {summary:<50}")
    print()

    print("--- 4. 工具 Schema Token 统计 ---")
    all_counts = [t for _, t, _ in tool_token_list]
    print(f"工具总数: {len(all_counts)}")
    print(f"Schema tokens 总计: {sum(all_counts)}")
    print(f"Schema tokens 均值: {sum(all_counts)/len(all_counts):.1f}" if all_counts else "Schema tokens 均值: N/A")
    print(f"Schema tokens 中位数: {sorted(all_counts)[len(all_counts)//2]}" if all_counts else "Schema tokens 中位数: N/A")
    print(f"Schema tokens 最大: {max(all_counts)} ({tool_token_list[0][0]})" if all_counts else "Schema tokens 最大: N/A")
    print(f"Schema tokens 最小: {min(all_counts)} ({tool_token_list[-1][0]})" if all_counts else "Schema tokens 最小: N/A")
    print()

    print("--- 5. 分层 vs 全量 对比 ---")
    print(f"全量 Schema 方式 token 数: {old_full_tokens}")
    print(f"分层加载方式 token 数:   {full_with_ctx_tokens}")
    print(f"节省 token:              {savings} ({savings_pct:.1f}%)")

    print()
    print("--- 6. 各 Namespace 工具统计 ---")
    all_ns = ToolRegistry.list_all_namespaces()
    for ns in sorted(all_ns.keys()):
        tools = [t for t in all_ns[ns] if not t.deprecated]
        ns_tokens = sum(count(ToolRegistry._format_tool_schema(t)) for t in tools)
        print(f"  {ns:<20}: {len(tools):>2} 工具, Schema 合计 {ns_tokens:>6} tokens")

    # ============================================================
    # 5. Baseline 数据 (为 Phase 4 使用)
    # ============================================================
    print()
    print("--- 7. Phase 4 Baseline ---")
    print(f"预加载工具数: 2 (get_app_state, get_statistics)")
    print(f"预加载 tokens: {preloaded_tokens}")
    print(f"路由表行数: 7")
    print(f"路由表 tokens: {routing_tokens}")
    print(f"工具目录 tokens: {directory_tokens}")
    print(f"get_tool_help Schema tokens: {help_schema_tokens}")
    print(f"总 namespace 数: {len(all_ns)}")
    total_active = sum(1 for s in all_tools if not s.deprecated)
    total_deprecated = sum(1 for s in all_tools if s.deprecated)
    print(f"活跃工具: {total_active}, 废弃工具: {total_deprecated}")


if __name__ == "__main__":
    main()
