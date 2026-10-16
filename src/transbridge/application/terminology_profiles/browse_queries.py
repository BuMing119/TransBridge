"""Explain profile mappings against the exact base used by effective terminology."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from enum import StrEnum
from threading import RLock

from transbridge.application.terminology.browse_queries import TermBrowseContext
from transbridge.application.terminology.effective import (
    EffectiveSnapshotStatus,
    EffectiveTerminologySnapshot,
    EffectiveTerminologySnapshotPort,
)
from transbridge.application.terminology.identity import canonical_digest
from transbridge.application.terminology.models import TermDecision
from transbridge.application.terminology.query_control import QueryCancellation

from .effective import (
    ProfiledEffectiveTerminologySnapshotPort,
    TerminologyProfileRevisionSource,
)
from .models import ProfileTermMapping, PublishedTerminologyProfile, logical_term_key


class ProfileMappingState(StrEnum):
    EFFECTIVE = "effective"
    BASE_MISSING = "base_missing"
    BASE_INACTIVE = "base_inactive"
    UNMAPPED = "unmapped"


@dataclass(frozen=True, slots=True)
class ProfileMappingRow:
    term_key: str
    mapping: ProfileTermMapping | None
    base_decision: TermDecision | None
    state: ProfileMappingState

    def __post_init__(self) -> None:
        if self.mapping is None and self.base_decision is None:
            raise ValueError("a profile mapping row requires a mapping or base decision")

    @property
    def original(self) -> str:
        return self.mapping.original if self.mapping is not None else self.base_decision.original

    @property
    def translation(self) -> str:
        return "" if self.mapping is None else self.mapping.translation

    @property
    def base_translation(self) -> str:
        return "" if self.base_decision is None else self.base_decision.translation

    @property
    def scope_kind(self) -> str:
        return self.mapping.scope_kind if self.mapping is not None else self.base_decision.scope.kind.value

    @property
    def plugin_id(self) -> str | None:
        return self.mapping.plugin_id if self.mapping is not None else self.base_decision.scope.plugin_id


class ProfiledTerminologyBrowseSnapshotPort:
    """Reuse the AI projection and explain the exact assets it read."""

    def __init__(self, base: EffectiveTerminologySnapshotPort, profiles: TerminologyProfileRevisionSource) -> None:
        self._base = base
        self._profiles = profiles
        self._cache: OrderedDict[object, TermBrowseContext] = OrderedDict()
        self._lock = RLock()

    def describe(
        self, project_id: str, variant_id: str, *, cancellation: QueryCancellation | None = None
    ) -> TermBrowseContext:
        with self._lock:
            for _ in range(3):
                if cancellation is not None:
                    cancellation.raise_if_cancelled()
                token = (_read_token(self._base), _read_token(self._profiles))
                base = self._base.snapshot(project_id, variant_id)
                if cancellation is not None:
                    cancellation.raise_if_cancelled()
                profile = self._profiles.selected_revision(project_id, variant_id)
                if cancellation is not None:
                    cancellation.raise_if_cancelled()
                if token != (_read_token(self._base), _read_token(self._profiles)):
                    continue
                key = (
                    project_id,
                    variant_id,
                    base.snapshot_identity,
                    base.status,
                    base.diagnostics,
                    None
                    if profile is None
                    else (
                        profile.project_id,
                        profile.profile_id,
                        profile.revision,
                        profile.content_digest,
                        profile.name,
                        profile.published_at,
                    ),
                )
                context = self._cache.get(key)
                if context is None:
                    context = self._compose(project_id, variant_id, base, profile, cancellation)
                    self._cache[key] = context
                    while len(self._cache) > 2:
                        self._cache.popitem(last=False)
                self._cache.move_to_end(key)
                return context
        raise ValueError("术语或译名方案在读取时发生变化，请重试")

    def _compose(self, project_id, variant_id, base, profile, cancellation) -> TermBrowseContext:
        capture = _CompositionCapture(base, profile)
        snapshot = ProfiledEffectiveTerminologySnapshotPort(capture, capture).snapshot(
            project_id, variant_id, cancellation=cancellation
        )
        base, profile = capture.base_snapshot, capture.profile
        assert base is not None
        if profile is not None and profile.project_id != project_id:
            raise ValueError("当前译名方案属于其他工程")
        readable = base.status in {EffectiveSnapshotStatus.READY, EffectiveSnapshotStatus.NO_PROJECT_VERSION}
        rows = () if profile is None or not readable else _mapping_rows(base.decisions, profile, cancellation)
        identity = (
            snapshot.snapshot_identity,
            None if profile is None else (profile.profile_id, profile.revision, profile.content_digest),
        )
        return TermBrowseContext(
            effective_snapshot=snapshot,
            snapshot_digest=canonical_digest(identity, namespace="terminology.browse-effective.v1"),
            base_version_id=base.version_id,
            profile_id=None if profile is None else profile.profile_id,
            profile_name=None if profile is None else profile.name,
            profile_revision=None if profile is None else profile.revision,
            mapping_rows=rows,
            diagnostics=snapshot.diagnostics,
        )


class _CompositionCapture:
    """Capture the dependencies read once by the shared effective projection."""

    def __init__(self, base: EffectiveTerminologySnapshot, profile: PublishedTerminologyProfile | None) -> None:
        self.base_snapshot = base
        self.profile = profile

    def snapshot(self, project_id: str, variant_id: str, version_id: str | None = None) -> EffectiveTerminologySnapshot:
        return self.base_snapshot

    def selected_revision(self, project_id: str, variant_id: str) -> PublishedTerminologyProfile | None:
        return self.profile

    def published_revision(self, profile_id: str, revision: int) -> PublishedTerminologyProfile | None:
        raise ValueError("captured browse composition does not resolve other revisions")


def _read_token(source) -> object:
    reader = getattr(source, "read_cache_token", None)
    return None if reader is None else reader()


def _mapping_rows(
    decisions: tuple[TermDecision, ...],
    profile: PublishedTerminologyProfile,
    cancellation: QueryCancellation | None = None,
) -> tuple[ProfileMappingRow, ...]:
    mappings = {}
    for item in profile.content.mappings:
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        mappings[item.term_key] = item
    base = {}
    for item in decisions:
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        base[logical_term_key(item.original, scope_kind=item.scope.kind.value, plugin_id=item.scope.plugin_id)] = item
    rows = []
    for key in sorted(mappings.keys() | base.keys()):
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        mapping, decision = mappings.get(key), base.get(key)
        if decision is None:
            state = ProfileMappingState.BASE_MISSING
        elif not decision.is_effective:
            state = ProfileMappingState.BASE_INACTIVE
        elif mapping is None:
            state = ProfileMappingState.UNMAPPED
        else:
            state = ProfileMappingState.EFFECTIVE
        rows.append(ProfileMappingRow(key, mapping, decision, state))
    return tuple(rows)
