# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""User-visible limits, separate from evidence for any particular execution."""

FILE_IMPORT_TOOLS = frozenset({"parse_esp", "parse_xt", "parse_eet", "parse_sst", "import_json"})

_SUPPORTED = frozenset({
    *FILE_IMPORT_TOOLS,
    "merge_task_result",
    "edit_translation",
    "set_stage",
    "start_translation",
    "start_polish",
    "apply_dictionary",
    "manage_entry_labels",
    "set_translation_config",
    "set_term_config",
    "pack_archive",
    "write_back",
    "switch_paratranz_project",
})

_LIMITATIONS = {
    "upload_entries": "文件上传会修改远端 ParaTranz 项目。撤销本轮无法撤回已发送的文件。",
    "export_artifact": "停止本轮会保留已提交的远端导出任务。本轮撤销无法撤回远端导出或恢复下载前的文件。",
    "extract_archive": "本轮撤销尚未覆盖解包生成的文件和目录；停止后这些文件会保留。",
    "stop_task": "已发出的任务取消不能撤回；撤销本轮不会自动重启该任务。",
}


def undo_limitation(tool_name, args=None):
    if tool_name in FILE_IMPORT_TOOLS and args is not None and args.get("action", "create_slot") == "create_slot":
        return "登记成功后来源会保留在工程中，本轮撤销无法移除该来源。"
    if tool_name in _SUPPORTED:
        return ""
    return _LIMITATIONS.get(tool_name, "此操作尚无可安全自动执行的逆操作；停止后会保留其结果，并列入不能恢复的范围。")
