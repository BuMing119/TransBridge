# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Apply a committed save batch to the working state that still owns it."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from transbridge.config.text_encoding import TextEncodings
from transbridge.persistence.v2.models import ProjectDto

from .models import ActiveProject, LifecycleSave


def project_needs_encoding_defaults(project: ProjectDto) -> bool:
    return not TextEncodings().to_project_data().keys() <= project.envelope.data.keys()


def project_encoding_settings(data: Mapping[str, str]) -> dict[str, str]:
    defaults = TextEncodings().to_project_data()
    return {key: data.get(key, default) for key, default in defaults.items()}


def _with_encoding_defaults(project: ProjectDto) -> ProjectDto:
    if not project_needs_encoding_defaults(project):
        return project
    data = project.envelope.data
    return replace(
        project,
        envelope=replace(project.envelope, data={**data, **project_encoding_settings(data)}),
    )


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
        project=_with_encoding_defaults(current.project),
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
        project=_with_encoding_defaults(current.project),
        variant=variant,
        persisted_project_revision=save.project.envelope.revision,
        persisted_variant_revision=save.variant.revision,
        source_additions=tuple(
            source for source in current.source_additions if (source.namespace, source.fingerprint) not in additions
        ),
    )
