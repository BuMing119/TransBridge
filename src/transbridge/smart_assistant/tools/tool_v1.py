"""v1 工具函数 — 从 tool_registry.py 迁移，返回格式升级为 ToolResult。"""
import threading
import logging

from .base import ToolResult

logger = logging.getLogger(__name__)


def _tool_lookup_terms(args: dict, ctx) -> ToolResult:
    """@deprecated: 请使用 search_terms。查询术语库中匹配的术语翻译。"""
    keywords = args.get("keywords", [])
    if not keywords:
        return ToolResult.ok("未提供查询关键词", data={})
    try:
        from src.transbridge.ai_translator.term_database import TermDatabaseManager
        mgr = TermDatabaseManager()
        terms = mgr.match_terms_enhanced(keywords)
        return ToolResult.ok(f"找到 {len(terms)} 个术语", data=terms)
    except Exception as exc:
        return ToolResult.fail(f"术语查询失败: {exc}")


def _tool_translate_entries(args: dict, ctx) -> ToolResult:
    """@deprecated: 请使用 start_translation。使用 AI 翻译指定或当前选中的词条。"""
    collection = ctx.collection
    if not collection or len(collection) == 0:
        return ToolResult.fail("当前没有加载翻译集合")
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
        return ToolResult.ok(
            f"翻译完成: 成功 {result.success_count}, 失败 {result.failed_count}",
            data={
                "success_count": result.success_count,
                "failed_count": result.failed_count,
                "skipped_count": result.skipped_count,
                "new_dynamic_terms": result.new_dynamic_terms,
            },
        )
    except Exception as exc:
        return ToolResult.fail(f"翻译失败: {exc}")


def _tool_check_quality(args: dict, ctx) -> ToolResult:
    """@deprecated: 请使用 check_consistency / validate_format。对当前集合执行翻译质量检查。"""
    collection = ctx.collection
    if not collection or len(collection) == 0:
        return ToolResult.fail("当前没有加载翻译集合")
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
        return ToolResult.ok(
            f"检查完成: {report.total_checked} 条, 问题 {report.issue_count} 处",
            data={
                "total_checked": report.total_checked,
                "issue_count": report.issue_count,
                "auto_fixed": report.auto_fixed,
                "needs_review": report.needs_review,
            },
        )
    except Exception as exc:
        return ToolResult.fail(f"质量检查失败: {exc}")


def _tool_export_json(args: dict, ctx) -> ToolResult:
    """@deprecated: 请使用 export_collection_json。导出当前集合到 JSON 文件。"""
    collection = ctx.collection
    if not collection or len(collection) == 0:
        return ToolResult.fail("当前没有可导出的集合")
    try:
        from pathlib import Path
        from src.transbridge.paratranz.config_manager import ParatranzConfig
        data_dir = Path(ParatranzConfig.get_data_dir())
        stem = Path(ctx.esp_path).stem if ctx.esp_path else "export"
        out_path = args.get("output_path")
        if out_path:
            path = Path(out_path)
            # C8: 路径安全校验
            from src.transbridge.smart_assistant.tools.tool_writer import _validate_output_path
            err = _validate_output_path(str(path))
            if err:
                return err
        else:
            path = data_dir / f"{stem}_export.json"
            # m31: 对默认路径也进行安全校验，防止 stem 含路径遍历字符
            from src.transbridge.smart_assistant.tools.tool_writer import _validate_output_path
            err = _validate_output_path(str(path))
            if err:
                return err
        collection.to_json_file(str(path))
        return ToolResult.ok(f"已导出到 {path}", data={"path": str(path)})
    except Exception as exc:
        return ToolResult.fail(f"导出失败: {exc}")


def _tool_write_back(args: dict, ctx) -> ToolResult:
    """@deprecated: 请使用 write_to_esp / write_to_eet / write_to_xt。写回译文到 ESP/EET/XT 文件。"""
    collection = ctx.collection
    if not collection or len(collection) == 0:
        return ToolResult.fail("当前没有可写回的集合")
    slot = ctx.active_slot
    if slot is None:
        return ToolResult.fail("没有活跃的集合槽位")
    try:
        from src.transbridge.writer.plugin_writer import PluginWriter
        plugin = slot.plugin
        if plugin is None:
            return ToolResult.fail("当前槽位无已解析的插件，无法写回")
        # C8: 路径安全校验
        target = args.get("target_path") or ctx.esp_path
        if target:
            from src.transbridge.smart_assistant.tools.tool_writer import _validate_output_path
            err = _validate_output_path(str(target))
            if err:
                return err
            allowed = {".esp", ".esm", ".esl", ".xml", ".strings"}
            if not any(str(target).lower().endswith(ext) for ext in allowed):
                return ToolResult.fail(f"不允许的文件扩展名，仅支持: {', '.join(allowed)}")
        strings_lookup = slot.strings_lookup
        language = slot.strings_lang or "english"
        writer = PluginWriter(plugin, strings_lookup=strings_lookup, language=language)
        count = writer.apply_collection(collection)
        if target:
            writer.write(target)
        return ToolResult.ok(f"已写回 {count} 条译文", data={"written_count": count})
    except Exception as exc:
        return ToolResult.fail(f"写回失败: {exc}")
