"""P2 文件解析工具 — 将翻译文件加载到 AppContext (parser namespace)。

Story 12 v2: 权限 write→read(H6), 文件扩展名白名单(E1)。
Story 24: 副作用补全 — action 参数 (create_slot/append) + HITL 确认。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from .base import ToolResult
from .diagnostics import technical_error

if TYPE_CHECKING:
    from transbridge.converter.translation_entry_collection import TranslationEntryCollection

_VALID_EXTENSIONS = {".esp", ".esm", ".esl", ".xml", ".eet", ".json", ".sst"}  # E1 + C4


def _sanitize_error(msg: str, path: str) -> str:
    """M17: 将错误信息中的完整路径替换为文件名，避免泄露路径信息。"""
    return msg.replace(path, os.path.basename(path)) if path and path in msg else msg


def _validate_path(path: str, *, check_exists: bool = True, check_extension: bool = True) -> ToolResult | None:
    """E1/M29: 校验文件路径（扩展名白名单 + 基础安全检查）。

    共享校验逻辑，供 parser 和 writer 复用。
    check_exists=False 用于输出路径（文件可能尚不存在）。
    check_extension=False 用于写回工具（不限制输出扩展名）。
    """
    if not path:
        return ToolResult.fail("文件路径为空")
    if check_exists and not os.path.exists(path):
        return ToolResult.fail(f"文件不存在: {os.path.basename(path)}")  # M17: 仅显示文件名
    if check_extension:
        ext = os.path.splitext(path)[1].lower()
        if ext not in _VALID_EXTENSIONS:
            return ToolResult.fail(f"不支持的文件类型: {ext}，允许: {sorted(_VALID_EXTENSIONS)}")
    # Security authorization is intentionally not duplicated here. The shared
    # InputValidationGuard resolves the canonical path (including symlinks and
    # junctions) and checks it against RuntimeContext.authorized_roots before
    # this business-level existence/extension validation runs.
    return None


# ── Story 24: 副作用辅助函数 ──────────────────────────────────────


def _to_collection(result) -> TranslationEntryCollection:
    """将各类解析器返回值归一化为 TranslationEntryCollection。"""
    TranslationEntryCollection = _collection_type()
    if isinstance(result, TranslationEntryCollection):
        return result
    if hasattr(result, "entries"):
        # ESP PluginParser 返回 plugin 对象，entries 属性为条目列表
        return TranslationEntryCollection(result.entries)
    # 列表/可迭代对象（EET/XT/SST/Strings 解析结果）
    return TranslationEntryCollection(list(result) if result else [])


def _collection_type():
    """延迟加载集合类型，避免 parser 工具注册阶段形成 I/O/legacy 循环导入。"""
    from transbridge.converter.translation_entry_collection import TranslationEntryCollection

    return TranslationEntryCollection


# ── Parser 工具函数工厂 ─────────────────────────────────────────
# M1: 消除 5 个 parser 函数中 95% 相同的代码（action校验/path校验/懒加载导入/解析/slot操作）

_PARSER_DISPATCH = {
    "esp": {
        "format_id": "plugin.sse",
        "label": "ESP 插件",
    },
    "eet": {
        # Legacy tool name retained for compatibility. This selects the
        # producer's independent .eet or XML parser by content, not one format.
        "label": "ESP-ESM Translator 文件",
    },
    "xt": {
        "format_id": "xml.xt",
        "label": "xTranslator XML",
    },
    "sst": {
        "module": "transbridge.parser.xt.sst_parser",
        "class": "SST_Parser",
        "parse_fn": lambda m, path: m().parse(path),
        "label": "xTranslator SST",
    },
    "json": {
        "label": "JSON 导入",
    },
}


def _parse_file(file_type: str, args: dict, ctx) -> ToolResult:
    """统一的文件解析入口。根据 file_type 分派到对应的解析器。

    file_type: "esp" | "eet" | "xt" | "sst" | "json"
    """
    from ._project_tool_mutations import ProjectToolTarget
    from ._source_import import publish_import

    target = ProjectToolTarget.capture(ctx)
    path = args.get("path", "")
    action = args.get("action", "create_slot")
    if action not in ("create_slot", "append"):
        return ToolResult.fail(f"无效的 action 值: {action}，有效值: create_slot, append")
    if not path:
        return ToolResult.fail(f"请提供 {_PARSER_DISPATCH[file_type]['label']} 文件路径")
    err = _validate_path(path)
    if err:
        return err

    from .entry_task_results import EntryTaskResults

    task = EntryTaskResults(
        ctx,
        target.collection,
        kind="source" if action == "create_slot" else "import",
        inputs={"source_name": Path(path).name, "action": action, "file_type": file_type},
    )

    dispatch = _PARSER_DISPATCH[file_type]
    source_snapshot = None
    format_id = None
    options = ()
    try:
        if file_type == "json":
            from ._json_import import parse_json_source

            parsed_json = parse_json_source(path, args)
            collection, source_snapshot, format_id, options = parsed_json
            task.record.update(
                parse_outcome=parsed_json.outcome.value,
                parse_diagnostics=[item.to_dict() for item in parsed_json.diagnostics],
                raw_entries=list(parsed_json.raw_entries),
                parse_unmapped=list(parsed_json.unmapped),
            )
        elif "format_id" in dispatch or file_type in {"sst", "eet"}:
            from transbridge.application.contracts import OperationOutcome, RequestContext
            from transbridge.application.io import (
                FormatId,
                ParseRequest,
                SourceDescriptor,
                TranslationIoUseCase,
            )
            from transbridge.entrypoints.agent import parse_translation_source

            source = Path(path)
            format_id = None if file_type in {"sst", "eet"} else FormatId(dispatch["format_id"])
            request = ParseRequest(
                SourceDescriptor(str(source), source.name, source.stat().st_size),
                RequestContext("smart-assistant-parser"),
                format_id,
                options=(("capture_sst_export_facts", True),)
                if format_id is FormatId.PLUGIN_SSE and action == "create_slot"
                else (),
            )
            if file_type == "eet":
                from transbridge.application.io.eet_source import parse_eet_source

                parsed = parse_eet_source(
                    source, request.context, cancellation=getattr(ctx, "cancellation_token", None)
                )
            elif file_type == "sst":
                from dataclasses import replace

                from transbridge.application.io.sst_adapter import SstAdapter

                content = source.read_bytes()
                format_id = {b"SSU8": FormatId.SST_SSU8, b"SSU9": FormatId.SST_SSU9}.get(content[:4])
                if format_id is None:
                    return ToolResult.fail("SST 文件头无效；仅支持 SSU8 / SSU9。")
                parsed = SstAdapter(format_id).parse_bytes(replace(request, format_hint=format_id), content)
            else:
                parsed = parse_translation_source(TranslationIoUseCase(), request)
            if parsed.outcome not in {OperationOutcome.COMPLETED, OperationOutcome.PARTIAL}:
                message = "; ".join(diagnostic.message for diagnostic in parsed.diagnostics)
                return ToolResult.fail(f"解析 {dispatch['label']} 失败: {message}")
            collection = _to_collection(parsed.entries)
            source_snapshot = parsed.source_snapshot
            format_id = parsed.format_id
            task.record.update(
                parse_outcome=parsed.outcome.value, parse_diagnostics=[item.to_dict() for item in parsed.diagnostics]
            )
        else:
            import importlib

            mod = importlib.import_module(dispatch["module"])
            cls = getattr(mod, dispatch["class"])
            result = cls().parse(path)
            collection = _to_collection(result)
        return publish_import(
            target,
            path,
            Path(path).stem,
            collection,
            source_snapshot,
            format_id,
            action,
            options,
            task=task,
        )
    except Exception as exc:
        return ToolResult.fail(f"解析 {dispatch['label']} 失败: {_sanitize_error(technical_error(exc), path)}")


# ── Parser 工具函数 ──────────────────────────────────────────────


def _tool_parse_esp(args: dict, ctx) -> ToolResult:
    return _parse_file("esp", args, ctx)


def _tool_parse_eet(args: dict, ctx) -> ToolResult:
    return _parse_file("eet", args, ctx)


def _tool_parse_xt(args: dict, ctx) -> ToolResult:
    return _parse_file("xt", args, ctx)


def _tool_parse_sst(args: dict, ctx) -> ToolResult:
    return _parse_file("sst", args, ctx)


def _tool_import_json(args: dict, ctx) -> ToolResult:
    return _parse_file("json", args, ctx)


# ── 注册 ──────────────────────────────────────────────────────

_PARAM_SCHEMAS = {
    "parse_esp": {
        "path": {"type": "str", "required": True, "description": "Path to the ESP/ESM file"},
        "action": {
            "type": "str",
            "required": False,
            "description": (
                "Action after parsing: create_slot (create and activate a new slot; default) or append "
                "(append to the active collection)"
            ),
        },
    },
    "parse_eet": {
        "path": {"type": "str", "required": True, "description": "Path to EET v3 binary or ESP-ESM Translator XML"},
        "action": {
            "type": "str",
            "required": False,
            "description": (
                "Action after parsing: create_slot (create and activate a new slot; default) or append "
                "(append to the active collection)"
            ),
        },
    },
    "parse_xt": {
        "path": {"type": "str", "required": True, "description": "Path to the XT XML file"},
        "action": {
            "type": "str",
            "required": False,
            "description": (
                "Action after parsing: create_slot (create and activate a new slot; default) or append "
                "(append to the active collection)"
            ),
        },
    },
    "parse_sst": {
        "path": {"type": "str", "required": True, "description": "Path to the SST binary file"},
        "action": {
            "type": "str",
            "required": False,
            "description": (
                "Action after parsing: create_slot (create and activate a new slot; default) or append "
                "(append to the active collection)"
            ),
        },
    },
    "import_json": {
        "path": {"type": "str", "required": True, "description": "Path to the JSON file"},
        "format": {
            "type": "str",
            "required": False,
            "description": "auto/paratranz/transbridge/dsd; ambiguous JSON requires an explicit choice",
        },
        "project_id": {
            "type": "int",
            "required": False,
            "description": "Known ParaTranz project ID for remote references; otherwise IDs remain offline-scoped",
        },
        "action": {
            "type": "str",
            "required": False,
            "description": (
                "Action after import: create_slot (create and activate a new slot; default) or append "
                "(append to the active collection)"
            ),
        },
    },
}


# M9: Parser 工具权限为 write 而非 Plan 原定的 read。
# Story 24 增加了 create_slot/append 副作用（修改 AppContext 全局状态），
# 因此权限从 read 提升为 write 是合理的设计变更。
# 参见: plans/agent-tool-expansion/stories/story-24-parser-side-effects.md
def _register_parser_tools():
    from ..tool_registry import ToolRegistry

    ToolRegistry.register_tools(
        "parser",
        [
            {
                "name": "parse_esp",
                "display_name": "解析ESP",
                "description": "①Parse an ESP/ESM/ESL plugin file and extract translatable strings. ②Parameters: path (required, .esp/.esm/.esl), action (optional; create_slot creates and activates a new slot by default, or append adds to the active collection). ③Returns: create_slot→{action,label,entry_count,activated}, append→{action,added_count,total_count,target_label}. Rules: create_slot supports later write_back target=esp/strings inference; append requires has_active_collection; path must be a normalized path within a root authorized by RuntimeContext; use get_app_state to inspect esp_file.",  # noqa: E501
                "execute": _tool_parse_esp,
                "parameters": _PARAM_SCHEMAS.get("parse_esp", {}),
                "permission": "write",
            },
            {
                "name": "parse_eet",
                "display_name": "解析 ESP-ESM Translator 文件",
                "description": "①Parse EET v3 binary (experimental) or ESP-ESM Translator XML (DocumentElement root), detected by content. ②Parameters: path (required), action=create_slot/append. ③Returns source or merge counts. create_slot retains a full snapshot for write_back target=eet; append maps binary EET translations only to unique record/type/original matches in the active collection. Binary writes preserve original metadata and rebuild changed translation numeric caches. Paths must be within RuntimeContext authorized roots.",  # noqa: E501
                "execute": _tool_parse_eet,
                "parameters": _PARAM_SCHEMAS.get("parse_eet", {}),
                "permission": "write",
            },
            {
                "name": "parse_xt",
                "display_name": "解析 xTranslator XML",
                "description": "①Parse an XT XML translation file (xTranslator format for Skyrim mod translation, with an <XT> root element). ②Parameters: path (required, XT XML), action (optional; create_slot by default, or append). ③Returns: create_slot→{action,label,entry_count,activated}, append→{action,added_count,total_count,target_label}. Rules: create_slot supports later write_back target=xt inference; append requires has_active_collection; path must be a normalized path within a root authorized by RuntimeContext; use get_app_state to inspect xt_file.",  # noqa: E501
                "execute": _tool_parse_xt,
                "parameters": _PARAM_SCHEMAS.get("parse_xt", {}),
                "permission": "write",
            },
            {
                "name": "parse_sst",
                "display_name": "解析 xTranslator SST",
                "description": "①Parse SSU8/SSU9 SST dictionaries with strict record boundaries and a complete source snapshot. Both formats contain original and translated text. ②Parameters: path (required, .sst), action (optional; create_slot by default, or append). ③Returns: create_slot→{action,label,entry_count,activated}, append→{action,added_count,total_count,target_label}. Experimental write_back(target=sst) preserves the native format and metadata; old sources without current record identity require re-import. append requires has_active_collection and merges only uniquely proven matches. Paths must be within RuntimeContext authorized roots.",  # noqa: E501
                "execute": _tool_parse_sst,
                "parameters": _PARAM_SCHEMAS.get("parse_sst", {}),
                "permission": "write",
            },
            {
                "name": "import_json",
                "display_name": "导入JSON",
                "description": (
                    "①Import JSON using its explicit identity contract, retaining remote references and extensions. "
                    "②Arguments: path, action=create_slot/append, format=auto/paratranz/transbridge/dsd, "
                    "optional project_id for ParaTranz remote references. Ambiguous JSON requires format. "
                    "③create_slot returns {action,label,entry_count,activated}; append returns counts. "
                    "In V2, create_slot requires a formal source adapter (currently ParaTranz JSON); "
                    "append updates only existing full EntryKeys and cannot add an unregistered source. "
                    "Paths must be within an authorized root."
                ),
                "execute": _tool_import_json,
                "parameters": _PARAM_SCHEMAS.get("import_json", {}),
                "permission": "write",
            },
        ],
    )


_register_parser_tools()
