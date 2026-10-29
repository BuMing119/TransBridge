"""Keep batch source confirmation bound to its original project and source set."""

from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication, QWidget
import pytest

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
