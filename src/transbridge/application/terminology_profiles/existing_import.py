# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Merge source terms into an existing profile using the shared import policy."""

from dataclasses import replace

from .importing import TerminologyProfileImportService
from .models import TerminologyProfileContent


def preview_existing_import(service, project_id, variant_id, base, source, profile_id, *, policy):
    profile = service.get_profile(profile_id)
    if profile.project_id != project_id:
        raise ValueError("目标方案不属于当前工程。")
    preview = TerminologyProfileImportService(service).preview(
        project_id, variant_id, base, source, policy=policy, existing_mappings=profile.draft.mappings
    )
    previous = {item.term_key: item for item in profile.draft.mappings}
    # Preview shows the current translation as the comparison baseline. Persist
    # the original authority metadata, including scheme-owned terms, unchanged.
    content = TerminologyProfileContent(
        mappings=tuple(
            replace(previous[item.term_key], translation=item.translation) if item.term_key in previous else item
            for item in preview.content.mappings
        ),
        overrides=profile.draft.overrides,
        bindings=profile.draft.bindings,
    )
    publication = service.preview_publication(profile_id, content, expected_revision=profile.draft_revision)
    return replace(preview, target_publication=publication)
