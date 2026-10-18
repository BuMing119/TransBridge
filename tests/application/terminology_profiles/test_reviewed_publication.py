from dataclasses import replace
import sqlite3

import pytest

from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology.models import DecisionStatus, TermDecision, TermScope
from transbridge.application.terminology_profiles import (
    InMemoryTerminologyProfileRepository,
    ProfileTermMapping,
    TerminologyProfileConflictError,
    TerminologyProfileContent,
    TerminologyProfileService,
)
from transbridge.persistence.terminology.profiles import SqliteTerminologyProfileRepository
from transbridge.persistence.terminology.schema import initialize_schema


@pytest.fixture(params=("memory", "sqlite"))
def profiles(request):
    connection = None
    if request.param == "sqlite":
        connection = sqlite3.connect(":memory:", isolation_level=None)
        initialize_schema(connection)
        repository = SqliteTerminologyProfileRepository(connection)
    else:
        repository = InMemoryTerminologyProfileRepository()
    service = TerminologyProfileService(repository)
    original = service.create_with_content("project", "经典", _content("甲"))
    service.publish(original.profile_id, expected_draft_revision=0)
    service.select("project", "first", original.profile_id)
    yield service, repository, original
    if connection is not None:
        connection.close()


def _content(target):
    return TerminologyProfileContent((ProfileTermMapping("A", target, "原甲"),))


def test_preview_does_not_save_or_publish_and_describes_exact_changes(profiles):
    service, repository, original = profiles
    content = TerminologyProfileContent((ProfileTermMapping("A", "改甲", "原甲"), ProfileTermMapping("B", "乙")))
    preview = service.preview_publication(original.profile_id, content, expected_revision=0)
    assert [(item.kind, (item.after or item.before).original) for item in preview.changes] == [
        ("修改", "A"),
        ("新增", "B"),
    ]
    assert tuple(item.variant_id for item in preview.selections) == ("first",)
    assert repository.get_profile(original.profile_id).draft == original.draft
    assert service.selected_revision("project", "first").revision == 1


def test_reviewed_publish_keeps_selection_and_adoption_is_explicit(profiles):
    service, repository, original = profiles
    second = service.create_with_content("project", "新版", _content("新版甲"))
    preview = service.preview_publication(second.profile_id, second.draft, expected_revision=0)
    service.publish_reviewed(preview)
    assert service.selected_revision("project", "first").profile_id == original.profile_id
    adopt = service.preview_publication(second.profile_id, second.draft, expected_revision=0, target_variant_id="first")
    assert not adopt.has_changes
    adopted = service.publish_reviewed(adopt)
    assert adopted.revision == 1
    assert repository.get_profile(second.profile_id).latest_published_revision == 1
    assert service.selected_revision("project", "first").profile_id == second.profile_id


@pytest.mark.parametrize("change", ("draft", "publish", "users", "target", "rename", "archive"))
def test_changed_review_inputs_reject_without_saving_local_edits(profiles, change):
    service, repository, original = profiles
    preview = service.preview_publication(
        original.profile_id, _content("本地改甲"), expected_revision=0, target_variant_id="current"
    )
    if change == "draft":
        service.save_draft(original.profile_id, _content("他人改甲"), expected_revision=0)
    elif change == "publish":
        service.publish(original.profile_id, expected_draft_revision=0)
    elif change == "users":
        service.select("project", "another", original.profile_id)
    elif change == "target":
        other = service.create_with_content("project", "其他", _content("其他甲"))
        service.publish(other.profile_id, expected_draft_revision=0)
        service.select("project", "current", other.profile_id)
    elif change == "rename":
        service.rename(original.profile_id, "已重命名")
    else:
        service.archive(original.profile_id)
    before = repository.get_profile(original.profile_id)
    selections = repository.list_profile_selections(original.profile_id)
    with pytest.raises(TerminologyProfileConflictError, match="变化"):
        service.publish_reviewed(preview)
    assert repository.get_profile(original.profile_id) == before
    assert repository.list_profile_selections(original.profile_id) == selections


def test_failed_adoption_rolls_back_draft_publication_and_all_users(profiles, monkeypatch):
    service, repository, original = profiles
    preview = service.preview_publication(
        original.profile_id, _content("新版甲"), expected_revision=0, target_variant_id="current"
    )
    before = repository.get_profile(original.profile_id)
    existing = service.selected_revision("project", "first")

    def fail(_selection):
        raise ValueError("模拟选择写入失败")

    monkeypatch.setattr(repository, "set_selection", fail)
    with pytest.raises(TerminologyProfileConflictError, match="模拟选择"):
        service.publish_reviewed(preview)
    assert repository.get_profile(original.profile_id) == before
    assert service.selected_revision("project", "first") == existing
    assert repository.get_published(original.profile_id, 2) is None


def test_name_only_change_is_reviewed_and_published_without_mutating_history(profiles):
    service, _repository, original = profiles
    historical = service.selected_revision("project", "first")
    service.rename(original.profile_id, "新版名称")
    preview = service.preview_publication(
        original.profile_id, original.draft, expected_revision=0, target_variant_id="first"
    )
    assert preview.has_changes
    assert preview.changes == ()
    assert preview.previous_name == historical.name == "经典"
    assert preview.name == "新版名称"
    adopted = service.publish_reviewed(preview)
    assert adopted.name == "新版名称"
    assert adopted.revision == 2
    assert adopted.content_digest == historical.content_digest
    assert service.selected_revision("project", "first") == adopted
    assert service.published_revision(original.profile_id, 1) == historical
    repeated = service.preview_publication(original.profile_id, original.draft, expected_revision=0)
    assert not repeated.has_changes


def test_base_creation_copies_every_effective_scope_without_publishing_or_changing_base(profiles):
    service, _repository, original = profiles
    snapshot = EffectiveTerminologySnapshot(
        "project",
        "first",
        EffectiveSnapshotStatus.READY,
        version_id="base-1",
        content_digest="a" * 64,
        decisions=(
            TermDecision("a", "project", "first", "A", "a", "项目甲", TermScope.project(), DecisionStatus.ADOPTED),
            TermDecision(
                "b", "project", "first", "A", "a", "插件甲", TermScope.plugin("A.esp"), DecisionStatus.ADOPTED
            ),
            TermDecision(
                "c", "project", "first", "B", "b", "待审乙", TermScope.project(), DecisionStatus.REVIEW_REQUIRED
            ),
        ),
    )
    created = service.create_from_project_terminology("project", "first", "项目默认译名副本", snapshot)
    assert len(created.draft.mappings) == 2
    assert {item.translation for item in created.draft.mappings} == {"项目甲", "插件甲"}
    assert all(item.translation == item.base_translation for item in created.draft.mappings)
    assert created.latest_published_revision is None
    assert service.selected_revision("project", "first").profile_id == original.profile_id
    assert snapshot.decisions[0].translation == "项目甲"
    with pytest.raises(ValueError, match="已变化"):
        service.create_from_project_terminology("project", "other", "错误上下文", snapshot)
    with pytest.raises(ValueError, match="没有已发布的有效术语"):
        service.create_from_project_terminology("project", "first", "空方案", replace(snapshot, decisions=()))
