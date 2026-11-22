# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""通用文件操作工具 — 归档解包/打包、目录 diff、资源过滤 (archive namespace)。

FR16.1-16.3 的 Agent 工具。纯文件操作，不依赖 collection。
"""

from __future__ import annotations

from transbridge.application.assistant_requests.models import RequestError
from transbridge.fileops.archive import ArchiveCancelledError
from transbridge.fomod.xml_fidelity import FomodXmlError

from .base import ToolResult
from .diagnostics import technical_error
from .file_input_access import authorized_input_path
from .file_undo_capture import authorize_file_outputs, capture_file_command

# ── 参数 schema ────────────────────────────────────────────────

_PARAM_SCHEMAS = {
    "extract_archive": {
        "archive_path": {"type": "str", "required": True, "description": "Archive path (.7z/.zip/.rar)"},
        "dest_dir": {"type": "str", "required": True, "description": "Destination directory for extraction"},
    },
    "pack_archive": {
        "src_dir": {"type": "str", "required": True, "description": "Source directory to archive"},
        "archive_path": {"type": "str", "required": True, "description": "Output archive path"},
        "fmt": {
            "type": "str",
            "required": False,
            "enum": ["zip", "7z"],
            "default": "zip",
            "description": "Archive format",
        },
    },
    "diff_directories": {
        "old_dir": {"type": "str", "required": True, "description": "Old-version directory"},
        "new_dir": {"type": "str", "required": True, "description": "New-version directory"},
        "skip_hash_exts": {
            "type": "list",
            "items": "str",
            "required": False,
            "description": "Asset extensions flagged for hash revalidation; defaults to the desktop filter rules.",
        },
    },
    "filter_files": {
        "files": {"type": "list", "required": True, "description": "Relative file paths"},
        "fomod_dir": {
            "type": "str",
            "required": False,
            "description": "Extracted FOMOD root. Supply it for package previews to read XML image references.",
        },
        "rules_json": {
            "type": "str",
            "required": False,
            "description": "Optional filtering-rules JSON path; built-in rules are used by default",
        },
    },
}


# ── 工具实现 ───────────────────────────────────────────────────


def _authorized_directory(ctx, value):
    root = authorized_input_path(ctx, value)
    if not root.is_dir():
        raise RequestError("DIRECTORY_REQUIRED", "请选择目录。")
    # A package/diff reads descendants. Recheck linked members against the same grant.
    for directory, folders, files in root.walk():
        for name in (*folders, *files):
            path = directory / name
            if path.is_symlink() or path.is_junction():
                authorized_input_path(ctx, path)
    return root


def _tool_extract_archive(args: dict, ctx) -> ToolResult:
    try:
        from transbridge.fileops.archive import ArchiveExtractor

        if "files" in args:
            raise RequestError("ARGUMENT_SCHEMA_INVALID", "桌面解包按整份归档执行。")
        archive = authorized_input_path(ctx, args["archive_path"])
        destination = authorize_file_outputs(ctx, [args["dest_dir"]])[0]
        result = ArchiveExtractor().extract(archive, destination, cancellation=getattr(ctx, "cancellation_token", None))
        return ToolResult.ok(
            f"已解包 {result.extracted_count} 个文件到 {result.dest_dir}；本次解包尚未提供自动撤销凭证。",
            data={
                "dest_dir": result.dest_dir,
                "extracted_count": result.extracted_count,
                "undo_limitation": "解包生成目录尚未纳入本轮自动撤销",
            },
        )
    except RequestError as exc:
        return ToolResult.fail(str(exc), error_code=exc.code)
    except ArchiveCancelledError as exc:
        return ToolResult.fail("解包已取消。", error_code=exc.code)
    except Exception as exc:
        return ToolResult.fail(f"解包失败: {technical_error(exc)}", error_category="internal")


def _tool_pack_archive(args: dict, ctx) -> ToolResult:
    try:
        from transbridge.fileops.archive import pack

        source = _authorized_directory(ctx, args["src_dir"])
        target = authorize_file_outputs(ctx, [args["archive_path"]])[0]
        out = capture_file_command(
            ctx,
            [target],
            lambda: pack(
                str(source),
                str(target),
                fmt=args.get("fmt", "zip"),
                cancellation=getattr(ctx, "cancellation_token", None),
            ),
        )
        return ToolResult.ok(f"已打包为 {out}", data={"archive_path": out})
    except RequestError as exc:
        return ToolResult.fail(str(exc), error_code=exc.code)
    except ArchiveCancelledError as exc:
        return ToolResult.fail("打包已取消。", error_code=exc.code)
    except Exception as exc:
        return ToolResult.fail(f"打包失败: {technical_error(exc)}", error_category="internal")


def _tool_diff_directories(args: dict, ctx) -> ToolResult:
    try:
        from transbridge.fileops.differ import diff_directories, normalize_root
        from transbridge.fileops.filter_rules import FilterRules

        old_dir = _authorized_directory(ctx, normalize_root(authorized_input_path(ctx, args["old_dir"])))
        new_dir = _authorized_directory(ctx, normalize_root(authorized_input_path(ctx, args["new_dir"])))
        skip = set(args["skip_hash_exts"]) if "skip_hash_exts" in args else FilterRules().strip_exts
        result = diff_directories(str(old_dir), str(new_dir), skip_hash_exts=skip)
        data = result.to_dict()
        s = data["summary"]
        return ToolResult.ok(
            f"diff 完成: 增{s['added']}/删{s['removed']}/变{s['changed']}/不变{s['unchanged']}",
            data=data,
        )
    except RequestError as exc:
        return ToolResult.fail(str(exc), error_code=exc.code)
    except Exception as exc:
        return ToolResult.fail(f"diff 失败: {technical_error(exc)}", error_category="internal")


def _tool_filter_files(args: dict, ctx) -> ToolResult:
    try:
        from transbridge.fileops.filter_rules import FilterRules, filter_files
        from transbridge.fomod.xml_fidelity import find_fomod_xml_files, parse_fomod_xml

        rules = (
            FilterRules.from_json(authorized_input_path(ctx, args["rules_json"]))
            if "rules_json" in args
            else FilterRules()
        )
        references = []
        if "fomod_dir" in args:
            root = authorized_input_path(ctx, args["fomod_dir"])
            if not root.is_dir():
                raise RequestError("FOMOD_DIRECTORY_REQUIRED", "请选择已解包的 FOMOD 目录。")
            for path in find_fomod_xml_files(root):
                xml = authorized_input_path(ctx, path)
                references.extend(parse_fomod_xml(xml.read_bytes()).resource_references)
        kept, stripped = filter_files(args["files"], rules, references=references)
        return ToolResult.ok(
            f"过滤完成: 保留{len(kept)}/剔除{len(stripped)}",
            data={"kept": kept, "stripped": stripped},
        )
    except (RequestError, FomodXmlError) as exc:
        return ToolResult.fail(str(exc), error_code=exc.code)
    except Exception as exc:
        return ToolResult.fail(f"过滤失败: {technical_error(exc)}", error_category="internal")


# ── 注册 ───────────────────────────────────────────────────────


def _register_archive_tools():
    from ..tool_registry import ToolRegistry

    ToolRegistry.register_tools(
        "archive",
        [
            {
                "name": "extract_archive",
                "display_name": "解包归档",
                "description": (
                    "①Extract a 7z/zip/rar archive to a destination directory. "
                    "②Arguments: archive_path, dest_dir. ③Returns {dest_dir, extracted_count}. ④Rule: does not "
                    "depend on a user-installed 7-Zip."
                ),
                "execute": _tool_extract_archive,
                "permission": "write",
                "parameters": _PARAM_SCHEMAS["extract_archive"],
            },
            {
                "name": "pack_archive",
                "display_name": "打包目录",
                "description": (
                    "①Pack a directory as a zip or 7z archive. "
                    "②Arguments: src_dir, archive_path, fmt=zip (default) or 7z. "
                    "③Returns {archive_path}. ④Rule: rar output is not supported."
                ),
                "execute": _tool_pack_archive,
                "permission": "write",
                "parameters": _PARAM_SCHEMAS["pack_archive"],
            },
            {
                "name": "diff_directories",
                "display_name": "目录差异对比",
                "description": (
                    "①Compare old and new directory manifests. "
                    "②Arguments: old_dir, new_dir, and optional skip_hash_exts. "
                    "③Returns {added,removed,changed,unchanged}. "
                    "④Rule: aligns relative paths and compares content hashes."
                ),
                "execute": _tool_diff_directories,
                "permission": "read",
                "parameters": _PARAM_SCHEMAS["diff_directories"],
            },
            {
                "name": "filter_files",
                "display_name": "资源过滤",
                "description": (
                    "①Classify files as kept or stripped using extension rules. "
                    "②Arguments: files (relative paths) and optional rules_json. "
                    "For FOMOD package previews, supply fomod_dir to preserve XML-referenced resources. "
                    "③Returns {kept,stripped}. "
                    "④Rule: directory-level rules take precedence over global "
                    "rules."
                ),
                "execute": _tool_filter_files,
                "permission": "read",
                "parameters": _PARAM_SCHEMAS["filter_files"],
            },
        ],
    )


_register_archive_tools()
