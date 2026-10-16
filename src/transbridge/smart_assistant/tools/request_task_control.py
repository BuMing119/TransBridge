"""Apply one user-request control to its scoped, exact background task runs."""

from transbridge.application.assistant_requests.task_controls import request_task_control
from transbridge.application.contracts import JobRef
from transbridge.application.tasks import JobState, OwnerRef, TransitionError, job_snapshot_to_view

from .task_control import task_scope_matches


def _belongs(status, scope, request):
    return (
        not status.get("error")
        and task_scope_matches(status, scope)
        and (
            status.get("metadata", {}).get("assistant_request_id") == request.request_id
            or any(
                (dispatch.job_id, dispatch.run_id) == (status.get("task_id"), status.get("run_id"))
                for dispatch in request.dispatches
                if dispatch.job_id and dispatch.run_id
            )
        )
    )


def _control_one(manager, operation, scope, request, task_id, run_id, action):
    # A task list is only a candidate list. Re-read identity and ownership at the
    # action boundary; the immutable ref also fences replacement after this read.
    status = manager.get_status(task_id)
    if status.get("run_id") != run_id or not _belongs(status, scope, request):
        raise RuntimeError("任务运行代次或所属会话已经改变，请刷新后重试")
    owner = OwnerRef(**status["owner"])
    ref = JobRef(task_id, owner.owner_id, run_id)
    snapshot = manager.runtime.get(ref, owner)
    if not _belongs(job_snapshot_to_view(snapshot), scope, request):
        raise RuntimeError("后台任务的规范归属与当前请求不一致，未执行控制")
    desired = JobState.PAUSED if action == "pause" else JobState.RUNNING
    if snapshot.is_terminal or snapshot.state == desired:
        return False
    try:
        operation(ref, owner, action)
    except TransitionError:
        # Completion can win after the read. It needs no pause or restart.
        current = manager.runtime.get(ref, owner)
        if current.is_terminal or current.state == desired:
            return False
        raise
    return True


def control_request_tasks(manager, service, context, request, action) -> tuple[str, ...]:
    """Pause/resume owned jobs after the corresponding request command persists.

    Runs on the established background queue. Uses the application control bridge
    so worker signals and durable ``user_job_control`` waits change together.
    Cancelling remains the responsibility of the existing request event router.
    """
    if action not in {"pause", "resume"}:
        raise ValueError(f"不支持的请求后台任务操作：{action}")
    if not context.session_id or request.session_id != context.session_id or not request.request_id:
        raise ValueError("请求不属于当前会话，未控制后台任务")
    scope = {key: getattr(context, key) for key in ("owner_id", "session_id", "project_id", "variant_id")}
    targets = []
    for task_id in manager.list_all():
        status = manager.get_status(task_id)
        if _belongs(status, scope, request):
            targets.append((task_id, status.get("run_id")))
    operation = request_task_control(service, manager.runtime)
    affected, failures = [], []
    for task_id, run_id in targets:
        try:
            if not run_id:
                raise RuntimeError("后台任务缺少运行标识，未执行控制")
            if _control_one(manager, operation, scope, request, task_id, run_id, action):
                affected.append(task_id)
        except Exception as error:
            failures.append((task_id, run_id, error))
    if failures:
        label = "暂停" if action == "pause" else "恢复"
        details = "；".join(f"{task_id}（{run_id}）：{error}" for task_id, run_id, error in failures)
        raise RuntimeError(
            f"请求 {request.request_id} 的后台任务{label}未全部完成：{details}。请查看实际任务状态后重试。"
        ) from failures[0][2]
    return tuple(affected)
