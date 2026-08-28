"""Read-only effective terminology contracts and scope resolution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from .identity import normalize_original
from .models import DecisionStatus, ScopeKind, TermDecision


@dataclass(frozen=True, slots=True)
class TerminologyLookupContext:
    """Local Project/Variant identity, deliberately distinct from ParaTranz IDs."""

    local_project_id: str
    local_variant_id: str
    plugin_id: str | None = None
    version_id: str | None = None

    def __post_init__(self) -> None:
        if not self.local_project_id.strip() or not self.local_variant_id.strip():
            raise ValueError("terminology lookup requires local Project and Variant identities")
        for value, label in ((self.plugin_id, "plugin ID"), (self.version_id, "version ID")):
            if value is not None and not value.strip():
                raise ValueError(f"{label} must be absent or non-empty")

    def for_plugin(self, plugin_id: str | None) -> TerminologyLookupContext:
        return TerminologyLookupContext(
            self.local_project_id,
            self.local_variant_id,
            plugin_id=plugin_id,
            version_id=self.version_id,
        )


class EffectiveSnapshotStatus(StrEnum):
    READY = "ready"
    NO_PROJECT_VERSION = "no_project_version"
    UNAVAILABLE = "unavailable"
    CORRUPT = "corrupt"


@dataclass(frozen=True, slots=True)
class EffectiveTerminologySnapshot:
    local_project_id: str
    local_variant_id: str
    status: EffectiveSnapshotStatus
    version_id: str | None = None
    content_digest: str | None = None
    decisions: tuple[TermDecision, ...] = ()
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.local_project_id.strip() or not self.local_variant_id.strip():
            raise ValueError("effective snapshot requires local Project and Variant identities")
        object.__setattr__(self, "status", EffectiveSnapshotStatus(self.status))
        object.__setattr__(self, "decisions", tuple(sorted(self.decisions, key=lambda item: item.term_id)))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        if any(
            (item.project_id, item.variant_id) != (self.local_project_id, self.local_variant_id)
            for item in self.decisions
        ):
            raise ValueError("effective decisions must belong to the snapshot Project/Variant")
        if self.status is EffectiveSnapshotStatus.READY:
            if self.version_id is None or self.content_digest is None:
                raise ValueError("ready effective snapshot requires version identity and content digest")
            if not self.version_id.strip() or not self.content_digest.strip():
                raise ValueError("effective version identity and digest must not be empty")
        elif self.decisions:
            raise ValueError("non-ready effective snapshots cannot expose decisions")
        if (
            self.status in {EffectiveSnapshotStatus.UNAVAILABLE, EffectiveSnapshotStatus.CORRUPT}
            and not self.diagnostics
        ):
            raise ValueError("unavailable or corrupt effective snapshot requires diagnostics")

    @property
    def snapshot_identity(self) -> str:
        version = self.version_id or self.status.value
        digest = self.content_digest or self.status.value
        return f"{self.local_project_id}:{self.local_variant_id}:{version}:{digest}"


class EffectiveTerminologySnapshotPort(Protocol):
    """S08/persistence integration point; implementations remain read-only."""

    def snapshot(
        self,
        local_project_id: str,
        local_variant_id: str,
        version_id: str | None = None,
    ) -> EffectiveTerminologySnapshot: ...


@dataclass(frozen=True, slots=True)
class EffectiveTermResolution:
    decision: TermDecision | None
    blocks_legacy_fallback: bool
    snapshot: EffectiveTerminologySnapshot


@runtime_checkable
class EffectiveTerminologyPort(Protocol):
    def snapshot(
        self,
        local_project_id: str,
        local_variant_id: str,
        version_id: str | None = None,
    ) -> EffectiveTerminologySnapshot: ...

    def resolve(self, term: str, context: TerminologyLookupContext) -> EffectiveTermResolution: ...


class SnapshotEffectiveTerminologyPort:
    """Resolve scope precedence from immutable version membership snapshots."""

    def __init__(self, snapshots: EffectiveTerminologySnapshotPort) -> None:
        self._snapshots = snapshots

    def snapshot(
        self,
        local_project_id: str,
        local_variant_id: str,
        version_id: str | None = None,
    ) -> EffectiveTerminologySnapshot:
        snapshot = self._snapshots.snapshot(local_project_id, local_variant_id, version_id)
        if (snapshot.local_project_id, snapshot.local_variant_id) != (local_project_id, local_variant_id):
            raise ValueError("effective snapshot source returned another Project/Variant line")
        if version_id is not None and snapshot.version_id != version_id:
            raise ValueError("effective snapshot source returned another version")
        return snapshot

    def resolve(self, term: str, context: TerminologyLookupContext) -> EffectiveTermResolution:
        snapshot = self.snapshot(
            context.local_project_id,
            context.local_variant_id,
            context.version_id,
        )
        return resolve_snapshot(snapshot, term, context)


def resolve_snapshot(
    snapshot: EffectiveTerminologySnapshot,
    term: str,
    context: TerminologyLookupContext,
) -> EffectiveTermResolution:
    if (snapshot.local_project_id, snapshot.local_variant_id) != (
        context.local_project_id,
        context.local_variant_id,
    ):
        raise ValueError("lookup context does not match the effective snapshot")
    if snapshot.status is not EffectiveSnapshotStatus.READY:
        # Translation consumption deliberately preserves the pre-FR5.16 legacy
        # path when the project asset cannot be used.  The workbench surfaces
        # the diagnostics and remains fail-closed for writes separately.
        return EffectiveTermResolution(None, False, snapshot)
    normalized = normalize_original(term)
    if not normalized:
        return EffectiveTermResolution(None, False, snapshot)
    matching = tuple(item for item in snapshot.decisions if item.normalized_original == normalized)
    scoped: list[tuple[TermDecision, ...]] = []
    if context.plugin_id is not None:
        scoped.append(
            tuple(
                item
                for item in matching
                if item.scope.kind is ScopeKind.PLUGIN and item.scope.plugin_id == context.plugin_id
            )
        )
    scoped.append(tuple(item for item in matching if item.scope.kind is ScopeKind.PROJECT))
    for decisions in scoped:
        allowed = tuple(item for item in decisions if _is_effective(item))
        if allowed:
            return EffectiveTermResolution(allowed[-1], False, snapshot)
        if decisions:
            # An explicit decision at the highest applicable scope is a shadow
            # even while suppressed, unresolved, or awaiting review.  It must
            # not be silently replaced by a lower-scope or legacy term.
            return EffectiveTermResolution(None, True, snapshot)
    return EffectiveTermResolution(None, False, snapshot)


def _is_effective(decision: TermDecision) -> bool:
    return not decision.suppressed and decision.status in {
        DecisionStatus.ADOPTED,
        DecisionStatus.MANUAL_CONFIRMED,
    }


__all__ = [
    "EffectiveSnapshotStatus",
    "EffectiveTermResolution",
    "EffectiveTerminologyPort",
    "EffectiveTerminologySnapshot",
    "EffectiveTerminologySnapshotPort",
    "SnapshotEffectiveTerminologyPort",
    "TerminologyLookupContext",
    "resolve_snapshot",
]
