import time

from PyQt6.QtWidgets import QApplication, QDialog
import pytest

from tests.contracts.terminology.test_runtime import _runtime
from tests.integration.bootstrap.test_terminology_build_review import _Harness
from transbridge.application.terminology.runtime import TerminologyTaskEntrypoint, TerminologyWorkloadRegistry
from transbridge.application.terminology.workloads import TerminologyWorkloadType
from transbridge.bootstrap.terminology_publication import PublishRunner
from transbridge.bootstrap.terminology_workloads import ProductionTerminologyCommands
from transbridge.persistence.terminology.repository import SqliteTerminologyRepository
from transbridge.ui.tools.terminology.presenter import TerminologyPresenter, TerminologyUiServices
from transbridge.ui.tools.terminology.window import TerminologyWindow

_APP = QApplication.instance() or QApplication([])


@pytest.mark.parametrize("change_after_review", [False, True])
def test_window_review_to_runtime_publication_refreshes_or_reports_stale(tmp_path, monkeypatch, change_after_review):
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    harness = _Harness(repository, monkeypatch)
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
    inspected = []

    def review(dialog):
        inspected.append(dialog.changes.model.rowCount())
        if change_after_review:
            commands.apply_decision(
                "change_translation", harness.context, term_id=draft.decisions[0].term_id, translation="后来修改"
            )
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr("transbridge.ui.tools.terminology.version_interactions.ChangeReviewDialog.exec", review)
    presenter = TerminologyPresenter(
        TerminologyUiServices(build_inputs=harness, commands=commands, queries=repository, runtime=runtime),
        harness.context,
    )
    window = TerminologyWindow(presenter)
    try:
        window._publish()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            _APP.processEvents()
            message = window.operation_notice.message.text()
            if "新术语版本已发布" in message or "操作未完成" in message:
                break
            time.sleep(0.005)
        assert inspected == [1]
        if change_after_review:
            assert repository.effective_version("project-1", "main") is None
            assert repository.active_draft("project-1", "main").decisions[0].translation == "后来修改"
            assert "重新检查" in window.operation_notice.message.text()
        else:
            published = repository.effective_version("project-1", "main")
            assert published is not None
            assert published.ref.version_id in window.versions_view.current_version.text()
            assert repository.active_draft("project-1", "main") is None
            assert window.draft_model.rowCount() == 0
            assert "新术语版本已发布" in window.operation_notice.message.text()
    finally:
        window.close()
        window._actions._pool.waitForDone(3000)
        repository.close()
