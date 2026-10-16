"""Apply monitor commands only to visible jobs still owned by this session."""

from transbridge.smart_assistant.tools.task_control import task_scope_matches


def apply_monitor_action(manager, monitor, scope, task_id, action):
    visible = monitor.visible_task_ids
    if action == "cleanup_completed":
        candidates = visible
    elif task_id in visible and action in {"cleanup", "cancel", "pause", "resume"}:
        candidates = (task_id,)
    else:
        return
    for identity in candidates:
        status = manager.get_status(identity)
        if status.get("error") or not task_scope_matches(status, scope):
            continue
        command = "cleanup" if action == "cleanup_completed" else action
        if command == "cleanup" and status.get("status") not in {"completed", "failed", "cancelled"}:
            continue
        getattr(manager, command)(identity)
