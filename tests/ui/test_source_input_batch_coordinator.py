"""Keep batch source confirmation bound to its original project and source set."""

from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication, QWidget
import pytest

from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult
from transbridge.application.projects.storage_contracts import ProjectReadToken
from transbridge.bootstrap.runtime import UseCaseRegistry
from transbridge.persistence.v2.ids import ProjectId, VariantId
from transbridge.ui.coordinators.source_input_batch import SourceInputBatchCoordinator
from transbridge.ui.coordinators.source_input_coordinator import SourceInputCoordinator

_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def batch():
    host = QWidget()
    host.close_pending = False
    host.context = SimpleNamespace(
        active_version_identity=("project", "variant"),
        slots={
            "ready": SimpleNamespace(label="Ready.esp", collection=[object()], source_snapshot=object()),
            "missing": SimpleNamespace(label="Missing.esp", collection=[object()], source_snapshot=None),
        },
    )
    inputs = SimpleNamespace(_busy=False, resolved_keys=lambda keys: tuple(keys))
    subject = SourceInputBatchCoordinator(host, inputs)
    yield subject
    if subject.dialog is not None:
        subject.dialog.reject()
    host.close()
    host.deleteLater()
    _APP.processEvents()


@pytest.mark.parametrize("invalidate", ["project_switch", "host_close"])
def test_confirmed_scope_does_not_resume_after_owner_changes_before_queued_callback(batch, invalidate):
    resumed = []
    assert not batch.ensure(
        ("ready", "missing"), resume=lambda: None, on_scope_ready=resumed.append, allow_partial=True
    )
    batch._continue(("ready",))
    assert batch.dialog is None and resumed == []
    if invalidate == "project_switch":
        batch.host.context.active_version_identity = ("another-project", "variant")
    else:
        batch.host.close_pending = True
    _APP.processEvents()
    assert resumed == []


def test_disappeared_original_source_invalidates_partial_scope_instead_of_silently_skipping(batch):
    resumed = []
    assert not batch.ensure(
        ("ready", "missing"), resume=lambda: None, on_scope_ready=resumed.append, allow_partial=True
    )
    del batch.host.context.slots["ready"]
    batch.host.context.slots["missing"].source_snapshot = object()
    batch.dialog.set_needs(batch._needs())
    batch.dialog.review_button.click()
    batch.dialog.confirm_button.click()
    _APP.processEvents()
    assert resumed == []


def test_committed_input_does_not_reopen_original_project_after_user_switches(batch):
    reopened = []
    batch.host.project_coordinator = SimpleNamespace(
        open_project_identity=lambda request, callback: reopened.append((request.project_id, request.variant_id))
    )
    inputs = SourceInputCoordinator(batch.host)
    inputs._busy = True
    inputs._identity = batch.host.context.active_version_identity
    inputs._owner_current = None
    inputs._request = SimpleNamespace(project_id="project", variant_id="variant")
    inputs._committed(SimpleNamespace(is_success=True, value=object()))
    batch.host.context.active_version_identity = ("another-project", "variant")
    _APP.processEvents()
    assert reopened == []


def test_unavailable_supplement_reports_failure_before_selecting_a_file(batch):
    batch.host.app_runtime = SimpleNamespace(use_cases=UseCaseRegistry())
    inputs = SourceInputCoordinator(batch.host)
    errors, finished = [], []
    assert not inputs._ensure_one(
        "template",
        ("missing",),
        resume=lambda: pytest.fail("Unsupported operation resumed"),
        report_error=errors.append,
        on_finished=finished.append,
    )
    assert len(errors) == 1 and "当前工程不支持补充" in errors[0]
    assert finished == [False] and not inputs._busy


@pytest.mark.parametrize("committed", [True, False])
def test_available_supplement_hands_committed_view_to_recovery(batch, committed):
    host = batch.host
    inputs = SourceInputCoordinator(host)
    events = []
    request = SimpleNamespace(project_id="project", variant_id="variant")
    token = ProjectReadToken(ProjectId("project"), VariantId("variant"), 3, 4)
    result = (
        OperationResult.completed(SimpleNamespace(active_token=token))
        if committed
        else OperationResult.failed(DomainError(ErrorCategory.CONFLICT, "SOURCE_INPUT_STALE", "来源已变化"))
    )

    def commit(preview_token, context):
        assert context is request
        events.append(("commit", preview_token))
        return result

    def start(operation, *, before_start, on_result, **_options):
        before_start()
        on_result(operation())
        return True

    def restore(committed_token, context, *, view_token, on_finished):
        assert context is request
        events.append(("refresh", committed_token, view_token))
        host.context.finish_project_open(view_token)
        on_finished(True)

    host.context.begin_project_open = lambda: events.append(("begin",)) or 7
    host.context.finish_project_open = lambda view_token: events.append(("finish", view_token))
    host.app_runtime = SimpleNamespace(
        use_cases=UseCaseRegistry({
            "project_source_supplements": SimpleNamespace(
                commit=commit, discard=lambda preview, _request: events.append(("discard", preview))
            )
        })
    )
    host.project_coordinator = SimpleNamespace(refresh_committed_sources=restore)
    host.start_foreground_task = start
    inputs._busy = True
    inputs._identity = host.context.active_version_identity
    inputs._owner_current = None
    inputs._request = request
    inputs._token = "preview"
    inputs._next_namespace = None
    inputs._mappings = {}
    inputs._resume = lambda: events.append(("resume",))
    inputs._report_error = lambda message: events.append(("error", message))
    inputs._on_finished = lambda success: events.append(("ready", success))

    inputs._commit("preview")
    _APP.processEvents()
    _APP.processEvents()
    assert not inputs._busy
    assert events[:2] == [("begin",), ("commit", "preview")]
    if committed:
        assert events[2:] == [("refresh", token, 7), ("finish", 7), ("ready", True), ("resume",)]
    else:
        assert events[2:] == [
            ("finish", 7),
            ("discard", "preview"),
            ("ready", False),
            ("error", "SOURCE_INPUT_STALE: 来源已变化"),
        ]


def test_directory_errors_remain_visible_while_other_candidates_are_supplemented(batch):
    from transbridge.application.projects.source_input_candidates import InputCandidates

    assert not batch.ensure(("ready", "missing"), resume=lambda: None)
    batch.inputs._ensure_one = lambda *_args, **_kwargs: None
    batch._discovered(InputCandidates(("D:/chosen/Missing.esp",), ("D:/locked: access denied",)))
    try:
        assert "D:/locked: access denied" in batch.dialog.feedback.text()
        assert "正在补充" in batch.dialog.feedback.text()
    finally:
        batch._set_busy(False)
