"""Adapter from immutable project terminology snapshots to ADR-027 TermEntry."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

from transbridge.application.terminology.effective import (
    EffectiveSnapshotStatus,
    EffectiveTerminologyPort,
    EffectiveTerminologySnapshot,
    TerminologyLookupContext,
    resolve_snapshot,
)
from transbridge.application.terminology.identity import normalize_original
from transbridge.application.terminology.models import TermDecision

from .term_formats import TermEntry


class EffectiveTerminologyFeatureGate(Protocol):
    def enabled(self, context: TerminologyLookupContext) -> bool: ...


class DisabledEffectiveTerminologyGate:
    def enabled(self, context: TerminologyLookupContext) -> bool:
        del context
        return False


@dataclass(frozen=True, slots=True)
class PublishedEffectiveTerminologyGate:
    """Enable only after a first published version is known for the line."""

    has_published_version: Callable[[str, str], bool]

    def enabled(self, context: TerminologyLookupContext) -> bool:
        try:
            return bool(self.has_published_version(context.local_project_id, context.local_variant_id))
        except Exception:
            # A damaged/unavailable repository must never make an existing
            # translation path fail merely while evaluating the opt-in gate.
            return False


@dataclass(frozen=True, slots=True)
class ProjectTerminologyLoad:
    entries: tuple[TermEntry, ...]
    status: EffectiveSnapshotStatus | None
    snapshot_identity: str
    diagnostics: tuple[str, ...] = ()


class ProjectTerminologyAdapter:
    """Project > matching plugin/global > legacy facade with a default-off gate."""

    def __init__(
        self,
        effective: EffectiveTerminologyPort,
        gate: EffectiveTerminologyFeatureGate | None = None,
    ) -> None:
        self._effective = effective
        self._gate = gate or DisabledEffectiveTerminologyGate()

    def enabled(self, context: TerminologyLookupContext) -> bool:
        return self._gate.enabled(context)

    def load(
        self,
        context: TerminologyLookupContext,
        legacy_entries: Iterable[TermEntry],
    ) -> ProjectTerminologyLoad:
        legacy = tuple(legacy_entries)
        if not self._gate.enabled(context):
            return ProjectTerminologyLoad(legacy, None, "legacy-global")
        snapshot = self._effective.snapshot(
            context.local_project_id,
            context.local_variant_id,
            context.version_id,
        )
        if snapshot.status is not EffectiveSnapshotStatus.READY:
            return ProjectTerminologyLoad(legacy, snapshot.status, "legacy-global", snapshot.diagnostics)
        terms = _candidate_terms(snapshot, legacy)
        merged: dict[str, TermEntry] = {}
        for entry in legacy:
            merged[normalize_original(entry.term)] = entry
        for term in terms:
            resolution = resolve_snapshot(snapshot, term, context)
            key = normalize_original(term)
            if resolution.decision is not None:
                merged[key] = project_term_entry(resolution.decision)
            elif resolution.blocks_legacy_fallback:
                merged.pop(key, None)
        return ProjectTerminologyLoad(
            tuple(merged.values()),
            snapshot.status,
            _scope_snapshot_identity(snapshot, context),
            snapshot.diagnostics,
        )

    def resolve(
        self,
        term: str,
        context: TerminologyLookupContext,
        legacy_resolver: Callable[[str], TermEntry | None],
    ) -> TermEntry | None:
        if not self._gate.enabled(context):
            return legacy_resolver(term)
        resolution = self._effective.resolve(term, context)
        if resolution.decision is not None:
            return project_term_entry(resolution.decision)
        if resolution.blocks_legacy_fallback:
            return None
        return legacy_resolver(term)

    def context_for_entry(
        self,
        base: TerminologyLookupContext,
        entry: object,
    ) -> TerminologyLookupContext:
        return base.for_plugin(plugin_id_from_entry(entry))

    def snapshot_identity(self, context: TerminologyLookupContext) -> str:
        if not self._gate.enabled(context):
            return "legacy-global"
        snapshot = self._effective.snapshot(
            context.local_project_id,
            context.local_variant_id,
            context.version_id,
        )
        if snapshot.status is not EffectiveSnapshotStatus.READY:
            return "legacy-global"
        return _scope_snapshot_identity(snapshot, context)


def plugin_id_from_entry(entry: object) -> str | None:
    form_id = getattr(entry, "form_id_with_plugin", None)
    if isinstance(form_id, str) and "|" in form_id:
        plugin_id = form_id.rsplit("|", 1)[1].strip()
        return plugin_id or None
    details = getattr(entry, "report_details", ())
    try:
        plugin_id = dict(details).get("terminology_plugin_id")
    except (TypeError, ValueError):
        return None
    return plugin_id if isinstance(plugin_id, str) and plugin_id.strip() else None


def project_term_entry(decision: TermDecision) -> TermEntry:
    decision.require_effective()
    return TermEntry(
        term=decision.original,
        translation=decision.translation,
        source="project-terminology",
        context=decision.scope.canonical_key,
        variants=list(decision.variants),
        note=decision.notes,
        external_id=decision.term_id,
    )


def _candidate_terms(snapshot: EffectiveTerminologySnapshot, legacy: tuple[TermEntry, ...]) -> tuple[str, ...]:
    values = {entry.term for entry in legacy}
    values.update(item.original for item in snapshot.decisions)
    return tuple(sorted(values, key=lambda value: (normalize_original(value), value)))


def _scope_snapshot_identity(
    snapshot: EffectiveTerminologySnapshot,
    context: TerminologyLookupContext,
) -> str:
    scope = f"plugin:{context.plugin_id}" if context.plugin_id is not None else "project"
    return f"{snapshot.snapshot_identity}:{scope}"


__all__ = [
    "DisabledEffectiveTerminologyGate",
    "EffectiveTerminologyFeatureGate",
    "ProjectTerminologyAdapter",
    "ProjectTerminologyLoad",
    "PublishedEffectiveTerminologyGate",
    "plugin_id_from_entry",
    "project_term_entry",
]
