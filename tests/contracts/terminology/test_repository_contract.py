from dataclasses import FrozenInstanceError, replace

import pytest

from transbridge.application.terminology.errors import (
    ActiveDraftError,
    CursorStaleError,
    DigestCollisionError,
    RepositoryConflictError,
    RevisionConflictError,
)
from transbridge.application.terminology.in_memory import InMemoryTerminologyRepository
from transbridge.application.terminology.models import (
    BuildCompleteness,
    BuildResult,
    BuildResultRef,
    BuildSummary,
    CanonicalDiff,
    DecisionStatus,
    DraftRef,
    ExtractionMethod,
    TermCandidate,
    TermDecision,
    TerminologyDraft,
    TerminologyVersion,
    TerminologyVersionRef,
    TermScope,
)
from transbridge.application.terminology.ports import PageRequest


def _candidate(number: int) -> TermCandidate:
    return TermCandidate(
        candidate_id=f"candidate-{number}",
        original=f"Original {number}",
        translation=f"译名 {number}",
        normalized_original=f"original {number}",
        normalized_translation=f"译名 {number}",
        evidence_ids=(f"evidence-{number}",),
        scope=TermScope.project(),
        extraction_method=ExtractionMethod.DETERMINISTIC_NAME,
        algorithm_version="v1",
    )


def _build(name: str = "one", *, candidates: tuple[TermCandidate, ...] = ()) -> BuildResult:
    return BuildResult(
        BuildResultRef(f"build:v1:{name}", f"content:v1:{name}"),
        "project-1",
        "variant-1",
        BuildSummary(1, len(candidates), len(candidates), 0),
        candidates=candidates,
    )


def _draft(draft_id: str = "draft-1", revision: int = 0, digest: str = "decisions-0") -> TerminologyDraft:
    return TerminologyDraft(DraftRef(draft_id, "project-1", "variant-1", None, "empty:v1", revision, digest))


def _version(build: BuildResult, version_id: str, parent: str | None = None) -> TerminologyVersion:
    ref = TerminologyVersionRef(version_id, build.project_id, build.variant_id, f"version-content:{version_id}")
    decision = TermDecision(
        term_id=f"term-{version_id}",
        project_id=build.project_id,
        variant_id=build.variant_id,
        original="Dragon",
        normalized_original="dragon",
        translation="龙",
        status=DecisionStatus.ADOPTED,
    )
    return TerminologyVersion(
        ref=ref,
        parent_version_id=parent,
        build_ref=build.ref,
        project_revision=1,
        variant_revision=1,
        completeness=BuildCompleteness.FULL,
        published_at="2026-08-28T00:00:00Z",
        decisions=(decision,),
        canonical_diff=CanonicalDiff(parent, version_id, f"diff:{version_id}", ()),
    )


class RepositoryContract:
    """Adapter-neutral semantics; persistent adapters should subclass this suite."""

    @pytest.fixture
    def repository(self):
        raise NotImplementedError

    def test_builds_are_immutable_and_digest_collisions_are_rejected(self, repository) -> None:
        build = _build()
        assert repository.put_build(build) == build.ref
        assert repository.get_build(build.ref) == build
        with pytest.raises(FrozenInstanceError):
            repository.get_build(build.ref).project_id = "changed"
        with pytest.raises(DigestCollisionError):
            repository.put_build(replace(build, summary=BuildSummary(2, 0, 0, 0)))

    def test_only_one_active_draft_exists_per_project_variant(self, repository) -> None:
        repository.create_draft(_draft())
        with pytest.raises(ActiveDraftError):
            repository.create_draft(_draft("draft-2"))

    def test_draft_updates_and_discard_use_expected_revision(self, repository) -> None:
        repository.create_draft(_draft())
        updated = _draft(revision=1, digest="decisions-1")
        with pytest.raises(RevisionConflictError):
            repository.update_draft(updated, expected_revision=3)
        assert repository.update_draft(updated, expected_revision=0) == updated.ref
        with pytest.raises(RevisionConflictError):
            repository.discard_draft("project-1", "variant-1", expected_revision=0)
        repository.discard_draft("project-1", "variant-1", expected_revision=1)
        assert repository.active_draft("project-1", "variant-1") is None

    def test_draft_identity_cannot_be_replaced_by_matching_revision(self, repository) -> None:
        repository.create_draft(_draft())
        replacement = _draft("draft-2", revision=1, digest="decisions-1")
        with pytest.raises(ActiveDraftError):
            repository.update_draft(replacement, expected_revision=0)

    def test_cursor_is_bound_to_query_and_snapshot(self, repository) -> None:
        build = _build(candidates=tuple(_candidate(number) for number in range(3)))
        repository.put_build(build)
        first = repository.list_candidates(build.ref, PageRequest(limit=1, query_fingerprint="sort:id"))

        assert [item.candidate_id for item in first.items] == ["candidate-0"]
        assert first.next_cursor is not None
        second = repository.list_candidates(
            build.ref,
            PageRequest(limit=1, cursor=first.next_cursor, query_fingerprint="sort:id"),
        )
        assert [item.candidate_id for item in second.items] == ["candidate-1"]
        with pytest.raises(CursorStaleError) as exc_info:
            repository.list_candidates(
                build.ref,
                PageRequest(limit=1, cursor=first.next_cursor, query_fingerprint="filter:other"),
            )
        assert exc_info.value.code == "CURSOR_STALE"

    def test_publish_is_atomic_against_expected_effective_pointer(self, repository) -> None:
        build = _build()
        repository.put_build(build)
        version_1 = _version(build, "version-1")
        repository.publish_version(version_1, expected_effective_version_id=None)
        version_2 = _version(build, "version-2", "version-1")

        with pytest.raises(RepositoryConflictError):
            repository.publish_version(version_2, expected_effective_version_id=None)
        assert repository.effective_version("project-1", "variant-1") == version_1
        repository.publish_version(version_2, expected_effective_version_id="version-1")
        assert repository.effective_version("project-1", "variant-1") == version_2

    def test_effective_pointer_isolated_by_variant_line(self, repository) -> None:
        first_build = _build()
        repository.put_build(first_build)
        repository.publish_version(_version(first_build, "version-a"), expected_effective_version_id=None)

        other_build = replace(
            _build("other"),
            variant_id="variant-2",
        )
        repository.put_build(other_build)
        other_version = _version(other_build, "version-b")
        repository.publish_version(other_version, expected_effective_version_id=None)

        assert repository.effective_version("project-1", "variant-1").ref.version_id == "version-a"
        assert repository.effective_version("project-1", "variant-2").ref.version_id == "version-b"


class TestInMemoryRepositoryContract(RepositoryContract):
    @pytest.fixture
    def repository(self) -> InMemoryTerminologyRepository:
        return InMemoryTerminologyRepository()
