"""User-visible limits, separate from evidence for any particular execution."""

_SUPPORTED = frozenset({
    "edit_translation",
    "set_stage",
    "start_translation",
    "start_polish",
    "run_postprocess",
    "apply_dictionary",
    "manage_entry_labels",
    "set_translation_config",
    "set_term_config",
    "pack_archive",
    "write_back",
    "switch_paratranz_project",
})

_LIMITATIONS = {
    "upload_entries": "远端字符串接口没有条件更新保护；撤销仅可能恢复有凭证的本地修改，不会自动覆盖远端数据。",
    "export_artifact": "远端导出任务不支持撤销；停止本轮不会取消已提交的远端导出。",
    "extract_archive": "本轮撤销尚未覆盖解包生成的文件和目录；停止后这些文件会保留。",
    "stop_task": "已发出的任务取消不能撤回；撤销本轮不会自动重启该任务。",
}


def undo_limitation(tool_name):
    if tool_name in _SUPPORTED:
        return ""
    return _LIMITATIONS.get(tool_name, "此操作尚无可安全自动执行的逆操作；停止后会保留其结果，并列入不能恢复的范围。")
