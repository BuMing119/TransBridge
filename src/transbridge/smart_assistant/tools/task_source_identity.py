# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Freeze source resource names before detached assistant execution starts."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AssistantSourceIdentity:
    key: str
    reference_path: str
    saved_name: str | None


def capture_source_identity(ctx, collection) -> AssistantSourceIdentity:
    from .input_prerequisites import source_slot_for

    app = getattr(ctx, "app_context", None) or ctx
    slots = getattr(app, "slots", {})
    matched = next(
        ((str(key), slot) for key, slot in slots.items() if slot.collection is collection),
        None,
    )
    if matched is None and (first := next(iter(collection), None)) is not None:
        owner = source_slot_for(ctx, first)
        matched = next(((str(key), slot) for key, slot in slots.items() if slot is owner), None)
    fallback_key = getattr(ctx, "_target_source_key", None) or getattr(ctx, "active_key", None) or "assistant"
    key, slot = matched if matched is not None else (str(fallback_key), None)
    reference = str((getattr(slot, "esp_path", None) if slot is not None else getattr(ctx, "esp_path", None)) or "")
    source = getattr(getattr(slot, "source_snapshot", None), "source", None)
    name = getattr(source, "display_name", None) or getattr(source, "uri", None)
    return AssistantSourceIdentity(key, reference, name)
