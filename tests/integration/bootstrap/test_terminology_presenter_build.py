from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace

import pytest

from tests.application.terminology.test_build import _entry
from tests.integration.bootstrap.test_terminology_build_review import _Harness
from transbridge.application.contracts import Diagnostic, OperationCounts, OperationOutcome, OperationResult
from transbridge.application.terminology.models import DecisionStatus
from transbridge.bootstrap.terminology_build import BuildRunner
from transbridge.persistence.terminology.repository import SqliteTerminologyRepository
from transbridge.ui.tools.terminology.presenter import (
    TerminologyPreflightError,
    TerminologyPresenter,
    TerminologyUiServices,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    harness = _Harness(repository, monkeypatch)
    presenter = TerminologyPresenter(
        TerminologyUiServices(build_inputs=harness, commands=harness.commands), harness.context
    )
    try:
        yield harness, presenter
    finally:
        presenter.close()
        repository.close()


def _finish_build(harness):
    request = harness.submit.request
    execution = BuildRunner(harness.repositories, harness.state, None, harness.ids, harness.lifecycle)(
        request, harness.runtime
    )
    return harness.commit.commit_if_current(request.expected, execution.mutation)


def test_consecutive_presenter_builds_capture_the_current_draft(workflow):
    harness, presenter = workflow
    assert presenter.preflight().ready
    presenter.start_build()
    assert _finish_build(harness).current
    first = harness.repository.active_draft("project-1", "main")
    harness.commands.apply_decision(
        "change_translation", harness.context, term_id=first.decisions[0].term_id, translation="人工译名"
    )

    presenter.start_build()

    assert presenter.snapshot.draft_id == first.ref.draft_id
    assert presenter.snapshot.draft_revision == first.ref.revision + 1
    assert _finish_build(harness).current
    second = harness.repository.active_draft("project-1", "main")
    assert second.ref.draft_id == first.ref.draft_id
    assert second.decisions[0].translation == "人工译名"
    assert second.decisions[0].is_effective


def test_presenter_rebuild_captures_new_sources_without_manual_preflight(workflow):
    harness, presenter = workflow
    presenter.start_build()
    assert _finish_build(harness).current
    harness.parser.entries["source-a"] += (_entry("source-a", "two", "Sword", "剑"),)
    harness.change_source()

    presenter.start_build()

    assert presenter.snapshot.sources == harness.snapshot.sources
    assert _finish_build(harness).current
    draft = harness.repository.active_draft("project-1", "main")
    assert {item.original for item in draft.decisions} == {"Dragon", "Sword"}


def test_source_change_after_fresh_capture_still_rejects_the_build_commit(workflow):
    harness, presenter = workflow
    presenter.start_build()
    assert _finish_build(harness).current
    original = harness.repository.active_draft("project-1", "main")
    presenter.start_build()
    harness.change_source()

    rejected = _finish_build(harness)

    assert not rejected.current
    assert rejected.diagnostic == "TERMINOLOGY_SOURCE_CHANGED"
    assert harness.repository.active_draft("project-1", "main") == original


def test_refresh_and_build_keep_an_owned_copy_of_the_selected_configuration(workflow):
    harness, _presenter = workflow
    captures = []

    def capture(context, *, config):
        captures.append(deepcopy(config))
        result = harness.capture_build_input(context, config=config)
        digest = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        config["matching"]["mode"] = "changed-by-provider"
        return OperationResult.completed(replace(result.value, config_digest=digest))

    presenter = TerminologyPresenter(
        TerminologyUiServices(build_inputs=SimpleNamespace(capture_build_input=capture), commands=harness.commands),
        harness.context,
    )
    config = {"matching": {"mode": "exact"}}
    assert presenter.preflight(config=config).ready
    expected_digest = presenter.snapshot.config_digest
    config["matching"]["mode"] = "changed-by-caller"
    assert presenter.preflight().ready

    presenter.start_build()

    assert captures == [{"matching": {"mode": "exact"}}] * 3
    assert presenter.snapshot.config_digest == expected_digest
    assert harness.submit.request.config_digest == expected_digest
    assert _finish_build(harness).current
    presenter.close()


def test_failed_recapture_drops_old_snapshot_and_keeps_validation_context(workflow, monkeypatch):
    harness, presenter = workflow
    assert presenter.preflight().ready
    failure = OperationResult(
        OperationOutcome.FAILED,
        counts=OperationCounts(failed=1),
        diagnostics=(
            Diagnostic(
                "TERMINOLOGY_SOURCE_REQUIRED",
                "source-a is no longer enabled",
                details=(("source_id", "source-a"),),
            ),
        ),
    )
    monkeypatch.setattr(harness, "capture_build_input", lambda context, *, config: failure)

    with pytest.raises(TerminologyPreflightError) as caught:
        presenter.start_build()

    assert presenter.snapshot is None
    assert not hasattr(harness.submit, "request")
    assert caught.value.code == "TERMINOLOGY_SOURCE_REQUIRED"
    assert not caught.value.state.ready
    assert {item.label: item.value for item in caught.value.state.technical_details} == {
        "诊断代码": "TERMINOLOGY_SOURCE_REQUIRED",
        "诊断信息": "source-a is no longer enabled",
        "source_id": "source-a",
    }
    assert "登记并启用" in str(caught.value)


def test_same_translation_confirms_a_review_required_term_and_can_publish(workflow):
    harness, presenter = workflow
    presenter.start_build()
    assert _finish_build(harness).current
    harness.parser.entries["source-a"] = (_entry("source-a", "one", "Dragon", "飞龙"),)
    harness.change_source()
    presenter.start_build()
    assert _finish_build(harness).current
    review = harness.repository.active_draft("project-1", "main").decisions[0]
    assert review.status is DecisionStatus.REVIEW_REQUIRED

    confirmed = presenter.change_translation(review.term_id, review.translation)

    assert confirmed.decisions[0].status is DecisionStatus.MANUAL_CONFIRMED
    assert confirmed.decisions[0].translation == "龙"
    published = harness.publish()
    assert published.decisions[0].translation == "龙"
    assert published.decisions[0].is_effective
