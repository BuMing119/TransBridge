from dataclasses import replace

import pytest

from tests.application.terminology.story08_support import Permit, State, build, draft, expected
from transbridge.application.terminology.models import BuildCompleteness, BuildFreshness
from transbridge.application.terminology.publish import (
    PublishGuardRejectedError,
    PublishTerminologyRequest,
    VersionPublisher,
)
from transbridge.persistence.terminology.repository import SqliteTerminologyRepository


def _request():
    source = build()
    reviewed = draft()
    return (
        source,
        reviewed,
        PublishTerminologyRequest(
            project_id="project-1",
            variant_id="variant-1",
            expected=expected(),
            build_ref=source.ref,
            draft_ref=reviewed.ref,
            version_id="v1",
            published_at="2026-08-28T01:00:00+00:00",
        ),
    )


def test_formal_publish_uses_business_guards_without_a_release_feature_switch(tmp_path) -> None:
    source, reviewed, request = _request()
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    repository.put_build(source)
    repository.create_draft(reviewed)
    publisher = VersionPublisher(repository.publisher, State(expected()), Permit())

    result = publisher.publish(request)

    assert result.version_ref.version_id == "v1"
    assert repository.effective_version("project-1", "variant-1") is not None


@pytest.mark.parametrize(
    "unpublishable",
    [replace(build(), freshness=BuildFreshness.STALE), replace(build(), completeness=BuildCompleteness.PARTIAL)],
)
def test_stale_and_partial_builds_are_rejected(tmp_path, unpublishable) -> None:
    _, reviewed, request = _request()
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    repository.put_build(unpublishable)
    repository.create_draft(reviewed)
    publisher = VersionPublisher(repository.publisher, State(expected()), Permit())

    with pytest.raises((ValueError, RuntimeError)):
        publisher.publish(request)


def test_run_or_business_state_rejection_does_not_publish(tmp_path) -> None:
    source, reviewed, request = _request()
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    repository.put_build(source)
    repository.create_draft(reviewed)
    publisher = VersionPublisher(repository.publisher, State(expected()), Permit(False))

    with pytest.raises(PublishGuardRejectedError, match="effective version changed"):
        publisher.publish(replace(request, expected=replace(expected(), effective_version_id="other")))
    with pytest.raises(RuntimeError, match="run permit"):
        publisher.publish(request)
    assert repository.effective_version("project-1", "variant-1") is None


@pytest.mark.parametrize(
    ("request_change", "state_change", "message"),
    [
        ({"draft_ref": replace(draft().ref, revision=1)}, {}, "draft is absent or changed"),
        ({}, {"draft_revision": 1}, "expected draft state"),
        ({}, {"base_version_id": "other"}, "draft base"),
    ],
)
def test_draft_revision_and_base_guards_reject_concurrent_state(
    tmp_path,
    request_change,
    state_change,
    message,
) -> None:
    source, reviewed, request = _request()
    guarded_state = replace(expected(), **state_change)
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    repository.put_build(source)
    repository.create_draft(reviewed)
    publisher = VersionPublisher(repository.publisher, State(guarded_state), Permit())

    with pytest.raises(RuntimeError, match=message):
        publisher.publish(replace(request, expected=guarded_state, **request_change))

    assert repository.effective_version("project-1", "variant-1") is None
