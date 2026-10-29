"""Selected assistant input facts and an actionable, caller-bound repair receipt."""

from transbridge.application.preflight.inputs import ORIGINAL_KNOWN, SourceInputFacts, missing_source_keys

from .types import ToolResult


class SourceInputRequired(ValueError):
    def __init__(self, result):
        super().__init__(result.message)
        self.result = result


def original_available(entry):
    return dict(getattr(entry, "metadata", ())).get(ORIGINAL_KNOWN, True) is not False


def source_slot_for(ctx, entry):
    app = getattr(ctx, "app_context", None) or ctx
    slots = getattr(app, "slots", {})
    captured = getattr(ctx, "_target_source_key", None)
    if captured in slots and slots[captured].collection.get(entry.identity) is not None:
        return slots[captured]
    return next((slot for slot in slots.values() if slot.collection.get(entry.identity) is not None), None)


def source_input_failure(
    ctx, entries=None, *, requirement="originals", source_keys=None, error_code="SOURCE_INPUT_REQUIRED"
):
    """Return only the existing missing prerequisite, with no Qt or file reads.

    Omitted entries mean every row of the explicitly selected sources. An omitted
    source scope means the captured active source, or exact target entry owners.
    """
    app = getattr(ctx, "app_context", None) or ctx
    slots = getattr(app, "slots", {})
    targets = None if entries is None else tuple(entries)
    target_keys = None if targets is None else {entry.identity for entry in targets}
    if source_keys is None:
        captured = getattr(ctx, "_target_source_key", None) or getattr(app, "active_key", None)
        source_keys = (
            (captured,)
            if captured in slots
            else tuple(
                key
                for key, slot in slots.items()
                if target_keys is not None and any(entry.identity in target_keys for entry in slot.collection)
            )
        )
    selected = tuple((key, slots[key]) for key in source_keys if key in slots)
    if targets is None:
        targets = tuple(entry for _key, slot in selected for entry in slot.collection)
        target_keys = {entry.identity for entry in targets}
    facts = tuple(
        SourceInputFacts(
            key,
            all(original_available(entry) for entry in slot.collection if entry.identity in target_keys),
            getattr(slot, "source_snapshot", None) is not None,
        )
        for key, slot in selected
    )
    # Compatibility contexts without slots can still carry an explicit unknown row.
    if not selected and targets:
        facts = (SourceInputFacts("assistant", all(original_available(entry) for entry in targets), False),)
    missing = missing_source_keys(facts, requirement)
    if not missing:
        return None
    identity = getattr(ctx, "_target_version_identity", None) or getattr(app, "active_version_identity", None)
    repair = {
        "kind": "source_input",
        "requirement": requirement,
        "project_id": identity[0] if identity else None,
        "variant_id": identity[1] if identity else None,
        "source_keys": [fact.source_key for fact in facts],
        "missing_source_keys": list(missing),
        "entry_keys": [entry.identity.to_dict() for entry in targets],
        "source_ids": [slot.source_id for key, slot in selected if key in missing and getattr(slot, "source_id", None)],
        "namespaces": list(dict.fromkeys(entry.identity.namespace.value for entry in targets)),
    }
    gate = getattr(ctx, "assistant_gate", None)
    admission = getattr(gate, "admission", None)
    if admission is not None:
        from transbridge.application.assistant_requests.admission import get_effect

        ids = admission.ready_item_ids
        effect_id = getattr(ctx, "assistant_effect_id", "")
        if effect_id:
            ids = get_effect(gate.current_request(), effect_id).execution.item_ids
        scope = dict(admission.scope)
        repair.update(
            project_id=scope.get("project_id", repair["project_id"]),
            variant_id=scope.get("variant_id", repair["variant_id"]),
            session_id=admission.session_id,
            request_id=admission.request_id,
            request_revision=admission.request_revision,
            request_lease_epoch=admission.request_lease_epoch,
            item_ids=list(ids),
        )
    return ToolResult.fail(
        "本次操作缺少原文或文件模板，请补充对应来源输入后重新准备并确认。",
        data={"source_input_repair": repair},
        error_category="input",
        error_code=error_code,
        recovery_action="supplement_source_input",
    )
