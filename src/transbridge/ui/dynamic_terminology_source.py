"""Resolve the same dynamic-cache source used by AI tasks and source pickers."""

from dataclasses import dataclass
from pathlib import PureWindowsPath


@dataclass(frozen=True, slots=True)
class DynamicTerminologySource:
    source_key: str | None
    reference: str | None
    display_name: str

    @property
    def cache_path(self) -> str:
        from transbridge.ui.tools.ai_translator.source_terms import source_terminology_path

        return source_terminology_path(self.reference, self.source_key)


def dynamic_terminology_source(context, source=None) -> DynamicTerminologySource:
    """References are optional; saved names only label the existing cache identity."""
    slots = getattr(context, "slots", {})
    if source is None:
        slot = getattr(context, "active_slot", None)
        key = next((str(key) for key, value in slots.items() if value is slot), None)
        key = key or getattr(context, "active_key", None)
        reference = getattr(slot, "esp_path", None) if slot is not None else getattr(context, "esp_path", None)
        source = next((item for item in getattr(context, "project_sources", ()) if item.get("source_id") == key), {})
    else:
        key = str(source.get("source_id") or source.get("key") or "") or None
        slot = slots.get(key)
        reference = getattr(slot, "esp_path", None) if slot is not None else source.get("location")
    snapshot = getattr(getattr(slot, "source_snapshot", None), "source", None)
    display_name = (
        getattr(snapshot, "display_name", None)
        or source.get("display_name")
        or source.get("name")
        or source.get("label")
        or getattr(slot, "label", None)
        or (PureWindowsPath(reference).name if reference else "")
        or key
        or ""
    )
    return DynamicTerminologySource(key, reference, str(display_name))
