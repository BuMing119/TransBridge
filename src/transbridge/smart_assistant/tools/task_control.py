"""Resolve and control background tasks within the captured caller lifecycle."""

from __future__ import annotations

from .task_runtime_bridge import task_metadata
from .types import ToolResult


def task_scope_matches(status: dict, scope: dict) -> bool:
    """Require exact ownership; an absent caller scope never grants global access."""
    owner = status.get("owner", status.get("metadata", {}))
    for key in ("owner_id", "session_id", "project_id", "variant_id"):
        actual = owner.get(key) or ""
        expected = scope.get(key) or ""
        if key == "owner_id" and actual == "legacy-task-manager":
            actual = ""
        if str(actual) != str(expected):
            return False
    return True


def action_label(action: str) -> str:
    return {"stop": "发送停止信号", "pause": "暂停", "resume": "恢复"}.get(action, action)


def control_tasks(args: dict, ctx, manager) -> ToolResult:
    """Resolve an explicit task, a unique active task, or an explicit scoped batch."""
    action = args.get("action", "stop")
    if action not in ("stop", "pause", "resume"):
        return ToolResult.fail(f"无效 action: {action}，可选: stop, pause, resume")
    task_id = args.get("task_id")
    all_tasks = args.get("all_tasks", False)
    if not isinstance(all_tasks, bool) or (task_id and all_tasks):
        return ToolResult.fail("all_tasks 必须为布尔值，且不能与 task_id 同时指定")
    scope = task_metadata(ctx, {})
    if task_id:
        status = manager.get_status(task_id)
        if status.get("error") or not task_scope_matches(status, scope):
            return ToolResult.fail(f"任务不存在或已结束，或不属于当前会话: {task_id}")
        targets = [task_id]
    else:
        active = {}
        for tid in manager.list_active():
            status = manager.get_status(tid)
            if not status.get("error") and task_scope_matches(status, scope):
                active[tid] = status
        targets = list(active)
        if not all_tasks:
            targets = [
                tid
                for tid, status in active.items()
                if status.get("metadata", {}).get("parent_task_id") not in active
                or status.get("metadata", {}).get("parent_task_id") == tid
            ]
            if active and not targets:
                return ToolResult.fail(
                    "任务父子关系无有效根任务，请指定 task_id", data={"candidate_task_ids": list(active)}
                )
        if not targets:
            return ToolResult.ok("当前会话无运行中的任务", data={"affected_task_ids": [], "action": action})
        if len(targets) > 1 and not all_tasks:
            return ToolResult.fail(
                "当前会话有多个活跃任务，请指定 task_id；仅在用户明确要求全部操作时使用 all_tasks=true",
                data={"candidate_task_ids": targets},
            )

    affected, failed, states = [], [], {}
    operation = {"stop": manager.cancel, "pause": manager.pause, "resume": manager.resume}[action]
    for target in targets:
        if operation(target):
            affected.append(target)
        else:
            failed.append(target)
        states[target] = manager.get_status(target).get("status", "unknown")
    data = {"affected_task_ids": affected, "action": action, "task_states": states}
    if len(targets) == 1:
        data.update(task_id=targets[0], status=states[targets[0]])
    if failed:
        data["failed_task_ids"] = failed
        message = f"{len(affected)} 个任务已{action_label(action)}，{len(failed)} 个任务未执行；请查看实际状态"
        return ToolResult.partial_ok(message, data=data) if affected else ToolResult.fail(message, data=data)
    if action == "stop":
        pending = sum(state == "cancelling" for state in states.values())
        message = f"已向 {len(affected)} 个任务发送停止信号；{pending} 个仍在取消中" if pending else "任务已取消"
    else:
        message = f"已{action_label(action)} {len(affected)} 个任务"
    return ToolResult.ok(message, data=data)


def get_scoped_task_status(args: dict, ctx, manager) -> ToolResult:
    """Project only tasks belonging to the same captured caller scope."""
    scope = task_metadata(ctx, {})
    task_id = args.get("task_id")
    if task_id:
        status = manager.get_status(task_id)
        if status.get("error") or not task_scope_matches(status, scope):
            return ToolResult.fail("任务不存在或不属于当前会话")
        return ToolResult.ok(f"任务 {task_id}: {status['status']}", data=status)
    statuses = [manager.get_status(tid) for tid in manager.list_all()]
    statuses = [status for status in statuses if not status.get("error") and task_scope_matches(status, scope)]
    active_count = sum(status["status"] in {"queued", "running", "paused", "cancelling"} for status in statuses)
    return ToolResult.ok(
        f"活跃任务: {active_count} / 总任务: {len(statuses)}",
        data={"active_count": active_count, "total_count": len(statuses), "tasks": statuses},
    )
