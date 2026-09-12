"""Persist request intent when a user controls jobs from another application entrypoint."""

from dataclasses import replace

from transbridge.application.contracts import RequestContext
from transbridge.application.tasks import TaskAccessError

from .journal import EventCause
from .models import RequestError
from .reducer import require_open


def request_task_control(service, runtime):
    def control(ref, actor, action, *, expected_revision=None):
        snapshot = runtime.get(ref, actor)
        owner = snapshot.owner
        operation = {"cancel": runtime.cancel, "pause": runtime.pause, "resume": runtime.resume}[action]
        if not owner.session_id or not dict(snapshot.specification.metadata).get("assistant_request_id"):
            return operation(ref, actor, expected_revision=expected_revision)
        context = RequestContext(
            owner.owner_id,
            session_id=owner.session_id,
            project_id=owner.project_id,
            variant_id=owner.variant_id,
            permissions=actor.permissions,
        )
        # Emergency cancellation remains possible even if persistence is unavailable.
        cancelled = operation(ref, actor, expected_revision=expected_revision) if action == "cancel" else None
        try:
            with service.serialized(context):
                requests = service.requests(service.state(context))
                request = next(
                    (r for r in requests if any(record.run_id == ref.run_id for record in (*r.effects, *r.dispatches))),
                    None,
                )
                if request is None:
                    raise RequestError("REQUEST_RECOVERY_REQUIRED", "任务的原请求关联缺失，不能恢复执行")
                record = next(
                    record for record in (*request.effects, *request.dispatches) if record.run_id == ref.run_id
                )
                ids = set(record.execution.item_ids)
                if action == "resume":
                    require_open(request, record.execution.request_revision)
                    if request.pause_reasons or any(
                        i.waiting_reasons and set(i.waiting_reasons) != {"user_job_control"}
                        for i in request.items
                        if i.item_id in ids
                    ):
                        raise RequestError("REQUEST_PAUSED", "原请求仍暂停或有独立等待原因，不能直接恢复任务")
                    if getattr(record, "status", "") == "outcome_unknown":
                        raise RequestError("OUTCOME_UNKNOWN", "请先核对旧操作结果")
                result = (
                    cancelled if cancelled is not None else operation(ref, actor, expected_revision=expected_revision)
                )

                def record_control(current):
                    items = tuple(
                        replace(
                            item,
                            waiting_reasons=tuple(r for r in item.waiting_reasons if r != "user_job_control")
                            if action == "resume"
                            else tuple(dict.fromkeys((*item.waiting_reasons, "user_job_control"))),
                        )
                        if item.item_id in ids
                        else item
                        for item in current.items
                    )
                    return replace(current, items=items)

                service.update_request(
                    context,
                    request.request_id,
                    record_control,
                    cause=EventCause(
                        f"task.{action}",
                        "user",
                        {
                            "job_ids": [ref.job_id],
                            "run_ids": [ref.run_id],
                        },
                    ),
                )
            service.notify(owner.session_id)
            return result
        except RequestError as error:
            detail = f"取消信号已发送，但请求记录失败：{error}" if action == "cancel" else str(error)
            raise TaskAccessError(error.code, detail) from error

    return control
