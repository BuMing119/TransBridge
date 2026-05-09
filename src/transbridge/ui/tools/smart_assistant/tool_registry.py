"""工具注册表：ToolSpec + ToolRegistry + v1 工具实现。"""
from dataclasses import dataclass
from typing import Callable, Any


@dataclass
class ToolSpec:
    name: str
    display_name: str
    description: str
    parameters: dict
    is_long_running: bool = False
    execute: Callable[[dict, Any], dict] | None = None


class _ToolRegistry:
    """工具注册表（类级别单例）。"""

    _tools: dict[str, ToolSpec] = {}

    @classmethod
    def register(cls, spec: ToolSpec) -> None:
        cls._tools[spec.name] = spec

    @classmethod
    def get(cls, name: str) -> ToolSpec | None:
        return cls._tools.get(name)

    @classmethod
    def list_all(cls) -> list[ToolSpec]:
        return list(cls._tools.values())

    @classmethod
    def build_tool_schema_for_prompt(cls) -> str:
        lines = ["可用工具列表："]
        for tool in cls._tools.values():
            lines.append(f"- {tool.name}: {tool.description}")
            lines.append(f"  参数: {tool.parameters}")
        return "\n".join(lines)


ToolRegistry = _ToolRegistry


# ── v1 工具执行函数 ──────────────────────────────────────────

def _tool_lookup_terms(args: dict, ctx) -> dict:
    """查询术语库中匹配的术语翻译。"""
    keywords = args.get("keywords", [])
    if not keywords:
        return {"success": True, "message": "未提供查询关键词", "data": {}}
    try:
        from src.transbridge.ai_translator.term_database import TermDatabaseManager
        mgr = TermDatabaseManager()
        terms = mgr.match_terms_enhanced(keywords)
        return {"success": True, "message": f"找到 {len(terms)} 个术语", "data": terms}
    except Exception as exc:
        return {"success": False, "message": f"术语查询失败: {exc}"}


def _tool_translate_entries(args: dict, ctx) -> dict:
    """使用 AI 翻译指定或当前选中的词条。"""
    import threading
    collection = ctx.collection
    if not collection or len(collection) == 0:
        return {"success": False, "message": "当前没有加载翻译集合"}
    try:
        from src.transbridge.ai_translator.translator import AutoTranslator, TranslatorConfig
        from src.transbridge.paratranz.config_manager import LLMConfig
        llm_cfg = LLMConfig.load_from_file()
        cfg = TranslatorConfig(llm_config=llm_cfg, esp_path=ctx.esp_path, overwrite=False)
        translator = AutoTranslator(cfg)

        stop_event = threading.Event()

        def _noop_progress(current, total, msg, succ, fail, new_terms):
            pass

        result = translator.translate(
            collection=collection,
            target_entry_ids=None,
            progress_callback=_noop_progress,
            stop_event=stop_event,
        )
        return {
            "success": True,
            "message": f"翻译完成: 成功 {result.success_count}, 失败 {result.failed_count}",
            "data": {
                "success_count": result.success_count,
                "failed_count": result.failed_count,
                "skipped_count": result.skipped_count,
                "new_dynamic_terms": result.new_dynamic_terms,
            },
        }
    except Exception as exc:
        return {"success": False, "message": f"翻译失败: {exc}"}


def _tool_check_quality(args: dict, ctx) -> dict:
    """对当前集合执行翻译质量检查。"""
    collection = ctx.collection
    if not collection or len(collection) == 0:
        return {"success": False, "message": "当前没有加载翻译集合"}
    try:
        from src.transbridge.ai_translator.post_processor.post_processor import (
            PostProcessor, PostProcessorConfig,
        )
        cfg = PostProcessorConfig()
        cfg.enable_refinement = False
        cfg.enable_polish = False
        cfg.enable_llm_arbitration = False
        processor = PostProcessor(cfg)
        report = processor.process(collection)
        return {
            "success": True,
            "message": f"检查完成: {report.total_checked} 条, 问题 {report.issue_count} 处",
            "data": {
                "total_checked": report.total_checked,
                "issue_count": report.issue_count,
                "auto_fixed": report.auto_fixed,
                "needs_review": report.needs_review,
            },
        }
    except Exception as exc:
        return {"success": False, "message": f"质量检查失败: {exc}"}


def _tool_get_collection_summary(args: dict, ctx) -> dict:
    """返回当前翻译集合的统计摘要。"""
    collection = ctx.collection
    if not collection or len(collection) == 0:
        return {"success": True, "message": "当前未加载翻译集合", "data": {"total": 0, "translated": 0}}
    total = len(collection)
    translated = sum(1 for e in collection if e.translation)
    return {
        "success": True,
        "message": f"总计 {total} 条，已翻译 {translated} 条",
        "data": {"total": total, "translated": translated, "untranslated": total - translated},
    }


def _tool_export_json(args: dict, ctx) -> dict:
    """导出当前集合到 JSON 文件。"""
    collection = ctx.collection
    if not collection or len(collection) == 0:
        return {"success": False, "message": "当前没有可导出的集合"}
    try:
        from pathlib import Path
        from src.transbridge.paratranz.config_manager import ParatranzConfig
        data_dir = Path(ParatranzConfig.get_data_dir())
        stem = Path(ctx.esp_path).stem if ctx.esp_path else "export"
        path = data_dir / f"{stem}_export.json"
        collection.to_json_file(str(path))
        return {"success": True, "message": f"已导出到 {path}", "data": {"path": str(path)}}
    except Exception as exc:
        return {"success": False, "message": f"导出失败: {exc}"}


def _tool_write_back(args: dict, ctx) -> dict:
    """写回译文到 ESP/EET/XT 文件。"""
    collection = ctx.collection
    if not collection or len(collection) == 0:
        return {"success": False, "message": "当前没有可写回的集合"}
    slot = ctx.active_slot
    if slot is None:
        return {"success": False, "message": "没有活跃的集合槽位"}
    try:
        from src.transbridge.writer.plugin_writer import PluginWriter
        plugin = slot.plugin
        if plugin is None:
            return {"success": False, "message": "当前槽位无已解析的插件，无法写回"}
        strings_lookup = slot.strings_lookup
        language = slot.strings_lang or "english"
        writer = PluginWriter(plugin, strings_lookup=strings_lookup, language=language)
        count = writer.apply_collection(collection)
        if ctx.esp_path:
            writer.write(ctx.esp_path)
        return {"success": True, "message": f"已写回 {count} 条译文", "data": {"written_count": count}}
    except Exception as exc:
        return {"success": False, "message": f"写回失败: {exc}"}


# ── 启动时自动注册 v1 工具 ──────────────────────────────────

def _register_v1_tools():
    ToolRegistry.register(ToolSpec(
        name="lookup_terms",
        display_name="查询术语",
        description="查询术语库中匹配的术语翻译，用于在翻译前获取标准译名",
        parameters={"keywords": {"type": "list", "description": "要查询的关键词列表"}},
        execute=_tool_lookup_terms,
    ))
    ToolRegistry.register(ToolSpec(
        name="translate_entries",
        display_name="翻译词条",
        description="使用 AI 翻译指定或当前选中的词条",
        parameters={"filter": {"type": "dict", "description": "可选，筛选条件"}},
        is_long_running=True,
        execute=_tool_translate_entries,
    ))
    ToolRegistry.register(ToolSpec(
        name="check_quality",
        display_name="质量检查",
        description="对当前集合执行翻译质量检查，返回问题列表",
        parameters={},
        execute=_tool_check_quality,
    ))
    ToolRegistry.register(ToolSpec(
        name="get_collection_summary",
        display_name="集合概况",
        description="返回当前翻译集合的统计摘要（总数、已翻译数等）",
        parameters={},
        execute=_tool_get_collection_summary,
    ))
    ToolRegistry.register(ToolSpec(
        name="export_json",
        display_name="导出JSON",
        description="导出当前集合到 JSON 文件",
        parameters={},
        execute=_tool_export_json,
    ))
    ToolRegistry.register(ToolSpec(
        name="write_back",
        display_name="写回译文",
        description="将译文写回到 ESP/EET/XT 文件",
        parameters={},
        is_long_running=True,
        execute=_tool_write_back,
    ))


_register_v1_tools()
