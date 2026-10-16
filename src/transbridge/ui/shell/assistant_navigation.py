"""Navigate from an assistant result to the same authoritative workbench version."""

from transbridge.ui.project_labels import entry_label_key


def locate_assistant_entries(host, context, entry_ids):
    expected = (context.project_id, context.variant_id)
    current = host.context.active_version_identity
    if not all(expected) or current != expected:
        raise ValueError("请先在工作台打开此任务原来的项目和版本，再定位词条。")
    collection = host.context.collection
    if collection is None or not entry_ids:
        raise ValueError("当前版本没有可定位的词条。")
    wanted = str(entry_ids[0])
    entry = collection.get(wanted)
    if entry is None:
        raise ValueError("这条记录在当前版本中已不存在或标识不唯一，无法安全定位。")
    label = entry_label_key(entry)
    if any(other is not entry and entry_label_key(other) == label for other in collection):
        raise ValueError("当前表格中存在相同显示标识的词条，请通过原文核对，避免定位到另一条记录。")
    host.show_workbench()
    host.workbench.locate_entry(label)
    host.raise_()
    host.activateWindow()


def assistant_navigation(host, open_settings):
    return {
        "open_settings": open_settings,
        "open_project": lambda: host.project_coordinator.open_project(),
        "locate_entries": lambda context, ids: locate_assistant_entries(host, context, ids),
    }
