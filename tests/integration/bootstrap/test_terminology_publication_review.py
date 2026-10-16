from dataclasses import replace
import hashlib
import json

import pytest

from tests.contracts.terminology.test_runtime import _runtime
from tests.integration.bootstrap.test_terminology_build_review import _Harness
from tests.integration.bootstrap.test_terminology_presenter_build import _finish_build
from transbridge.application.contracts import OperationResult
from transbridge.application.tasks import JobState
from transbridge.application.terminology.publish import PublishGuardRejectedError
from transbridge.application.terminology.runtime import TerminologyTaskEntrypoint, TerminologyWorkloadRegistry
from transbridge.application.terminology.workloads import TerminologyWorkloadType
from transbridge.bootstrap.terminology_context import task_owner
from transbridge.bootstrap.terminology_publication import PublishRunner
from transbridge.bootstrap.terminology_workloads import ProductionTerminologyCommands
from transbridge.persistence.terminology.repository import SqliteTerminologyRepository
from transbridge.ui.tools.terminology.presenter import TerminologyPresenter, TerminologyUiServices

pytestmark = pytest.mark.integration


@pytest.fixture
def harness(tmp_path, monkeypatch):
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    try:
        yield _Harness(repository, monkeypatch)
    finally:
        repository.close()


def _finish_publish(harness):
    request = harness.submit.request
    execution = PublishRunner(harness.repositories, harness.state)(request, harness.runtime)
    return harness.commit.commit_if_current(request.expected, execution.mutation)


def test_preview_is_read_only_and_task_runtime_publishes_the_exact_reviewed_version(harness):
    draft = harness.build()
    runtime = _runtime()
    registry = TerminologyWorkloadRegistry()
    registry.bind(TerminologyWorkloadType.PUBLISH, PublishRunner(harness.repositories, harness.state))
    tasks = TerminologyTaskEntrypoint(runtime, registry, harness.commit)
    commands = ProductionTerminologyCommands(
        tasks,
        harness.repositories,
        None,
        harness.state,
        harness.ids,
        harness.commands._clock,
        harness,
        harness.lifecycle,
    )

    preview = commands.preview_publish(harness.context)

    assert preview.draft_ref == draft.ref
    assert preview.diff.changes
    assert harness.repository.effective_version("project-1", "main") is None
    assert harness.repository.active_draft("project-1", "main") == draft
    submitted = commands.publish_preview(preview, harness.context)
    assert runtime.get(submitted, task_owner(harness.context)).state is JobState.COMPLETED
    assert commands.task_result(submitted.run_id, harness.context).committed
    assert commands.task_failure(submitted.run_id, harness.context) is None
    assert harness.repository.effective_version("project-1", "main") == preview.candidate_version
    assert harness.repository.active_draft("project-1", "main") is None


@pytest.mark.parametrize("change", ["draft", "source", "build"])
def test_preview_rejects_changed_inputs_before_task_submission(harness, change):
    draft = harness.build()
    preview = harness.commands.preview_publish(harness.context)
    if change == "draft":
        harness.commands.apply_decision(
            "change_translation", harness.context, term_id=draft.decisions[0].term_id, translation="新译名"
        )
    elif change == "source":
        harness.change_source()
    else:
        harness.build()
    previous_request = harness.submit.request

    with pytest.raises(PublishGuardRejectedError, match="重新检查"):
        harness.commands.publish_preview(preview, harness.context)

    assert harness.submit.request is previous_request
    assert harness.repository.effective_version("project-1", "main") is None


def test_reviewed_draft_changed_after_submission_is_not_published(harness):
    draft = harness.build()
    preview = harness.commands.preview_publish(harness.context)
    harness.commands.publish_preview(preview, harness.context)
    harness.commands.apply_decision(
        "change_translation", harness.context, term_id=draft.decisions[0].term_id, translation="提交后修改"
    )

    result = _finish_publish(harness)

    assert not result.current
    assert harness.repository.effective_version("project-1", "main") is None
    assert harness.repository.active_draft("project-1", "main").decisions[0].translation == "提交后修改"


def test_preview_rejects_another_line_and_modified_candidate(harness):
    harness.build()
    preview = harness.commands.preview_publish(harness.context)
    with pytest.raises(PermissionError):
        harness.commands.publish_preview(preview, replace(harness.context, variant_id="other"))
    modified = replace(preview, candidate_version=replace(preview.candidate_version, published_at="modified"))
    with pytest.raises(PublishGuardRejectedError, match="预览不一致"):
        harness.commands.publish_preview(modified, harness.context)


def test_presenter_preview_and_publish_use_the_original_non_default_config(harness, monkeypatch):
    capture = harness.capture_build_input

    def configured_capture(context, *, config):
        result = capture(context, config=config)
        digest = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        return OperationResult.completed(replace(result.value, config_digest=digest))

    monkeypatch.setattr(harness, "capture_build_input", configured_capture)
    presenter = TerminologyPresenter(
        TerminologyUiServices(build_inputs=harness, commands=harness.commands), harness.context
    )
    config = {"matching": {"case_sensitive": True}}
    presenter.preflight(config=config)
    presenter.start_build()
    assert _finish_build(harness).current

    preview = presenter.preview_publish()
    config["matching"]["case_sensitive"] = False
    presenter.publish_preview(preview)

    assert json.loads(preview.config_json) == {"matching": {"case_sensitive": True}}
    assert _finish_publish(harness).current
    assert harness.repository.effective_version("project-1", "main") == preview.candidate_version
