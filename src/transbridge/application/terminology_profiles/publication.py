"""Reviewable profile publication, with storage-enforced review preconditions."""

from __future__ import annotations

from dataclasses import dataclass

from .models import (
    ProfileState,
    ProfileTermMapping,
    PublishedTerminologyProfile,
    TerminologyProfileContent,
    TerminologyProfileSelection,
)


@dataclass(frozen=True, slots=True)
class ProfileMappingChange:
    kind: str
    before: ProfileTermMapping | None
    after: ProfileTermMapping | None


@dataclass(frozen=True, slots=True)
class ProfilePublicationPreview:
    project_id: str
    profile_id: str
    name: str
    draft_revision: int
    published_revision: int | None
    content: TerminologyProfileContent
    changes: tuple[ProfileMappingChange, ...]
    advanced_changed: bool
    selections: tuple[TerminologyProfileSelection, ...]
    target_variant_id: str | None = None
    target_selection: TerminologyProfileSelection | None = None
    previous_name: str | None = None

    @property
    def has_changes(self) -> bool:
        return (
            self.published_revision is None
            or bool(self.changes)
            or self.advanced_changed
            or self.previous_name != self.name
        )


def preview_publication(repository, profile_id, content, *, expected_revision, target_variant_id=None):
    profile = repository.get_profile(profile_id)
    if profile is None or profile.state is ProfileState.ARCHIVED:
        raise ValueError("方案已不存在或已归档，请重新打开方案。")
    if profile.draft_revision != expected_revision:
        raise ValueError("方案草稿已变化，请重新载入后查看发布预览。")
    published = (
        None
        if profile.latest_published_revision is None
        else repository.get_published(profile_id, profile.latest_published_revision)
    )
    if profile.latest_published_revision is not None and published is None:
        raise ValueError("方案的已发布版本不可用，请重新载入。")
    previous = TerminologyProfileContent() if published is None else published.content
    before = {item.term_key: item for item in previous.mappings}
    after = {item.term_key: item for item in content.mappings}
    changes = tuple(
        ProfileMappingChange(
            "新增" if key not in before else "移除" if key not in after else "修改",
            before.get(key),
            after.get(key),
        )
        for key in sorted(before.keys() | after.keys())
        if before.get(key) != after.get(key)
    )
    return ProfilePublicationPreview(
        profile.project_id,
        profile.profile_id,
        profile.name,
        profile.draft_revision,
        profile.latest_published_revision,
        content,
        changes,
        previous.overrides != content.overrides or previous.bindings != content.bindings,
        repository.list_profile_selections(profile_id),
        target_variant_id,
        None if target_variant_id is None else repository.get_selection(profile.project_id, target_variant_id),
        None if published is None else published.name,
    )


def commit_publication(repository, preview: ProfilePublicationPreview, *, published_at: str):
    """Called by the repository inside its write lock and transaction.

    All review inputs are checked before any write. The repository's enclosing
    transaction also covers saving the draft, publication and optional adoption.
    """
    profile = repository.get_profile(preview.profile_id)
    if (
        profile is None
        or profile.state is ProfileState.ARCHIVED
        or profile.project_id != preview.project_id
        or profile.name != preview.name
        or profile.draft_revision != preview.draft_revision
        or profile.latest_published_revision != preview.published_revision
    ):
        raise ValueError("方案在预览后已变化，请重新查看发布预览。")
    if repository.list_profile_selections(preview.profile_id) != preview.selections:
        raise ValueError("使用此方案的翻译版本在预览后已变化，请重新查看影响范围。")
    if preview.target_variant_id is not None and (
        repository.get_selection(preview.project_id, preview.target_variant_id) != preview.target_selection
    ):
        raise ValueError("当前翻译版本的方案选择在预览后已变化，请重新确认。")
    if profile.draft != preview.content:
        profile = repository.save_draft(
            profile.profile_id,
            preview.content,
            expected_revision=preview.draft_revision,
            updated_at=published_at,
        )
    if preview.has_changes:
        published = PublishedTerminologyProfile(
            profile_id=profile.profile_id,
            project_id=profile.project_id,
            revision=(profile.latest_published_revision or 0) + 1,
            name=profile.name,
            content_digest=preview.content.content_digest,
            content=preview.content,
            published_at=published_at,
        )
        repository.insert_published(published, expected_draft_revision=profile.draft_revision)
    else:
        published = repository.get_published(profile.profile_id, profile.latest_published_revision)
        if published is None:
            raise ValueError("方案的已发布版本不可用，请重新载入。")
    if preview.target_variant_id is not None:
        repository.set_selection(
            TerminologyProfileSelection(
                profile.project_id, preview.target_variant_id, profile.profile_id, published.revision, published_at
            )
        )
    return published
