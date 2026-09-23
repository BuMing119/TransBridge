"""Record a local ParaTranz binding command without touching the remote project."""

from copy import deepcopy

from transbridge.application.contracts import DomainError
from transbridge.application.projects.assistant_binding_undo import capture_binding_undo

from .admission import validate_effect
from .models import RequestError
from .round_undo import _execution_owner


def capture_binding_commit(undo, context, execution, effect_id, mutation):
    with undo.requests.serialized(context):
        request = _execution_owner(undo.requests.state(context), context, execution)
        effect = validate_effect(request, effect_id)
        if effect.execution != execution:
            raise RequestError("UNDO_SCOPE_MISMATCH", "本地工程绑定的执行归属不匹配")
        return _capture_binding_commit(undo, context, execution, effect_id, mutation)


def _capture_binding_commit(undo, context, execution, effect_id, mutation):
    active = undo.projects.active
    if active is None:
        return mutation()
    before = deepcopy(active.project)
    if context.project_id != before.envelope.identity:
        raise RequestError("UNDO_SCOPE_MISMATCH", "本地工程绑定与撤销请求范围不匹配")
    identity = undo.begin_receipt(context, execution, effect_id, "binding", before.envelope.to_dict())
    result = mutation()
    latest = undo.projects.active
    after = None if latest is None else deepcopy(latest.project)
    if not result.is_success:
        undo._finish(context, execution.work_round_id, identity, "rejected" if after == before else "unresolved")
        return result
    if (
        after is None
        or not isinstance(result.value, dict)
        or result.value.get("project_revision") != after.envelope.revision
        or result.value.get("project_id") != before.envelope.identity
    ):
        undo._finish(context, execution.work_round_id, identity, "unresolved")
        return result
    try:
        receipt = capture_binding_undo(before, after)
    except DomainError as exc:
        undo._finish(context, execution.work_round_id, identity, "unresolved", detail=str(exc))
        return result
    reference = undo._store(context, receipt)
    undo._finish(context, execution.work_round_id, identity, "recorded", receipt_ref=reference)
    return result
