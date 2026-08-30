"""Resolve the optional project terminology binding for translation consumers."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING

from transbridge.application.terminology.effective import TerminologyLookupContext
from transbridge.application.translation.terminology_run_snapshot import (
    FrozenEffectiveTerminologyPort,
    FrozenTerminologyRunSnapshot,
    TerminologyRunSnapshotFactory,
    TerminologyRunSnapshotRef,
)

if TYPE_CHECKING:
    from .legacy_term_policy import LegacyTermFilterPort
    from .project_terminology_adapter import ProjectTerminologyAdapter

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProjectTerminologyBinding:
    """One Project/Variant-scoped read-only translation dependency."""

    adapter: ProjectTerminologyAdapter | None = None
    context: TerminologyLookupContext | None = None
    run_snapshot: FrozenTerminologyRunSnapshot | None = None
    legacy_term_filter: LegacyTermFilterPort | None = None

    def translator_kwargs(self) -> dict[str, object]:
        if self.adapter is None or self.context is None:
            return {}
        values: dict[str, object] = {
            "effective_terminology": self.adapter,
            "terminology_context": self.context,
        }
        if self.run_snapshot is not None:
            values["terminology_snapshot"] = self.run_snapshot.ref
        if self.legacy_term_filter is not None:
            values["legacy_term_filter"] = self.legacy_term_filter
        return values

    def term_database_kwargs(self) -> dict[str, object]:
        if self.adapter is None or self.context is None:
            return {}
        values: dict[str, object] = {
            "effective_loader": self.adapter,
            "terminology_context": self.context,
        }
        if self.legacy_term_filter is not None:
            values["legacy_term_filter"] = self.legacy_term_filter
        return values

    @property
    def snapshot_ref(self):
        return None if self.run_snapshot is None else self.run_snapshot.ref


def freeze_project_terminology(
    owner: object,
    snapshot_ref: TerminologyRunSnapshotRef | None = None,
) -> ProjectTerminologyBinding:
    """Capture the active line once and return a binding safe for every run stage."""

    identity = getattr(owner, "active_version_identity", None)
    factory = getattr(owner, "effective_terminology_factory", None)
    if not _valid_identity(identity) or factory is None:
        if snapshot_ref is not None:
            raise ValueError("checkpoint terminology snapshot cannot be restored without its Project/Variant")
        return ProjectTerminologyBinding()
    project_id, variant_id = identity
    try:
        create = getattr(factory, "effective_adapter", None)
        adapter = create(project_id, variant_id) if callable(create) else factory(project_id, variant_id)
    except Exception as exc:
        from transbridge.application.translation.terminology_run_snapshot import TerminologyRunSnapshotError

        raise TerminologyRunSnapshotError("effective terminology adapter could not be created") from exc
    if adapter is None:
        from transbridge.application.translation.terminology_run_snapshot import TerminologyRunSnapshotError

        raise TerminologyRunSnapshotError("effective terminology adapter is unavailable")
    source = _AdapterSnapshotSource(adapter)
    snapshot_factory = TerminologyRunSnapshotFactory(source)
    if snapshot_ref is not None:
        if (snapshot_ref.local_project_id, snapshot_ref.local_variant_id) != (project_id, variant_id):
            raise ValueError("checkpoint terminology snapshot belongs to another active Project/Variant")
        frozen = snapshot_factory.restore(snapshot_ref)
    else:
        frozen = snapshot_factory.freeze(project_id, variant_id)

    from .project_terminology_adapter import EnabledEffectiveTerminologyGate, ProjectTerminologyAdapter

    frozen_adapter = ProjectTerminologyAdapter(
        FrozenEffectiveTerminologyPort(frozen),
        EnabledEffectiveTerminologyGate(),
    )
    context = TerminologyLookupContext(project_id, variant_id, version_id=frozen.ref.version_id)
    legacy_filter = _freeze_legacy_filter(owner, frozen)
    return ProjectTerminologyBinding(frozen_adapter, context, frozen, legacy_filter)


@dataclass(frozen=True, slots=True)
class _AdapterSnapshotSource:
    adapter: object

    def snapshot(self, local_project_id: str, local_variant_id: str, version_id: str | None = None):
        from transbridge.application.terminology.effective import TerminologyLookupContext

        capture = getattr(self.adapter, "effective_snapshot", None)
        if not callable(capture):
            raise TypeError("effective terminology adapter does not expose snapshot capture")
        return capture(TerminologyLookupContext(local_project_id, local_variant_id, version_id=version_id))


def _freeze_legacy_filter(owner: object, frozen: FrozenTerminologyRunSnapshot):
    if frozen.ref.version_id is None:
        return None
    source = getattr(owner, "terminology_echo_links_factory", None)
    if source is None:
        effective_factory = getattr(owner, "effective_terminology_factory", None)
        if callable(getattr(effective_factory, "freeze_echo_links", None)):
            source = effective_factory
    if source is None:
        return None
    freeze = getattr(source, "freeze", None)
    if not callable(freeze):
        freeze = getattr(source, "freeze_echo_links", None)
    links = freeze(frozen.ref) if callable(freeze) else source(frozen.ref)
    if links is None:
        return None
    from .legacy_term_policy import ProjectTerminologyEchoFilter

    return ProjectTerminologyEchoFilter(frozen, links)


def resolve_project_terminology(owner: object) -> ProjectTerminologyBinding:
    """Build a binding from an AppContext-like object, otherwise preserve legacy behavior.

    The factory is deliberately injected into the context instead of discovered
    from process globals.  Smart Assistant's execution context delegates these
    two properties to the same AppContext, so GUI and Agent entrypoints consume
    exactly the same Project/Variant line and gate.
    """

    identity = getattr(owner, "active_version_identity", None)
    factory = getattr(owner, "effective_terminology_factory", None)
    if not _valid_identity(identity) or factory is None:
        return ProjectTerminologyBinding()
    project_id, variant_id = identity
    try:
        create = getattr(factory, "effective_adapter", None)
        adapter = create(project_id, variant_id) if callable(create) else factory(project_id, variant_id)
        if adapter is None:
            return ProjectTerminologyBinding()
        return ProjectTerminologyBinding(
            adapter=adapter,
            context=TerminologyLookupContext(project_id, variant_id),
        )
    except Exception:  # noqa: BLE001 - read-only terminology is an optional legacy-compatible layer
        logger.warning(
            "Project terminology is unavailable for %s/%s; using legacy terminology",
            project_id,
            variant_id,
            exc_info=True,
        )
        return ProjectTerminologyBinding()


def _valid_identity(value: object) -> bool:
    return (
        isinstance(value, tuple)
        and len(value) == 2
        and all(isinstance(item, str) and bool(item.strip()) for item in value)
    )


__all__ = ["ProjectTerminologyBinding", "freeze_project_terminology", "resolve_project_terminology"]
