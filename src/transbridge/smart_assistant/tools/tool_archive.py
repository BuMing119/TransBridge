"""通用文件操作工具 — 归档解包/打包、目录 diff、资源过滤 (archive namespace)。

FR16.1-16.3 的 Agent 工具。纯文件操作，不依赖 collection。
"""

from __future__ import annotations

from .base import ToolResult

# ── 参数 schema ────────────────────────────────────────────────

_PARAM_SCHEMAS = {
    "extract_archive": {
        "archive_path": {"type": "str", "required": True, "description": "Archive path (.7z/.zip/.rar)"},
        "dest_dir": {"type": "str", "required": True, "description": "Destination directory for extraction"},
        "files": {"type": "list", "required": False, "description": "Optional relative paths to extract selectively"},
    },
    "pack_archive": {
        "src_dir": {"type": "str", "required": True, "description": "Source directory to archive"},
        "archive_path": {"type": "str", "required": True, "description": "Output archive path"},
        "fmt": {"type": "str", "required": False, "description": "Archive format: zip (default) or 7z"},
    },
    "diff_directories": {
        "old_dir": {"type": "str", "required": True, "description": "Old-version directory"},
        "new_dir": {"type": "str", "required": True, "description": "New-version directory"},
        "skip_hash_exts": {
            "type": "list",
            "required": False,
            "description": "Extensions to compare without hashing, such as [.bsa]",
        },
    },
    "filter_files": {
        "files": {"type": "list", "required": True, "description": "Relative file paths"},
        "rules_json": {
            "type": "str",
            "required": False,
            "description": "Optional filtering-rules JSON path; built-in rules are used by default",
        },
    },
}


# ── 工具实现 ───────────────────────────────────────────────────


def _tool_extract_archive(args: dict, ctx) -> ToolResult:
    try:
        from transbridge.fileops.archive import extract

        result = extract(
            args["archive_path"],
            args["dest_dir"],
            files=args.get("files"),
        )
        return ToolResult.ok(
            f"已解包 {result['extracted_count']} 个文件到 {args['dest_dir']}",
            data=result,
        )
    except Exception as exc:
        return ToolResult.fail(f"解包失败: {exc}", error_category="internal")


def _tool_pack_archive(args: dict, ctx) -> ToolResult:
    try:
        from transbridge.fileops.archive import pack

        out = pack(
            args["src_dir"],
            args["archive_path"],
            fmt=args.get("fmt", "zip"),
        )
        return ToolResult.ok(f"已打包为 {out}", data={"archive_path": out})
    except Exception as exc:
        return ToolResult.fail(f"打包失败: {exc}", error_category="internal")


def _tool_diff_directories(args: dict, ctx) -> ToolResult:
    try:
        from transbridge.fileops.differ import diff_directories

        skip = set(args.get("skip_hash_exts") or [])
        result = diff_directories(args["old_dir"], args["new_dir"], skip_hash_exts=skip)
        data = result.to_dict()
        s = data["summary"]
        return ToolResult.ok(
            f"diff 完成: 增{s['added']}/删{s['removed']}/变{s['changed']}/不变{s['unchanged']}",
            data=data,
        )
    except Exception as exc:
        return ToolResult.fail(f"diff 失败: {exc}", error_category="internal")


def _tool_filter_files(args: dict, ctx) -> ToolResult:
    try:
        from transbridge.fileops.filter_rules import FilterRules, filter_files

        rules = FilterRules.from_json(args["rules_json"]) if "rules_json" in args else FilterRules()
        kept, stripped = filter_files(args["files"], rules)
        return ToolResult.ok(
            f"过滤完成: 保留{len(kept)}/剔除{len(stripped)}",
            data={"kept": kept, "stripped": stripped},
        )
    except Exception as exc:
        return ToolResult.fail(f"过滤失败: {exc}", error_category="internal")


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
                    "②Arguments: archive_path, dest_dir, and "
                    "optional files for selective extraction. ③Returns {dest_dir, extracted_count}. ④Rule: does not "
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
