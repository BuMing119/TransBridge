"""Persist intent around one revision-checked, non-secret configuration write."""

from transbridge.config.repository import ConfigRepositoryError

from .admission import validate_effect
from .config_undo import FIELDS, capture_config_undo
from .round_undo import _execution_owner


def capture_config_commit(undo, context, execution, effect_id, updates):
    if not updates or set(updates) - FIELDS:
        raise ConfigRepositoryError("config_undo_invalid", "unsupported assistant configuration fields")
    with undo.requests.serialized(context):
        request = _execution_owner(undo.requests.state(context), context, execution)
        effect = validate_effect(request, effect_id)
        if effect.execution != execution:
            raise ConfigRepositoryError("config_undo_invalid", "configuration effect ownership does not match")
        repository = undo.configuration()
        before = repository.load()
        identity = undo.begin_receipt(
            context,
            execution,
            effect_id,
            "config",
            {
                "path": before.path,
                "revision": before.revision,
                "values": {key: before.value("llm", key) for key in updates},
            },
        )
        # A failure remains pending: never infer whether a filesystem replace committed.
        after = repository.update_sections({"llm": updates}, expected_revision=before.revision)
        receipt = capture_config_undo(before, after, updates)
        reference = undo._store(context, receipt)
        undo._finish(context, execution.work_round_id, identity, "recorded", receipt_ref=reference)
        return after
