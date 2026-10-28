"""Assistant translation settings and scope preferences."""

from __future__ import annotations

import logging

from .base import ToolResult

logger = logging.getLogger(__name__)


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
        term_db_info = {"path": None, "entry_count": 0}
        try:
            import json
            from pathlib import Path

            esp_stem = Path(ctx.esp_path).stem if ctx.esp_path else None
            if esp_stem:
                term_db_path = Path("data") / f"{esp_stem}_terms.json"
                if term_db_path.exists():
                    with open(term_db_path, encoding="utf-8") as f:
                        terms = json.load(f)
                    term_db_info = {
                        "path": str(term_db_path),
                        "entry_count": len(terms) if isinstance(terms, dict) else 0,
                    }
        except Exception as exc:
            logger.warning("术语数据库信息读取失败: %s", exc)
        return term_db_info

    def get_translation_config(self, args: dict, ctx) -> ToolResult:
        """返回当前 LLM 翻译配置，含后处理、术语、ParaTranz 状态。"""
        from ._common import load_llm_config

        llm = load_llm_config()

        # C4: 真实的后处理配置
        post_process = self._get_post_process_config(llm)

        # C4: 术语数据库信息
        term_db_info = self._get_term_db_info(ctx)

        # C5: ParaTranz 配置状态
        pt_config = {"token_configured": False, "api_url": None}
        try:
            from transbridge.paratranz.config_manager import ParatranzConfig

            pt_cfg = ParatranzConfig.load_from_file()
            pt_config = {
                "token_configured": bool(pt_cfg.token),
                "api_url": pt_cfg.base_url,
            }
        except Exception as exc:
            logger.warning("ParaTranz 配置读取失败: %s", exc)

        return ToolResult.ok(
            data={
                "provider": llm.provider,
                "model": llm.model,
                "api_key_configured": bool(llm.api_key),
                "temperature": getattr(llm, "temperature", None),
                "max_tokens": llm.max_output_tokens,
                "target_lang": llm.target_lang,
                "game_profile": llm.game_profile,
                "term_priority": llm.term_priority,
                "local_json_path": llm.local_json_path or None,
                "local_csv_path": getattr(llm, "local_csv_path", "") or None,
                "local_excel_path": llm.local_excel_path or None,
                "post_process": post_process,
                "term_database": term_db_info,
                "paratranz": pt_config,
                "config_revision": llm.config_revision,
                "base_url_host": llm.base_url.split("://")[-1].split("/")[0] if llm.base_url else None,
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

        llm = load_llm_config()

        changed = []
        endpoint_fields = {"provider", "base_url", "model"}
        touched_endpoint = endpoint_fields.intersection(args)
        if touched_endpoint and touched_endpoint != endpoint_fields:
            return ToolResult.fail("provider/base_url/model 必须在同一次调用中完整提供")
        for field_name in ["provider", "base_url", "model", "temperature", "target_lang", "game_profile"]:
            if field_name in args:
                setattr(llm, field_name, args[field_name])
                changed.append(field_name)
        if "max_tokens" in args:
            llm.max_output_tokens = args["max_tokens"]
            changed.append("max_tokens")

        # m5: 检测并记录未知参数
        known_fields = {
            "provider",
            "base_url",
            "model",
            "temperature",
            "max_tokens",
            "target_lang",
            "game_profile",
        }
        unknown = [k for k in args if k not in known_fields]
        if unknown:
            logger.warning("set_translation_config: 已忽略未知参数: %s", unknown)

        if not changed:
            return ToolResult.ok("未做任何修改")

        from .config_undo_capture import save_config

        save_config(ctx, llm, ["max_output_tokens" if key == "max_tokens" else key for key in changed])
        return ToolResult.ok(
            f"已更新配置: {', '.join(changed)}",
            data={"changed_fields": changed, "config_revision": llm.config_revision},
        )

    def set_term_config(self, args: dict, ctx) -> ToolResult:
        """设置术语数据库配置"""
        term_sources = args.get("term_sources")
        json_path = args.get("json_path")
        csv_path = args.get("csv_path")
        excel_path = args.get("excel_path")

        from ._common import load_llm_config

        llm = load_llm_config()
        changed = []

        if term_sources is not None:
            valid = ["dynamic", "paratranz", "json", "csv", "excel"]
            invalid = [s for s in term_sources if s not in valid]
            if invalid:
                return ToolResult.fail(f"无效的术语来源: {invalid}。可选: {valid}")
            llm.term_priority = list(term_sources)
            changed.append(f"term_sources={term_sources}")

        if json_path is not None:
            llm.local_json_path = json_path
            changed.append(f"json_path={json_path}")

        if csv_path is not None:
            llm.local_csv_path = csv_path
            changed.append(f"csv_path={csv_path}")

        if excel_path is not None:
            llm.local_excel_path = excel_path
            changed.append(f"excel_path={excel_path}")

        if not changed:
            return ToolResult.ok("未修改任何术语配置", data={"unchanged": True})

        from .config_undo_capture import save_config

        mapping = {
            "term_sources": "term_priority",
            "json_path": "local_json_path",
            "csv_path": "local_csv_path",
            "excel_path": "local_excel_path",
        }
        save_config(ctx, llm, [target for source, target in mapping.items() if args.get(source) is not None])
        return ToolResult.ok(f"已更新术语配置: {', '.join(changed)}", data={"changed": changed})

    def set_scope(self, args: dict, ctx) -> ToolResult:
        """设置翻译作用域。E8: 操作 ctx.translation_scope 正式属性（带类型校验）。"""
        scope = {
            "stages": args.get("stages", []),
            "labels": args.get("labels", []),
            "categories": args.get("categories", []),
            "action": args.get("action", "include"),
        }
        try:
            ctx.translation_scope = scope
        except (TypeError, ValueError) as exc:
            return ToolResult.fail(str(exc))
        return ToolResult.ok(f"翻译作用域已更新: action={scope['action']}", data=ctx.translation_scope)

    def get_scope_preview(self, args: dict, ctx) -> ToolResult:
        """预览当前作用域下匹配的条目数。m25: 复用 filter_entries 统一筛选逻辑。"""
        from .base import filter_entries

        collection = ctx.collection
        if not collection or len(collection) == 0:
            return ToolResult.ok("当前无翻译集合", data={"matched": 0, "total": 0})
        scope = ctx.translation_scope
        # m25: 将 translation_scope 转为 filter_state 格式后调用 filter_entries
        filter_state = {
            "stage": scope.get("stages"),
            "category": scope.get("categories"),
            "labels": scope.get("labels"),
        }
        entry_labels = getattr(ctx, "entry_labels", None)
        results = filter_entries(collection, filter_state, entry_labels=entry_labels)
        matched = len(results)
        total = len(collection)
        return ToolResult.ok(
            f"作用域匹配: {matched}/{total} 条",
            data={"matched": matched, "total": total, "scope": scope},
        )
