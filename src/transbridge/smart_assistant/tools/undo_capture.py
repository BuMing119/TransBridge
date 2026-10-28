"""Attach undo evidence only around an actual local Variant command."""

from .undo_admission import assistant_undo_target


def capture_variant_command(context, mutation, *, warnings=None):
    """Use the program-owned effect identity for assistant writes.

    Call this inside the dispatched command, never around a queued GUI callback
    or around the generic execution gate. The command retains its ordinary CAS.
    """
    target = assistant_undo_target(context)
    if target is None:
        return mutation()
    gate, undo, effect = target
    completed = []

    def commit():
        result = mutation()
        completed.append(result)
        return result

    try:
        return undo.capture_variant_commit(gate.context, effect.execution, effect.effect_id, commit)
    except Exception as exc:
        if warnings is None or not completed or not completed[0].is_success:
            raise
        from .diagnostics import technical_error

        warnings.append(
            f"修改已提交，但撤销回执保存失败：{technical_error(exc)}；自动撤销可能不可用，请勿重复执行本次修改。"
        )
        return completed[0]
