"""Read-only user-task summaries, joining background work by authoritative identities."""


def related_tasks(request, tasks):
    identities = {(d.job_id, d.run_id) for d in request.dispatches if d.job_id and d.run_id}
    return tuple(
        task
        for task in tasks
        if task.get("metadata", {}).get("assistant_request_id") == request.request_id
        or (task.get("task_id"), task.get("run_id")) in identities
    )


def task_progress_text(tasks):
    lines = []
    phases = {
        "preparing": "准备中",
        "translating": "翻译中",
        "running": "处理中",
        "saving": "保存中",
        "completed": "已完成",
        "failed": "失败",
        "cancelled": "已取消",
        "paused": "已暂停",
    }
    for task in tasks:
        progress = task.get("progress", {}) or {}
        status = task.get("status")
        phase = str(
            status
            if status in {"paused", "completed", "failed", "cancelled"}
            else progress.get("phase") or status or "处理中"
        )
        label = phases.get(phase, phase)
        current, total = progress.get("current", 0), progress.get("total", 0)
        count = f" · {current}/{total}" if isinstance(total, (int, float)) and total > 0 else ""
        lines.append(label + count)
    return "；".join(lines)


def task_summary(request, tasks=()):
    from .request_progress_view import request_status

    done = sum(item.status == "satisfied" for item in request.items)
    failed = sum(item.status == "failed" for item in request.items)
    detail = f"{request_status(request)} · 已完成 {done}/{len(request.items)} 项"
    if failed:
        detail += f" · {failed} 项失败"
    background = task_progress_text(related_tasks(request, tasks))
    return detail + (f"\n{background}" if background else "")
