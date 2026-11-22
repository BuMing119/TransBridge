# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Assistant translation settings and scope preferences."""

from __future__ import annotations

from urllib.parse import urlsplit

from transbridge.application.translation.task_scope import TranslationScope
from transbridge.config.repository import ConfigRepositoryError, validate_llm_endpoint_update
from transbridge.config.translation_preferences import (
    apply_service_preferences,
    apply_term_preferences,
    ordered_term_sources,
    validate_llm_preferences,
)

from .base import ToolResult
from .config_undo_capture import save_config
from .workbench_rows import check_workbench_source


class TranslationSettings:
    def _get_post_process_config(self, llm) -> dict:
        """Read post-processing switches from the same LLM configuration."""
        return {
            "enabled": getattr(llm, "enable_post_process", True),
            "consistency_check": getattr(llm, "pp_enable_consistency_check", True),
            "format_validation": getattr(llm, "pp_enable_format_validation", True),
            "quality_gate": getattr(llm, "pp_enable_quality_gate", True),
            "refinement": getattr(llm, "pp_enable_refinement", True),
            "polish": getattr(llm, "pp_enable_polish", False),
            "arbitration": getattr(llm, "pp_enable_arbitration", True),
        }

    def _get_term_db_info(self, ctx) -> dict:
        """Read the terminology database associated with the current source."""
        from transbridge.ui.dynamic_terminology_source import dynamic_terminology_source, load_source_term_database

        app = getattr(ctx, "app_context", None) or ctx
        check_workbench_source(ctx, app)
        source = dynamic_terminology_source(app)
        if not source.reference and not source.source_key:
            return {"path": None, "entry_count": 0}
        database = load_source_term_database(source.reference, source.source_key, read_only=True)
        return {"path": database.path, "entry_count": len(database.as_list())}

    def get_translation_config(self, args: dict, ctx) -> ToolResult:
        """返回当前 LLM 翻译配置，含后处理、术语、ParaTranz 状态。"""
        from transbridge.paratranz.config_manager import ParatranzConfig

        from ._common import load_llm_config

        if args:
            return ToolResult.fail("查询翻译配置无需参数")
        try:
            llm = load_llm_config()
            host = urlsplit(llm.base_url).hostname if llm.base_url else None
            term_db_info = self._get_term_db_info(ctx)
            pt_cfg = getattr(ctx, "config", None) or ParatranzConfig.load_from_file()
            pt_config = {"token_configured": pt_cfg.credential_configured, "api_url": pt_cfg.base_url}
        except (AttributeError, OSError, RuntimeError, ValueError) as exc:
            return ToolResult.fail(str(exc), error_code="CONFIG_LOAD_FAILED")

        return ToolResult.ok(
            data={
                "provider": llm.provider,
                "model": llm.model,
                "api_key_configured": bool(llm.api_key),
                "temperature": getattr(llm, "temperature", None),
                "max_tokens": llm.max_output_tokens,
                "target_lang": llm.target_lang,
                "term_priority": ordered_term_sources(llm.term_priority),
                "local_json_path": llm.local_json_path or None,
                "local_csv_path": getattr(llm, "local_csv_path", "") or None,
                "local_excel_path": llm.local_excel_path or None,
                "post_process": self._get_post_process_config(llm),
                "term_database": term_db_info,
                "paratranz": pt_config,
                "config_revision": llm.config_revision,
                "base_url_host": host,
            }
        )

    def set_translation_config(self, args: dict, ctx) -> ToolResult:
        """Update one configuration snapshot; endpoint identity is atomic."""

        from transbridge.config.language_profiles import LanguageProfileError, load_language_profile

        from ._common import load_llm_config

        if "target_lang" in args:
            try:
                load_language_profile(args["target_lang"])
            except LanguageProfileError as exc:
                return ToolResult.fail(str(exc))

        mapping = {
            "provider": "provider",
            "base_url": "base_url",
            "model": "model",
            "temperature": "temperature",
            "target_lang": "target_lang",
            "max_tokens": "max_output_tokens",
        }
        if extras := set(args) - mapping.keys():
            return ToolResult.fail(f"未知的翻译配置参数：{', '.join(sorted(extras))}")
        if not args:
            return ToolResult.ok("未做任何修改")
        llm = load_llm_config()
        updates = {mapping[key]: value for key, value in args.items()}
        try:
            validate_llm_endpoint_update(updates)
            apply_service_preferences(llm, updates)
            if error := validate_llm_preferences(llm):
                return ToolResult.fail(error)
            save_config(ctx, llm, list(updates))
        except (ConfigRepositoryError, TypeError, ValueError) as exc:
            return ToolResult.fail(str(exc), error_code=getattr(exc, "code", None))
        return ToolResult.ok(
            f"已更新配置: {', '.join(args)}",
            data={"changed_fields": list(args), "config_revision": llm.config_revision},
        )

    def set_term_config(self, args: dict, ctx) -> ToolResult:
        from ._common import load_llm_config

        mapping = {
            "term_sources": "term_priority",
            "json_path": "local_json_path",
            "csv_path": "local_csv_path",
            "excel_path": "local_excel_path",
        }
        if extras := set(args) - mapping.keys():
            return ToolResult.fail(f"未知的术语配置参数：{', '.join(sorted(extras))}")
        if not args:
            return ToolResult.ok("未修改任何术语配置", data={"unchanged": True})
        llm = load_llm_config()
        updates = {mapping[key]: value for key, value in args.items()}
        try:
            apply_term_preferences(llm, updates)
            if error := validate_llm_preferences(llm):
                return ToolResult.fail(error)
            save_config(ctx, llm, list(updates))
        except (ConfigRepositoryError, TypeError, ValueError) as exc:
            return ToolResult.fail(str(exc), error_code=getattr(exc, "code", None))
        return ToolResult.ok(f"已更新术语配置: {', '.join(args)}", data={"changed": list(args)})

    def set_scope(self, args: dict, ctx) -> ToolResult:
        """设置翻译作用域。E8: 操作 ctx.translation_scope 正式属性（带类型校验）。"""
        scope = {
            "stages": args.get("stages", []),
            "labels": args.get("labels", []),
            "categories": args.get("categories", []),
        }
        try:
            state = TranslationScope.from_filters(args)
            if not (state.stage_filters or state.label_filters or state.category_filters):
                scope["preset"] = "all"
            from .types import ExecutionContext

            if isinstance(ctx, ExecutionContext):

                def update():
                    ctx.app_context.translation_scope = scope
                    return scope, False

                gate = getattr(ctx, "assistant_gate", None)
                ctx.safe_mutate_wait(update if gate is None else lambda: gate.change_translation_scope(ctx, update))
                object.__setattr__(ctx, "translation_scope", scope)
            else:
                ctx.translation_scope = scope
        except (TypeError, ValueError) as exc:
            return ToolResult.fail(str(exc))
        return ToolResult.ok("翻译作用域已更新", data=scope)

    def get_scope_preview(self, args: dict, ctx) -> ToolResult:
        """Preview the same default translation selection used at launch."""
        from .translation_scope import preview_translation_scope

        collection = ctx.collection
        try:
            state, entries = preview_translation_scope(args, ctx, collection, mode=args.get("mode", "translate"))
        except ValueError as exc:
            return ToolResult.fail(str(exc))
        scope = {
            "stages": sorted(state.stage_filters),
            "labels": sorted(state.label_filters),
            "categories": sorted(state.category_filters),
        }
        if state.preset == "selection":
            scope.update(preset="selection", selected_count=len(state.selected_entry_ids))
        elif not any(scope.values()):
            scope["preset"] = "all"
        matched = len(entries)
        total = len(collection) if collection else 0
        return ToolResult.ok(
            f"作用域匹配: {matched}/{total} 条",
            data={"matched": matched, "total": total, "scope": scope},
        )
