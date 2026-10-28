"""User-triggered retries preserve the failed scope and worker lifecycle."""

from threading import Event

from PyQt6.QtCore import Qt
import pytest

from tests.ui.operations import test_categorized_upload_bridge as bridge, test_categorized_upload_dialog as view
from transbridge.ui.operations.categorized_upload_dialog import UploadReceipt

project_fixture = bridge.project_fixture
harness = bridge.harness
qapp = view.qapp
worker_queue = view.worker_queue


@pytest.mark.parametrize("categorized", [False, True])
def test_manual_retry_relocates_uncertain_creation_without_replaying_success(harness, categorized):
    harness.session.categorized = categorized
    selection = bridge._selection(harness, "both")
    harness.remote.timeout_after_create = True
    first = bridge._upload(harness, selection)
    assert (first.success_count, first.failed_count, first.retry_count) == (1, 1, 1)
    pending = harness.session.last_receipt.retry_files[0].path
    assert len(harness.remote.files) == 2
    before = len(harness.remote.writes)

    second = harness.session.retry_upload(bridge._progress, Event())

    assert (second.success_count, second.failed_count, second.retry_count) == (1, 0, 0)
    assert len(harness.remote.files) == 2
    assert harness.remote.reads == [("inventory", 42), ("inventory", 42)]
    assert [write[0] for write in harness.remote.writes[before:]] == ["source", "translations"]
    assert {write[2] for write in harness.remote.writes[before:]} == {pending}
    with pytest.raises(ValueError, match="没有可补传"):
        harness.session.retry_upload(bridge._progress, Event())


def test_manual_retry_survives_inventory_failure_and_uses_frozen_translation(harness):
    selection = bridge._selection(harness, "both")
    harness.remote.allowed = False
    with pytest.raises(PermissionError):
        bridge._upload(harness, selection)
    original = bridge._snapshot(harness).sources[0].entries[0]
    assert harness.services.gui_project_commands.update_entry(
        original.identity, harness.request_context, translation="本轮之后的新编辑", stage=5
    ).is_success
    harness.remote.allowed = True

    result = harness.session.retry_upload(bridge._progress, Event())

    assert result.success_count == 2 and result.retry_count == 0
    rows = [entry for file in harness.remote.files.values() for entry in file.entries]
    assert any(entry.translation == original.translation for entry in rows)
    assert all(entry.translation != "本轮之后的新编辑" for entry in rows)
    assert bridge._snapshot(harness).sources[0].entries[0].translation == "本轮之后的新编辑"


def test_manual_retry_can_be_cancelled_and_keeps_only_unfinished_files(harness, monkeypatch):
    selection = bridge._selection(harness, "both")
    harness.cancel.set()
    assert bridge._upload(harness, selection).retry_count == 2
    harness.cancel.clear()
    create = harness.remote.create_file

    def cancel_after_create(*args):
        result = create(*args)
        harness.cancel.set()
        return result

    monkeypatch.setattr(harness.remote, "create_file", cancel_after_create)
    result = harness.session.retry_upload(bridge._progress, harness.cancel)
    assert result.cancelled_count == result.retry_count == 2
    harness.cancel.clear()
    monkeypatch.setattr(harness.remote, "create_file", create)
    assert harness.session.retry_upload(bridge._progress, harness.cancel).retry_count == 0
    assert len(harness.remote.files) == 2


@pytest.mark.parametrize("changed", ["account", "project", "variant"])
def test_manual_retry_rechecks_target_before_any_remote_access(harness, changed):
    harness.remote.timeout_after_create = True
    assert bridge._upload(harness).retry_count == 1
    reads, writes = list(harness.remote.reads), list(harness.remote.writes)
    if changed == "account":
        harness.context.resolve_paratranz_target().account_user_id = 8
    else:
        setattr(harness.context, f"active_{changed}_id", "other")
    with pytest.raises(ValueError, match="变化"):
        harness.session.retry_upload(bridge._progress, Event())
    assert harness.remote.reads == reads and harness.remote.writes == writes


def test_button_waits_for_completion_and_retries_independently_of_current_selection(qapp, worker_queue):
    retries = []
    subject = view._dialog(
        upload=lambda *_args: UploadReceipt(1, 1, retry_count=1),
        retry_upload=lambda *_args: retries.append(True) or UploadReceipt(1, 0),
    )
    subject.show()
    qapp.processEvents()
    worker_queue[-1].complete()
    try:
        assert subject._retry_button.isHidden()
        subject._upload_button.click()
        assert subject._retry_button.isHidden()
        subject._request_retry()
        assert len(worker_queue) == 2
        worker_queue[-1].complete()
        assert subject._retry_button.isVisible()
        assert "（1）" in subject._retry_button.text()
        assert "补传未完成文件" in subject._status.text()
        for index in range(subject._source_list.count()):
            subject._source_list.item(index).setCheckState(Qt.CheckState.Unchecked)
        subject._mode.setCurrentIndex(subject._mode.findData("trans_force"))
        assert not subject._upload_button.isEnabled()
        subject._retry_button.click()
        assert subject._retry_button.isHidden()
        assert not subject._upload_button.isEnabled()
        worker_queue[-1].complete()
        assert retries == [True]
        assert subject._retry_button.isHidden()
        assert "补传完成" in subject._status.text()
    finally:
        subject.close()


def test_retry_error_keeps_button_available_until_later_success(qapp, worker_queue):
    subject = view._dialog(retry_upload=lambda *_args: UploadReceipt(2, 0))
    qapp.processEvents()
    worker_queue[-1].complete()
    try:
        subject._upload_button.click()
        worker_queue[-1].error.emit("云端列表读取失败")
        worker_queue[-1].finished.emit()
        assert not subject._retry_button.isHidden()
        assert "补传未完成文件" in subject._status.text()
        subject._retry_button.click()
        worker_queue[-1].error.emit("连接仍未恢复")
        worker_queue[-1].finished.emit()
        assert not subject._retry_button.isHidden()
        subject._retry_button.click()
        worker_queue[-1].complete()
        assert subject._retry_button.isHidden()
    finally:
        subject.close()
