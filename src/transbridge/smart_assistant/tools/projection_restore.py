"""Restore a captured view from authority without admitting a new business write."""

from copy import copy


def restore_entry_projection(context, states, collection=None):
    """Only restore the original view; never publish or overwrite another version."""
    from .types import _projection_entry_states

    app = context.__dict__.get("app_context")
    target = collection if collection is not None else context.__dict__.get("_target_collection")
    if app is None or target is None or not context._target_is_current(app, target):
        return
    captured = dict(states)

    def restore():
        # A queued restore can arrive after the user has switched versions.
        if not context._target_is_current(app, target):
            return
        current = captured
        if bool(getattr(app, "uses_authoritative_projection", False)):
            authoritative = _projection_entry_states(app, target, context.__dict__.get("_target_version_identity"))
            if authoritative is None or any(entry.identity not in authoritative for entry in target):
                raise RuntimeError("无法核验最新权威条目，未使用旧快照恢复。")
            current = authoritative
        changed = False
        for entry in target:
            value = current.get(entry.identity)
            if value is not None and (entry.translation, entry.stage) != value:
                entry.translation, entry.stage = value
                changed = True
        if changed:
            context._emit_collection_changed(app, target)

    # This narrowly owned callback only repairs a projection. Reusing a cancelled
    # effect's business commit permission would prevent its own cleanup.
    dispatcher = copy(context)
    dispatcher.assistant_gate = None
    dispatcher.assistant_effect_id = ""
    dispatcher.safe_mutate_wait(restore)
