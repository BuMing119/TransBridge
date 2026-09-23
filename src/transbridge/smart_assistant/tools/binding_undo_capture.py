"""Capture only the local binding command after ParaTranz target validation."""

from transbridge.application.assistant_requests.binding_undo_capture import capture_binding_commit

from .undo_admission import assistant_undo_target


def capture_binding_command(context, mutation):
    target = assistant_undo_target(context)
    if target is None:
        return mutation()
    gate, undo, effect = target
    return capture_binding_commit(undo, gate.context, effect.execution, effect.effect_id, mutation)
