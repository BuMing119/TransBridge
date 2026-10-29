"""Explicit source availability on saved translation rows used by the workbench."""

from dataclasses import dataclass

from transbridge.application.preflight.inputs import (
    ORIGINAL_KNOWN as ORIGINAL_KNOWN,
    SourceInputFacts,
    missing_source_keys as _missing_source_keys,
)


def original_known(entry) -> bool:
    """An empty carrier string on a saved-only row is not parsed source text."""
    return dict(getattr(entry, "metadata", ())).get(ORIGINAL_KNOWN, True) is not False


def missing_source_keys(context, requirement: str, source_keys=None) -> tuple[str, ...]:
    slots = getattr(context, "slots", {})
    selected = tuple(slots) if source_keys is None else tuple(source_keys)
    return _missing_source_keys(
        (
            SourceInputFacts(
                key,
                all(original_known(entry) for entry in slots[key].collection),
                getattr(slots[key], "source_snapshot", None) is not None,
            )
            for key in selected
            if key in slots
        ),
        requirement,
    )


def ensure_source_input(
    context,
    requirement,
    *,
    resume,
    source_keys=None,
    parent=None,
    on_scope_ready=None,
    allow_partial=False,
    operation_label="本次操作",
):
    if not missing_source_keys(context, requirement, source_keys):
        return True
    coordinator = getattr(context, "source_input_coordinator", None)
    return (
        False
        if coordinator is None
        else coordinator.ensure(
            requirement,
            source_keys,
            resume=resume,
            parent=parent,
            on_scope_ready=on_scope_ready,
            allow_partial=allow_partial,
            operation_label=operation_label,
        )
    )


@dataclass(frozen=True)
class SourceInputScope:
    context: object
    source_keys: tuple[str, ...]

    @property
    def slots(self):
        coordinator = getattr(self.context, "source_input_coordinator", None)
        keys = self.source_keys if coordinator is None else coordinator.resolved_keys(self.source_keys)
        slots = getattr(self.context, "slots", {})
        return {key: slots[key] for key in keys if key in slots}

    @property
    def active_key(self):
        slots = self.slots
        current = getattr(self.context, "active_key", None)
        return current if current in slots else next(iter(slots), None)

    @property
    def active_slot(self):
        return self.slots.get(self.active_key)

    def __getattr__(self, name):
        return getattr(self.context, name)
