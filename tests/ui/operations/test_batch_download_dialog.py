from __future__ import annotations

import os
from threading import Event, get_ident

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, Qt, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QPushButton
import pytest

from transbridge.application.tasks import TaskCancelled
from transbridge.ui.operations import batch_download_dialog as module
from transbridge.ui.operations.batch_download_dialog import BatchDownloadDialog
from transbridge.ui.operations.batch_download_view import DownloadPreview, DownloadReceipt, DownloadSourceOption
from transbridge.ui.operations.sync_review_view import SyncReviewCandidate, SyncReviewRow

_APP = QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def qapp():
    return _APP


class Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self, *args):
        for callback in self.callbacks:
            callback(*args)


class DeferredWorker:
    def __init__(self, operation, **_kwargs):
        self.operation = operation
        self.result, self.error, self.finished = Signal(), Signal(), Signal()

    def start(self):
        pass

    def complete(self):
        try:
            self.result.emit(self.operation())
        except Exception as exc:
            self.error.emit(str(exc))
        self.finished.emit()


@pytest.fixture
def workers(monkeypatch):
    queue = []

    def create(operation, **kwargs):
        worker = DeferredWorker(operation, **kwargs)
        queue.append(worker)
        return worker

    monkeypatch.setattr(module, "ApiWorker", create)
    return queue


def sources():
    return (DownloadSourceOption("one", "主体.esp", 3), DownloadSourceOption("two", "扩展.esp", 5))


def make_dialog(**overrides):
    callbacks = {
        "load_sources": lambda *_args: sources(),
        "compare": lambda *_args: DownloadPreview(4, 4, 0),
        "apply": lambda *_args: DownloadReceipt(4),
    }
    callbacks.update(overrides)
    return BatchDownloadDialog("工程", "版本", "云端项目", **callbacks)


@pytest.fixture
def dialog(qapp, workers):
    subject = make_dialog()
    subject.show()
    qapp.processEvents()
    workers[-1].complete()
    yield subject
    if subject._worker is not None:
        subject.reject()
        workers[-1].complete()
    subject.close()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def review_row():
    candidate = SyncReviewCandidate("remote-choice", "云端译文", "Original", "Remote")
    return SyncReviewRow("row", "key", "Original", "Local", "", "选择译文", (candidate,))


def test_saved_result_reopens_in_worker_and_export_success_is_shown(qapp, workers, monkeypatch):
    from transbridge.ui.operations import batch_download_recovery as recovery

    calls = []
    record = {"created_at": "2026-10-26", "sources": {"one": "one.json"}, "result_id": "saved"}
    subject = make_dialog(
        saved_results=lambda: (record,),
        restore=lambda result_id, *_: calls.append(result_id) or DownloadPreview(1, 0, 1, source_ids=("one",)),
        export_results=lambda path: calls.append(path),
    )
    subject.show()
    qapp.processEvents()
    workers[-1].complete()
    monkeypatch.setattr(
        recovery.QInputDialog, "getItem", lambda *_args, **_kwargs: ("2026-10-26 · 1 个来源 · saved", True)
    )
    subject._recovery_controls.reopen.click()
    assert calls == []
    workers[-1].complete()
    workers[-1].complete()
    assert calls == ["saved"] and subject.selection() == ("one",)
    monkeypatch.setattr(recovery.QFileDialog, "getSaveFileName", lambda *_: ("export.json", "JSON"))
    subject._recovery_controls.save_as.click()
    workers[-1].complete()
    assert calls[-1] == "export.json" and "已另存" in subject._status.text()
    assert subject._worker is None
    subject.close()


@pytest.mark.parametrize("has_healthy", [False, True])
def test_saved_result_damage_warning_keeps_healthy_selection_available(qapp, workers, monkeypatch, has_healthy):
    from transbridge.application.sync.download_results import DownloadResultRecords
    from transbridge.ui.operations import batch_download_recovery as recovery

    calls, warnings = [], []
    record = {"created_at": "2026-10-26", "sources": {"one": "one.json"}, "result_id": "saved"}
    diagnostic = "损坏记录：result.json；原文件保留"
    records = DownloadResultRecords((record,) if has_healthy else (), diagnostics=(diagnostic,))
    subject = make_dialog(
        saved_results=lambda: records,
        restore=lambda result_id, *_: calls.append(result_id) or DownloadPreview(1, 0, 1, source_ids=("one",)),
        export_results=lambda *_: None,
    )
    monkeypatch.setattr(recovery.QMessageBox, "warning", lambda *_args: warnings.append(_args[-1]))
    monkeypatch.setattr(recovery.QMessageBox, "information", lambda *_args: None)
    monkeypatch.setattr(
        recovery.QInputDialog, "getItem", lambda *_args, **_kwargs: ("2026-10-26 · 1 个来源 · saved", True)
    )
    subject.show()
    qapp.processEvents()
    workers[-1].complete()
    subject._recovery_controls.reopen.click()
    workers[-1].complete()
    if has_healthy:
        workers[-1].complete()
    assert warnings == [diagnostic]
    assert calls == (["saved"] if has_healthy else [])
    subject.close()


def test_one_click_compares_and_saves_in_one_worker_without_another_confirmation(qapp, workers):
    calls = []

    def compare(source_ids, policy, decisions, _progress, cancel):
        calls.append(("compare", source_ids, policy, decisions, cancel))
        return DownloadPreview(4, 4, 0)

    def apply(_progress, cancel):
        calls.append(("apply", cancel))
        return DownloadReceipt(4)

    subject = make_dialog(compare=compare, apply=apply)
    subject.show()
    qapp.processEvents()
    assert not subject._primary_button.isEnabled()
    workers[-1].complete()
    assert subject.selection() == ("one", "two")
    assert "8 条" in subject._summary.text()
    assert subject._primary_button.text() == "下载并保存"
    subject._primary_button.click()
    subject._request_primary()
    assert len(workers) == 2  # One local catalog worker, one complete download worker.
    assert calls == []
    cancel = subject._cancel_event
    workers[-1].complete()
    assert calls == [("compare", ("one", "two"), "keep_current", (), cancel), ("apply", cancel)]
    assert subject._cancel_event is cancel
    assert len(workers) == 2
    assert "已更新 4 条" in subject._status.text()
    assert "保存到当前本地版本" in subject._status.text()
    assert not subject._primary_button.isEnabled()
    assert subject._primary_button.text() == "已保存"
    labels = [button.text() for button in subject.findChildren(QPushButton)]
    assert labels.count("已保存") == 1
    assert not any("对照" in label or label == "更新并保存" for label in labels)
    before = len(workers)
    subject._primary_button.click()
    subject._request_primary()
    assert len(workers) == before
    subject.close()


@pytest.mark.parametrize("change", ["source", "policy"])
def test_selection_and_policy_changes_do_not_run_until_the_next_download_click(dialog, workers, change):
    dialog._primary_button.click()
    workers[-1].complete()
    assert dialog._primary_button.text() == "已保存"
    assert not dialog._primary_button.isEnabled()
    calls = []
    dialog._compare = lambda *args: calls.append(args[:3]) or DownloadPreview(1, 0, 0)
    if change == "source":
        dialog._source_list.item(0).setCheckState(Qt.CheckState.Unchecked)
    else:
        dialog._policy.setCurrentIndex(1)
    assert dialog._preview is None
    assert dialog._primary_button.isEnabled()
    assert dialog._primary_button.text() == "下载并保存"
    assert calls == []
    before = len(workers)
    dialog._primary_button.click()
    workers[-1].complete()
    assert len(workers) == before + 1
    assert calls == [(("two",), "keep_current", ()) if change == "source" else (("one", "two"), "prefer_incoming", ())]


def test_no_selection_cannot_start_a_download(dialog, workers):
    dialog._select_none.click()
    before = len(workers)
    dialog._request_primary()
    assert len(workers) == before
    assert not dialog._primary_button.isEnabled()


@pytest.mark.parametrize("phase", ["compare", "apply"])
def test_failure_keeps_choices_and_decisions_for_one_click_retry(dialog, workers, phase):
    attempts = []
    dialog._source_list.item(1).setCheckState(Qt.CheckState.Unchecked)
    dialog._decisions = (("entry", "keep_local"),)

    def operation(*_args):
        attempts.append(phase)
        if len(attempts) == 1:
            raise RuntimeError("文件读取失败" if phase == "compare" else "历史还原点写入失败")
        return DownloadPreview(2, 1, 0) if phase == "compare" else DownloadReceipt(2)

    if phase == "compare":
        dialog._compare = operation
    else:
        dialog._apply = operation
    before = len(workers)
    dialog._primary_button.click()
    workers[-1].complete()
    assert dialog.selection() == ("one",)
    assert dialog._decisions == (("entry", "keep_local"),)
    assert "本次选择已保留" in dialog._status.text()
    assert dialog._primary_button.isEnabled()
    assert dialog._primary_button.text() == "重试未完成项"
    dialog._primary_button.click()
    workers[-1].complete()
    assert attempts == [phase, phase]
    assert len(workers) == before + 2
    assert "失败" not in dialog._status.text()
    assert "保存到当前本地版本" in dialog._status.text()


def test_stale_noncommitting_preview_cannot_replace_new_selection(dialog, workers):
    dialog._compare = lambda *_args: DownloadPreview(0, 8, 0)
    dialog._primary_button.click()
    dialog._source_list.item(0).setCheckState(Qt.CheckState.Unchecked)
    workers[-1].complete()
    assert dialog._preview is None
    assert dialog._primary_button.isEnabled()
    assert "所选插件已变化" in dialog._status.text()


def test_saved_receipt_is_not_discarded_when_selection_changes_during_work(dialog, workers):
    selected = []
    dialog._compare = lambda source_ids, *_args: selected.append(source_ids) or DownloadPreview(4, 4, 0)
    dialog._primary_button.click()
    dialog._source_list.item(0).setCheckState(Qt.CheckState.Unchecked)
    workers[-1].complete()
    assert selected == [("one", "two")]
    assert dialog.selection() == ("two",)
    assert "已更新 4 条" in dialog._status.text()
    assert "保存到当前本地版本" in dialog._status.text()
    assert dialog._preview is None


def test_local_rebase_automatically_continues_to_save_in_the_original_worker(dialog, workers):
    cancels, attempts = [], []

    def apply(_progress, cancel):
        cancels.append(cancel)
        attempts.append(True)
        if len(attempts) == 1:
            return DownloadReceipt(0, requires_review=True, preview=DownloadPreview(1, 7, 0))
        return DownloadReceipt(1)

    dialog._apply = apply
    before = len(workers)
    dialog._primary_button.click()
    original_cancel = dialog._cancel_event
    workers[-1].complete()
    assert len(attempts) == 2
    assert cancels == [original_cancel, original_cancel]
    assert len(workers) == before + 1
    assert "已更新 1 条" in dialog._status.text()
    assert "保存到当前本地版本" in dialog._status.text()


def test_dirty_version_without_cloud_changes_is_saved_by_the_same_download_click(dialog, workers):
    calls = []
    dialog._compare = lambda *_args: DownloadPreview(0, 8, 0, can_save=True)
    dialog._apply = lambda *_args: calls.append("save") or DownloadReceipt(0)
    before = len(workers)
    dialog._primary_button.click()
    workers[-1].complete()
    assert calls == ["save"]
    assert len(workers) == before + 1
    assert "已更新 0 条" in dialog._status.text()
    assert "保存到当前本地版本" in dialog._status.text()
    assert dialog._primary_button.text() == "已保存"
    assert not dialog._primary_button.isEnabled()


@pytest.mark.parametrize(
    ("preview", "message", "button_label"),
    [
        (DownloadPreview(0, 0, 0, can_apply=False), "本轮没有可更新译文，请查看文件结果；可原地重试。", "重试未完成项"),
        (DownloadPreview(0, 8, 0), "所选工程译文无需更新。", "已完成"),
        (DownloadPreview(0, 7, 1), "待确认项保留本地内容；请确认要采用的译文，其他译文无需更新。", "已完成"),
        (DownloadPreview(2, 6, 0, can_apply=False), "本轮尚不能更新译文，请查看文件结果；可原地重试。", "重试未完成项"),
        (
            DownloadPreview(0, 0, 0, can_apply=False, status="所选插件均没有当前路径文件。", retryable=True),
            "所选插件均没有当前路径文件。",
            "重试未完成项",
        ),
        (DownloadPreview(0, 8, 0, retryable=True), "本轮仍有未完成项，请查看文件结果后重试。", "重试未完成项"),
    ],
)
def test_noncommitting_results_distinguish_completed_requests_from_retryable_sources(
    dialog, workers, preview, message, button_label
):
    dialog._compare = lambda *_args: preview
    dialog._apply = lambda *_args: pytest.fail("An empty or blocked plan must not save")
    before = len(workers)
    dialog._primary_button.click()
    workers[-1].complete()
    assert dialog._status.text() == message
    assert "已更新并保存" not in dialog._status.text()
    assert dialog._primary_button.text() == button_label
    assert dialog._primary_button.isEnabled() is (button_label == "重试未完成项")
    assert len(workers) == before + 1


def test_source_defaults_all_and_none_keep_one_download_action(qapp, workers):
    subject = make_dialog(
        load_sources=lambda *_args: (
            DownloadSourceOption("one", "主体.esp", 3, selected=True),
            DownloadSourceOption("two", "扩展.esp", 5, selected=False),
        )
    )
    subject.show()
    qapp.processEvents()
    workers[-1].complete()
    assert subject.selection() == ("one",)
    assert "批量" not in subject.windowTitle()
    subject._select_all.click()
    assert subject.selection() == ("one", "two")
    subject._select_none.click()
    assert subject.selection() == ()
    assert not subject._primary_button.isEnabled()
    subject._source_list.item(0).setCheckState(Qt.CheckState.Checked)
    primary = subject._primary_button
    primary.click()
    assert not subject._select_all.isEnabled()
    assert not subject._select_none.isEnabled()
    workers[-1].complete()
    assert subject._primary_button is primary
    assert primary.text() == "已保存"
    assert not primary.isEnabled()
    assert "保存到当前本地版本" in subject._status.text()
    subject.close()


def test_safe_updates_are_saved_while_real_conflicts_remain_reviewable(dialog, workers):
    row = review_row()
    dialog._compare = lambda *_args: DownloadPreview(2, 5, 1, review_rows=(row,))
    dialog._apply = lambda *_args: DownloadReceipt(2)
    dialog._primary_button.click()
    workers[-1].complete()
    assert "已更新并保存 2 条" in dialog._status.text()
    assert "仍有 1 条需要确认" in dialog._status.text()
    assert "已更新并保存 2 条，待确认 1 条" in dialog._summary.text()
    assert dialog._preview.updates == 0
    assert not dialog._preview.can_apply
    assert dialog._review_button.isVisible()
    assert dialog._review_button.isEnabled()
    assert dialog.selection() == ("one", "two")
    assert dialog._primary_button.text() == "已保存"
    assert not dialog._primary_button.isEnabled()


def test_rebase_failure_keeps_specific_reason_without_another_save_confirmation(dialog, workers):
    fresh = DownloadPreview(0, 0, 0, can_apply=False, status="当前版本持续有新编辑；选择已保留，可直接重试。")
    dialog._apply = lambda *_args: DownloadReceipt(0, requires_review=True, preview=fresh)
    dialog._primary_button.click()
    workers[-1].complete()
    assert dialog._preview is fresh
    assert dialog._status.text() == fresh.status
    assert dialog._primary_button.text() == "重试未完成项"
    assert dialog._primary_button.isEnabled()


@pytest.mark.parametrize("with_conflict", [False, True])
def test_partial_save_with_failed_source_remains_retryable_and_finishes_after_retry(dialog, workers, with_conflict):
    compares, saves = [], []

    def compare(*_args):
        compares.append(True)
        if len(compares) == 1:
            return DownloadPreview(
                2, 0, int(with_conflict), review_rows=(review_row(),) if with_conflict else (), retryable=True
            )
        return DownloadPreview(1, 2, 0)

    def apply(*_args):
        saves.append(True)
        return DownloadReceipt(2, retryable=True) if len(saves) == 1 else DownloadReceipt(1)

    dialog._compare, dialog._apply = compare, apply
    dialog._primary_button.click()
    workers[-1].complete()
    assert "已更新并保存 2 条" in dialog._status.text()
    assert dialog._primary_button.text() == "重试未完成项"
    assert dialog._primary_button.isEnabled()
    if with_conflict:
        assert dialog._review_button.isEnabled()
        assert dialog._review_button.isVisible()
    before = len(workers)
    dialog._primary_button.click()
    workers[-1].complete()
    assert len(workers) == before + 1
    assert len(compares) == len(saves) == 2
    assert dialog._primary_button.text() == "已保存"
    assert not dialog._primary_button.isEnabled()


def test_unsubmitted_replan_is_retryable_even_without_a_source_error_flag(dialog, workers):
    fresh = DownloadPreview(1, 7, 0)
    dialog._apply = lambda *_args: DownloadReceipt(0, requires_review=True, preview=fresh)
    dialog._primary_button.click()
    workers[-1].complete()
    assert dialog._preview.updates == 1
    assert dialog._primary_button.text() == "重试未完成项"
    assert dialog._primary_button.isEnabled()


@pytest.mark.parametrize("accept", [False, True])
def test_review_applies_and_saves_choices_automatically_without_another_main_button_click(
    dialog, workers, monkeypatch, accept
):
    row = review_row()
    calls = []

    def compare(source_ids, policy, decisions, _progress, _cancel):
        calls.append(("compare", source_ids, policy, decisions))
        return DownloadPreview(1, 0, 0) if decisions else DownloadPreview(0, 0, 1, review_rows=(row,))

    dialog._compare = compare
    dialog._apply = lambda *_args: calls.append(("apply",)) or DownloadReceipt(1)
    dialog._primary_button.click()
    workers[-1].complete()
    assert dialog._review_button.isVisible()
    assert dialog._primary_button.text() == "已完成"
    assert not dialog._primary_button.isEnabled()

    class Review:
        def __init__(self, rows, parent, *, submit_on_accept):
            assert rows == (row,)
            assert parent is dialog
            assert submit_on_accept

        def exec(self):
            return QDialog.DialogCode.Accepted if accept else QDialog.DialogCode.Rejected

        def decisions(self):
            return {"row": "remote-choice"}

        def deleteLater(self):
            pass

    monkeypatch.setattr(module, "SyncReviewDialog", Review)
    before = len(workers)
    dialog._review_button.click()
    if accept:
        assert len(workers) == before + 1
        workers[-1].complete()
        assert calls[-2:] == [("compare", ("one", "two"), "keep_current", (("row", "remote-choice"),)), ("apply",)]
        assert "保存到当前本地版本" in dialog._status.text()
        assert dialog._decisions == (("row", "remote-choice"),)
        assert dialog._primary_button.text() == "已保存"
        assert not dialog._primary_button.isEnabled()
    else:
        assert len(workers) == before
        assert not any(call[0] == "apply" for call in calls)


@pytest.mark.parametrize("submit_on_accept", [False, True])
def test_real_review_dialog_explains_whether_accepting_will_save(qapp, submit_on_accept):
    subject = module.SyncReviewDialog((review_row(),), submit_on_accept=submit_on_accept)
    try:
        labels = [button.text() for button in subject.findChildren(QPushButton)]
        if submit_on_accept:
            assert "确认选择并保存" in labels
            assert "确认后自动下载并保存" in subject._footer_note.text()
            assert "选择尚未执行" not in subject._footer_note.text()
        else:
            assert "完成选择，返回更新" in labels
            assert "选择尚未执行" in subject._footer_note.text()
    finally:
        subject.close()
        subject.deleteLater()


@pytest.mark.parametrize("phase", ["load", "run"])
def test_close_cancels_without_destroying_the_running_worker_or_starting_save(qapp, workers, phase):
    subject = make_dialog(apply=lambda *_args: pytest.fail("Cancelled download must not save"))
    subject.show()
    qapp.processEvents()
    if phase == "run":
        workers[-1].complete()
        subject._primary_button.click()
    current = workers[-1]
    subject.close()
    assert subject.isVisible()
    assert subject._worker is current
    assert subject._cancel_event.is_set()
    assert not subject._cancel.isEnabled()
    current.complete()
    assert subject._worker is None
    assert "已取消" in subject._status.text()
    assert subject._status_state is module.SemanticState.INFO
    subject.close()


def test_cancellation_between_read_and_save_prevents_automatic_apply(dialog, workers):
    def compare(_source_ids, _policy, _decisions, _progress, cancel):
        cancel.set()
        return DownloadPreview(4, 4, 0)

    dialog._compare = compare
    dialog._apply = lambda *_args: pytest.fail("Cancellation between phases must prevent save")
    dialog._primary_button.click()
    cancel = dialog._cancel_event
    workers[-1].complete()
    assert dialog._cancel_event is cancel
    assert "已取消" in dialog._status.text()
    assert dialog._status_state is module.SemanticState.INFO


def test_cancelled_save_is_not_reported_as_an_error(dialog, workers):
    def apply(_progress, cancel):
        cancel.set()
        raise TaskCancelled("下载已取消")

    dialog._apply = apply
    dialog._primary_button.click()
    workers[-1].complete()
    assert "已取消" in dialog._status.text()
    assert dialog._status_state is module.SemanticState.INFO


@pytest.mark.parametrize("updated", [0, 4])
def test_successful_save_receipt_wins_over_late_cancellation(dialog, workers, updated):
    def apply(_progress, cancel):
        cancel.set()
        return DownloadReceipt(updated)

    dialog._apply = apply
    dialog._primary_button.click()
    workers[-1].complete()
    assert f"已更新 {updated} 条" in dialog._status.text()
    assert "保存到当前本地版本" in dialog._status.text()
    assert "已取消" not in dialog._status.text()
    assert dialog._status_state is module.SemanticState.SUCCESS
    assert dialog._primary_button.text() == "已保存"
    assert not dialog._primary_button.isEnabled()


def test_failed_source_load_can_retry_here_without_starting_a_download(qapp, workers):
    calls = []

    def load(*_args):
        calls.append("load")
        if len(calls) == 1:
            raise ValueError("来源目录读取失败")
        return sources()

    subject = make_dialog(load_sources=load)
    qapp.processEvents()
    workers[-1].complete()
    assert subject._reload_button.isEnabled()
    assert "已保留" in subject._status.text()
    subject._reload_button.click()
    workers[-1].complete()
    assert subject.selection() == ("one", "two")
    assert subject._reload_button.isHidden()
    assert calls == ["load", "load"]
    subject.close()


def test_details_are_bounded_and_literal_text(dialog, workers):
    dialog._compare = lambda *_args: DownloadPreview(0, 0, 0, ("<b>file</b>" + "x" * 40_000, "second"))
    dialog._primary_button.click()
    workers[-1].complete()
    assert len(dialog._details.toPlainText()) < 32_100
    assert dialog._details.toPlainText().startswith("<b>file</b>")
    assert "已截断" in dialog._details.toPlainText()


def test_old_worker_events_cannot_finish_or_overwrite_another_download(dialog, workers):
    dialog._primary_button.click()
    old = workers[-1]
    old.complete()
    dialog._policy.setCurrentIndex(1)
    dialog._primary_button.click()
    active = workers[-1]
    old.result.emit(DownloadReceipt(999))
    old.error.emit("旧错误")
    old.finished.emit()
    assert dialog._worker is active
    assert dialog._outcome is None and dialog._error is None
    active.complete()
    assert "已更新 4 条" in dialog._status.text()


def test_real_worker_compares_and_saves_off_gui_thread_without_blocking_events(qapp):
    main_thread = get_ident()
    phases, gui_ticks = [], []
    entered, release = Event(), Event()

    def compare(_sources, _policy, _decisions, progress, cancel):
        phases.append(("compare", get_ident(), cancel))
        entered.set()
        release.wait(2)
        for index in range(1000):
            progress(index, 1000, f"文件 {index}")
        assert not cancel.is_set()
        return DownloadPreview(1, 7, 0)

    def apply(_progress, cancel):
        phases.append(("apply", get_ident(), cancel))
        return DownloadReceipt(1)

    subject = make_dialog(compare=compare, apply=apply)
    subject.show()
    for _ in range(100):
        QTest.qWait(10)
        if subject._sources is not None:
            break
    assert subject._sources is not None
    original_render = subject._render_receipt
    rendering_threads = []
    subject._render_receipt = lambda value: rendering_threads.append(get_ident()) or original_render(value)
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: gui_ticks.append(True))
    timer.start()
    try:
        subject._primary_button.click()
        assert entered.wait(1)
        QTest.qWait(30)
        assert gui_ticks and subject._worker is not None
        assert len(phases) == 1
        release.set()
        for _ in range(100):
            QTest.qWait(10)
            if subject._worker is None:
                break
        assert subject._worker is None
        assert [phase for phase, _thread, _cancel in phases] == ["compare", "apply"]
        assert phases[0][1] == phases[1][1] != main_thread
        assert phases[0][2] is phases[1][2]
        assert rendering_threads == [main_thread]
        assert "保存到当前本地版本" in subject._status.text()
        assert subject._primary_button.text() == "已保存"
        assert not subject._primary_button.isEnabled()
    finally:
        release.set()
        timer.stop()
        while subject._worker is not None:
            QTest.qWait(10)
        subject.close()
