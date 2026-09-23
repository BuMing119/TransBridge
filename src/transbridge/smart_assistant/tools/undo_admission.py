"""Resolve assistant write provenance without downgrading an admitted command."""

from transbridge.application.assistant_requests.admission import get_effect
from transbridge.application.assistant_requests.models import RequestError


def assistant_undo_target(context):
    """Return the admitted capture target, or None for an independent tool call."""
    gate = getattr(context, "assistant_gate", None)
    if gate is None:
        if getattr(context, "assistant_required", False) or getattr(context, "assistant_effect_id", ""):
            raise RequestError("TURN_LEASE_STALE", "请求执行许可缺失，操作未开始")
        return None
    undo = getattr(gate.service, "undo", None)
    if undo is None:
        raise RequestError("UNDO_STORAGE_UNAVAILABLE", "助手撤销服务未配置，操作未开始")
    effect = get_effect(gate.current_request(), getattr(context, "assistant_effect_id", ""))
    if not effect.execution.work_round_id:
        raise RequestError("UNDO_ROUND_SOURCE_MISSING", "旧任务缺少工作轮次，请发送新的继续指令后再执行修改")
    return gate, undo, effect
