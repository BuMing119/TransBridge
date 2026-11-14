from dataclasses import replace
from types import SimpleNamespace

from tests.contracts.terminology.test_repository_contract import _build, _candidate, _draft, _version
from transbridge.application.terminology.models import (
    BuildSummary,
    ConflictGroup,
    ConflictStatus,
    ConflictVariant,
    DecisionStatus,
    ExtractionMethod,
)
from transbridge.persistence.terminology import SqliteTerminologyRepository
from transbridge.persistence.terminology.workbench_status import read_workbench_status
from transbridge.ui.tools.terminology.asset_snapshot import WorkbenchAssets, read_assets
from transbridge.ui.tools.terminology.build_projection import project_build
from transbridge.ui.tools.terminology.workflow_state import TerminologyWorkflowState


def test_summary_matches_full_snapshots_without_domain_decoding(tmp_path, monkeypatch):
    repository = SqliteTerminologyRepository.open(tmp_path, "project-1")
    try:
        conflict = ConflictGroup(
            "conflict-1",
            "project-1",
            "variant-1",
            "dragon",
            (
                ConflictVariant("龙", ("candidate-0",), ("evidence-0",)),
                ConflictVariant("巨龙", ("candidate-1",), ("evidence-1",)),
            ),
        )
        build = replace(
            _build(candidates=(_candidate(0), replace(_candidate(1), extraction_method=ExtractionMethod.LLM_TEXT))),
            conflicts=(conflict,),
            summary=BuildSummary(1, 0, 2, 1),
            diagnostics=("LLM_NO_TEXT",),
        )
        repository.put_build(build)
        version = _version(build, "v1")
        repository.publish_version(version, expected_effective_version_id=None)
        old = version.decisions[0]
        draft = replace(
            _draft(),
            ref=replace(
                _draft().ref, base_version_id=version.ref.version_id, base_content_digest=version.ref.content_digest
            ),
            decisions=(
                old,
                replace(
                    old, term_id="new", normalized_original="new", original="New", status=DecisionStatus.REVIEW_REQUIRED
                ),
            ),
        )
        repository.create_draft(draft)

        def forbidden(*_args, **_kwargs):
            raise AssertionError("status must not decode a complete snapshot")

        monkeypatch.setattr(repository, "_decode", forbidden)
        status = read_workbench_status(repository._connection, "project-1", "variant-1")
        assert status.build.ref == build.ref
        assert status.build.direct_count == status.build.extracted_count == 1
        assert status.draft.decision_count == 2
        assert status.draft.added_count == status.draft.unchanged_count == 1
        assert status.review_counts.review_required_terms == 1
        assert status.review_counts.unresolved_conflicts == 1
        assets = WorkbenchAssets(
            build=build, draft=draft, version=version, version_ref=version.ref, review_counts=status.review_counts
        )
        projected = replace(assets, build=status.build, draft=status.draft, version=None)
        assert TerminologyWorkflowState.from_assets(projected) == TerminologyWorkflowState.from_assets(assets)
        assert project_build(status.build) == project_build(build)
    finally:
        repository.close()


def test_resolution_overlay_and_missing_assets(tmp_path):
    repository = SqliteTerminologyRepository.open(tmp_path, "project-1")
    try:
        empty = read_workbench_status(repository._connection, "project-1", "variant-1")
        assert empty.build is empty.draft is empty.version_ref is None
        assert empty.review_counts.total == 0
        conflict = ConflictGroup(
            "c",
            "project-1",
            "variant-1",
            "dragon",
            (
                ConflictVariant("龙", ("a",), ("e1",)),
                ConflictVariant("巨龙", ("b",), ("e2",)),
            ),
        )
        build = replace(_build(), conflicts=(conflict,))
        repository.put_build(build)
        version = replace(
            _version(build, "v1"),
            conflicts=(replace(conflict, status=ConflictStatus.UNIFIED, recommended_translation="龙"),),
        )
        repository.publish_version(version, expected_effective_version_id=None)
        assert read_workbench_status(repository._connection, "project-1", "variant-1").review_counts.total == 0
        repository.create_draft(
            replace(
                _draft(),
                ref=replace(
                    _draft().ref, base_version_id=version.ref.version_id, base_content_digest=version.ref.content_digest
                ),
                conflict_resolutions=(),
            )
        )
        assert (
            read_workbench_status(repository._connection, "project-1", "variant-1").review_counts.unresolved_conflicts
            == 1
        )
    finally:
        repository.close()


def test_asset_read_uses_summary_without_legacy_full_reads():
    from transbridge.application.contracts import RequestContext
    from transbridge.application.terminology.conflict_review import TerminologyReviewCounts
    from transbridge.application.terminology.workbench_status import WorkbenchStatus

    status = WorkbenchStatus(None, None, None, TerminologyReviewCounts())

    def forbidden(*_args):
        raise AssertionError("full read")

    commands = SimpleNamespace(
        workbench_status=lambda context: status,
        latest_build_result=forbidden,
        active_draft=forbidden,
        latest_version=forbidden,
        review_counts=forbidden,
    )
    assets = read_assets(commands, RequestContext("test", project_id="p", variant_id="v"))
    assert assets.review_counts == status.review_counts
