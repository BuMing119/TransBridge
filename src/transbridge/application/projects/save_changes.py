"""Apply a committed save batch to the working state that still owns it."""

from __future__ import annotations

from dataclasses import replace

from .models import ActiveProject, LifecycleSave


def acknowledge_saved_changes(current: ActiveProject, save: LifecycleSave) -> ActiveProject:
    saved_revision = None if save.variant is None else save.variant.revision
    if current.variant is not None and save.variant is not None:
        current.variant.acknowledge_changes(
            saved_revision,
            save.project.envelope.revision,
            saved_snapshot=save.variant,
            applications=save.applications,
        )
    additions = {(source.namespace, source.fingerprint) for source in save.source_additions}
    return replace(
        current,
        persisted_project_revision=max(current.persisted_project_revision, save.project.envelope.revision),
        persisted_variant_revision=saved_revision,
        source_additions=tuple(
            source for source in current.source_additions if (source.namespace, source.fingerprint) not in additions
        ),
    )


def rebase_committed_variant_save(
    current: ActiveProject, save: LifecycleSave, observed_revision: int, observed_entries
) -> ActiveProject:
    assert current.variant is not None and save.variant is not None
    variant = current.variant.rebase_committed_save(
        save.variant, save.project.envelope.revision, observed_revision, observed_entries, save.applications
    )
    additions = {(source.namespace, source.fingerprint) for source in save.source_additions}
    return replace(
        current,
        variant=variant,
        persisted_project_revision=save.project.envelope.revision,
        persisted_variant_revision=save.variant.revision,
        source_additions=tuple(
            source for source in current.source_additions if (source.namespace, source.fingerprint) not in additions
        ),
    )
