"""Attach undo evidence only around an actual local Variant command."""

from .undo_admission import assistant_undo_target


def capture_variant_command(context, mutation):
    """Use the program-owned effect identity for assistant writes.

    Call this inside the dispatched command, never around a queued GUI callback
    or around the generic execution gate. The command retains its ordinary CAS.
    """
    target = assistant_undo_target(context)
    if target is None:
        return mutation()
    gate, undo, effect = target
    return undo.capture_variant_commit(gate.context, effect.execution, effect.effect_id, mutation)
