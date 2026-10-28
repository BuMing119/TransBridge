from __future__ import annotations

import os
from threading import Event, get_ident

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, Qt, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QCheckBox, QLabel, QPushButton, QVBoxLayout, QWidget
import pytest

from transbridge.ui.operations import categorized_upload_dialog as module
from transbridge.ui.operations.categorized_upload_dialog import (
    CategorizedUploadDialog,
    FileOption,
    SourceOption,
    UploadReceipt,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self, *args):
        for callback in self.callbacks:
            callback(*args)


class DeferredWorker:
    def __init__(self, fn, **_kwargs):
        self.fn = fn
        self.result = _Signal()
        self.error = _Signal()
        self.finished = _Signal()
        self.progress = _Signal()

    def start(self):
        pass

    def complete(self):
        try:
            self.result.emit(self.fn())
        except Exception as exc:
            self.error.emit(str(exc))
        self.finished.emit()


@pytest.fixture
def worker_queue(monkeypatch):
    workers = []

    def create(fn, **kwargs):
        worker = DeferredWorker(fn, **kwargs)
        workers.append(worker)
        return worker

    monkeypatch.setattr(module, "ApiWorker", create)
    return workers


def _sources():
    return (
        SourceOption("a", "主体.esp", (FileOption("a:people", "主体.esp/人名.json", 3),)),
        SourceOption("b", "扩展.esp", (FileOption("b:items", "扩展.esp/物品.json", 5),)),
    )


def _dialog(**overrides):
    options = {
        "load_preview": lambda _progress, _cancel: _sources(),
        "upload": lambda _selection, _progress, _cancel: UploadReceipt(8, 0),
    }
    options.update(overrides)
    return CategorizedUploadDialog("汉化工程", "正式版", "云端汉化", **options)


@pytest.fixture
def dialog(qapp, worker_queue):
    subject = _dialog()
    subject.show()
    qapp.processEvents()
    worker_queue[-1].complete()
    yield subject
    if subject._worker is not None:
        subject.reject()
        worker_queue[-1].complete()
    subject.close()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_selection_directly_uploads_and_retries_without_manual_comparison(qapp, worker_queue):
    uploaded = []
    subject = CategorizedUploadDialog(
        "工程",
        "版本",
        "云端",
        load_preview=lambda *_args: _sources(),
        upload=lambda selection, *_args: uploaded.append(selection) or UploadReceipt(8, 0),
    )
    qapp.processEvents()
    assert not subject._upload_button.isEnabled()
    worker_queue[-1].complete()
    assert subject._upload_button.isEnabled()
    assert subject._upload_button.text() == "上传所选分类"
    assert subject._files.columnCount() == 2
    assert all("对照" not in widget.text() for widget in subject.findChildren((QPushButton, QLabel)))
    for _ in range(2):
        subject._upload_button.click()
        assert not subject._upload_button.isEnabled()
        worker_queue[-1].complete()
        assert subject._upload_button.isEnabled()
    assert len(uploaded) == 2
    assert all(selection.source_ids == ("a", "b") for selection in uploaded)
    subject.close()


def test_loading_only_previews_local_files_and_never_uploads(qapp, worker_queue):
    calls = []
    subject = _dialog(
        load_preview=lambda _progress, _cancel: calls.append("preview") or _sources(),
        upload=lambda *_args: calls.append("upload"),
    )
    qapp.processEvents()
    assert calls == []
    assert not subject._upload_button.isEnabled()
    worker_queue[-1].complete()
    assert calls == ["preview"]
    assert subject.selection().source_ids == ("a", "b")
    assert subject.selection().file_keys == ("a:people", "b:items")
    assert not subject.selection().migrate_existing
    assert subject._upload_button.isEnabled()
    assert subject._reload_button.isHidden()
    assert "2 个插件、2 个分类文件，共 8 条词条" in subject._summary.text()
    assert "汉化工程" in subject._scope.text()
    assert "正式版" in subject._scope.text()
    assert "云端汉化" in subject._scope.text()
    subject.close()


@pytest.mark.parametrize("batch", [False, True])
def test_upload_scope_never_offers_legacy_migration(qapp, worker_queue, batch):
    subject = _dialog(batch=batch)
    qapp.processEvents()
    worker_queue[-1].complete()
    try:
        assert subject.findChildren(QCheckBox) == []
        assert not subject.selection().migrate_existing
        assert any("增量更新保留云端其他词条" in label.text() for label in subject.findChildren(QLabel))
    finally:
        subject.close()


def test_unchecking_files_updates_summary_and_empty_selection_blocks_upload(dialog, worker_queue):
    dialog._files.topLevelItem(0).setCheckState(0, Qt.CheckState.Unchecked)
    assert dialog._upload_button.isEnabled()
    assert dialog.selection().file_keys == ("b:items",)
    assert "1 个分类文件，共 5 条词条" in dialog._summary.text()
    dialog._files.topLevelItem(1).setCheckState(0, Qt.CheckState.Unchecked)
    assert not dialog._upload_button.isEnabled()
    workers_before = len(worker_queue)
    dialog._request_upload()
    assert len(worker_queue) == workers_before


def test_source_selection_is_passed_directly_to_upload_without_migration(dialog, worker_queue):
    captured = []
    dialog._upload = lambda selection, *_args: captured.append(selection) or UploadReceipt(5, 0)
    dialog._source_list.item(0).setCheckState(Qt.CheckState.Unchecked)
    assert dialog._upload_button.isEnabled()
    assert dialog._files.topLevelItemCount() == 1
    assert dialog.selection().source_ids == ("b",)
    dialog._upload_button.click()
    worker_queue[-1].complete()
    assert captured == [module.Selection(("b",), ("b:items",), False)]


def test_running_upload_keeps_captured_selection_and_cannot_start_twice(dialog, worker_queue):
    captured = []
    dialog._upload = lambda selection, *_args: captured.append(selection) or UploadReceipt(8, 0)
    dialog._upload_button.click()
    assert not dialog._files.isEnabled()
    dialog._files.topLevelItem(0).setCheckState(0, Qt.CheckState.Unchecked)
    workers_before = len(worker_queue)
    dialog._request_upload()
    assert len(worker_queue) == workers_before
    worker_queue[-1].complete()
    assert captured == [module.Selection(("a", "b"), ("a:people", "b:items"), False)]
    assert dialog._files.isEnabled()
    assert dialog._upload_button.isEnabled()


@pytest.mark.parametrize("cancelled", [False, True])
def test_local_load_failure_or_cancellation_can_reload_without_upload(qapp, worker_queue, cancelled):
    subject = _dialog()
    qapp.processEvents()
    worker = worker_queue[-1]
    if cancelled:
        subject.reject()
        worker.complete()
        assert "已取消读取分类" in subject._status.text()
    else:
        worker.error.emit("工程读取失败")
        worker.finished.emit()
        assert "请重新加载" in subject._status.text()
    assert not subject._upload_button.isEnabled()
    assert not subject._reload_button.isHidden()
    subject._reload_button.click()
    worker_queue[-1].complete()
    assert subject._upload_button.isEnabled()
    assert subject._reload_button.isHidden()
    subject.close()


def test_cancel_keeps_running_worker_alive_and_preserves_partial_receipt(dialog, worker_queue):
    dialog._upload = lambda _selection, _progress, cancel: UploadReceipt(3, 0, 5 if cancel.is_set() else 0)
    dialog._upload_button.click()
    worker = worker_queue[-1]
    dialog.close()
    assert dialog.isVisible()
    assert dialog._worker is worker
    assert dialog._cancel_event.is_set()
    assert not dialog._cancel.isEnabled()
    worker.complete()
    assert dialog._worker is None
    assert dialog._upload_button.isEnabled()
    assert "已取消" in dialog._status.text()
    assert "成功 3，失败或待核实 0，取消 5" in dialog._status.text()


def test_upload_error_restores_direct_retry(dialog, worker_queue):
    dialog._upload_button.click()
    worker = worker_queue[-1]
    dialog._progress_mailbox.publish(1, 8, "正在上传")
    worker.error.emit("请求超时，结果需要核对")
    worker.finished.emit()
    assert "失败" in dialog._status.text()
    assert "请求超时" in dialog._status.text()
    assert dialog._files.isEnabled()
    assert dialog._upload_button.isEnabled()
    assert "结果待核实的写入不会自动重发" in dialog._status.text()
    assert not dialog._progress_timer.isActive()


def test_partial_upload_reports_success_failure_and_pending_for_direct_retry(dialog, worker_queue):
    dialog._upload = lambda _selection, _progress, _cancel: UploadReceipt(
        3, 1, 0, ("主体.esp/人名.json：成功", "扩展.esp/物品.json：请求失败"), pending_count=4
    )
    dialog._upload_button.click()
    assert not dialog._files.isEnabled()
    assert not dialog._upload_button.isEnabled()
    worker_queue[-1].complete()
    assert "未全部完成" in dialog._status.text()
    assert "成功 3，失败或待核实 1，取消 0，待处理 4" in dialog._status.text()
    assert "主体.esp/人名.json：成功" in dialog._details.toPlainText()
    assert "扩展.esp/物品.json：请求失败" in dialog._details.toPlainText()
    assert dialog._upload_button.isEnabled()
    assert "可再次点击上传" in dialog._status.text()


def test_cancellation_before_remote_reads_does_not_claim_empty_upload_completed(dialog, worker_queue):
    dialog._upload = lambda *_args: UploadReceipt(0, 0, details=("已取消，尚未读取云端",))
    dialog._upload_button.click()
    dialog.reject()
    worker_queue[-1].complete()
    assert "分类上传已取消" in dialog._status.text()
    assert "分类上传完成" not in dialog._status.text()
    assert "尚未读取云端" in dialog._details.toPlainText()
    assert dialog._upload_button.isEnabled()


def test_conflicts_are_reported_in_upload_result_with_reason(dialog, worker_queue):
    dialog._upload = lambda *_args: UploadReceipt(2, 0, details=("一条归属待确认",), pending_count=1)
    dialog._upload_button.click()
    worker_queue[-1].complete()
    assert "成功 2，失败或待核实 0，取消 0，待处理 1" in dialog._status.text()
    assert "一条归属待确认" in dialog._details.toPlainText()
    assert dialog._upload_button.isEnabled()


def test_all_application_callbacks_run_on_background_workers(qapp):
    main_thread = get_ident()
    observed_threads = []

    def load_preview(progress, _cancel):
        observed_threads.append(get_ident())
        progress(1, 1, "工程已读取")
        return _sources()

    def upload(_selection, _progress, _cancel):
        observed_threads.append(get_ident())
        return UploadReceipt(7, 1, retry_count=1)

    def retry_upload(_progress, _cancel):
        observed_threads.append(get_ident())
        return UploadReceipt(1, 0)

    def settle(subject):
        for _ in range(200):
            qapp.processEvents()
            if subject._worker is None:
                return
            QTest.qWait(5)
        pytest.fail("后台分类上传操作未结束")

    subject = _dialog(load_preview=load_preview, upload=upload, retry_upload=retry_upload)
    subject.show()
    settle(subject)
    subject._upload_button.click()
    settle(subject)
    assert subject._retry_button.isVisible()
    subject._retry_button.click()
    settle(subject)
    assert len(observed_threads) == 3
    assert all(thread != main_thread for thread in observed_threads)
    assert "分类补传完成（按文件计数）：成功 1，失败或待核实 0" in subject._status.text()
    assert subject._retry_button.isHidden()
    assert subject._upload_button.isEnabled()
    subject.close()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_burst_progress_flushes_once_before_terminal_receipt(dialog, worker_queue, monkeypatch):
    rendered = []
    original_render = dialog._render_progress

    def render(current, total, message):
        rendered.append((current, total, message))
        original_render(current, total, message)

    def upload(_selection, progress, _cancel):
        for current in range(1, 20_001):
            progress(current, 20_000, "正在保存已确认的关联")
        return UploadReceipt(20_000, 0)

    monkeypatch.setattr(dialog, "_render_progress", render)
    dialog._upload = upload
    dialog._upload_button.click()
    worker = worker_queue[-1]
    mailbox = dialog._progress_mailbox
    assert dialog._progress_timer.isActive()
    worker.complete()
    assert rendered == [(20_000, 20_000, "正在保存已确认的关联")]
    assert worker.progress.callbacks == []
    assert dialog._progress.text() == "已处理 20000/20000（100%）"
    assert "分类上传完成（按文件计数）：成功 20000" in dialog._status.text()
    assert not dialog._progress_timer.isActive()
    assert dialog._progress_mailbox is None
    mailbox.publish(1, 20_000, "晚到的旧进度")
    assert mailbox.take() is None


def test_cancel_status_survives_timer_progress_and_preserves_partial_receipt(dialog, worker_queue):
    dialog._upload = lambda *_args: UploadReceipt(7, 0, 19_993)
    dialog._upload_button.click()
    dialog._progress_mailbox.publish(7, 20_000, "正在保存关联")
    dialog.reject()
    cancelling = dialog._status.text()
    dialog._consume_progress()
    assert dialog._status.text() == cancelling
    assert dialog._progress.text() == "已处理 7/20000（0%）"
    worker_queue[-1].complete()
    assert "成功 7，失败或待核实 0，取消 19993" in dialog._status.text()
    assert not dialog._progress_timer.isActive()


def test_old_worker_events_do_not_finish_or_overwrite_a_new_operation(dialog, worker_queue):
    dialog._upload_button.click()
    old_worker = worker_queue[-1]
    old_mailbox = dialog._progress_mailbox
    old_worker.complete()
    dialog._upload_button.click()
    active_worker = worker_queue[-1]
    active_mailbox = dialog._progress_mailbox
    old_mailbox.publish(200, 200, "旧进度")
    old_worker.result.emit(UploadReceipt(0, 100))
    old_worker.error.emit("旧错误")
    old_worker.finished.emit()
    assert dialog._worker is active_worker
    assert dialog._progress_mailbox is active_mailbox
    assert dialog._outcome is None
    assert dialog._error is None
    active_mailbox.publish(0, 0, "正在读取云端文件")
    dialog._consume_progress()
    assert dialog._progress.maximum() == 0
    assert dialog._progress.format() == "处理中…"
    assert dialog._status.text() == "正在读取云端文件"
    active_worker.complete()
    assert dialog._upload_button.isEnabled()


def test_progress_does_not_reapply_unchanged_semantic_style(dialog, monkeypatch):
    styled = []
    monkeypatch.setattr(module.ComponentStyle, "apply_state", lambda widget, state: styled.append((widget, state)))
    for current in range(100):
        dialog._render_progress(current, 100, f"正在处理 {current}")
    assert styled == []
    dialog._set_status("失败", module.SemanticState.ERROR)
    dialog._set_status("另一个错误", module.SemanticState.ERROR)
    assert styled == [(dialog._status, module.SemanticState.ERROR)]


@pytest.mark.parametrize(
    "details", [tuple(f"文件 {i}：成功" for i in range(20_000)), ("详细原因" * 20_000,), ("详细原因\n" * 20_000,)]
)
def test_large_receipt_details_have_a_visible_rendering_limit(dialog, details):
    dialog._render_receipt(UploadReceipt(20_000, 0, details=details))
    text = dialog._details.toPlainText()
    assert len(text) < 32_100
    assert len(text.splitlines()) <= 201
    assert "已截断显示；统计包含全部结果" in text
    assert "成功 20000" in dialog._status.text()


def test_twenty_thousand_background_updates_keep_gui_heartbeat_responsive(qapp, monkeypatch):
    published, release = Event(), Event()
    worker_threads, render_threads, rendered, heartbeat = [], [], [], []
    main_thread = get_ident()
    original_render = module.CategorizedUploadDialog._render_progress

    def load_preview(progress, _cancel):
        worker_threads.append(get_ident())
        for current in range(1, 20_001):
            progress(current, 20_000, "正在保存关联")
        published.set()
        if not release.wait(5):
            raise TimeoutError("测试未释放后台线程")
        return _sources()

    def render(subject, current, total, message):
        render_threads.append(get_ident())
        rendered.append(current)
        original_render(subject, current, total, message)

    monkeypatch.setattr(module.CategorizedUploadDialog, "_render_progress", render)
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: heartbeat.append(get_ident()))
    timer.start()
    subject = _dialog(load_preview=load_preview)
    subject.show()
    try:
        for _ in range(200):
            QTest.qWait(5)
            if published.is_set() and rendered and len(heartbeat) >= 5:
                break
        assert published.is_set()
        assert len(worker_threads) == 1
        assert worker_threads[0] != main_thread
        assert render_threads and all(thread == main_thread for thread in render_threads)
        assert rendered[-1] == 20_000
        assert len(rendered) < 20
        assert len(heartbeat) >= 5
        assert subject._progress.text() == "已处理 20000/20000（100%）"
        assert subject._worker is not None
    finally:
        release.set()
        for _ in range(200):
            QTest.qWait(5)
            if subject._worker is None:
                break
        timer.stop()
        subject.close()
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert subject._worker is None


def test_window_modal_upload_keeps_heartbeat_and_cancel_then_restores_parent(qapp):
    parent = QWidget()
    layout = QVBoxLayout(parent)
    upload_card = QWidget(parent)
    layout.addWidget(upload_card)
    edit_button = QPushButton("编辑工作台", parent)
    layout.addWidget(edit_button)
    parent.show()
    started, release = Event(), Event()
    heartbeat = []

    def upload(_selection, _progress, cancel):
        started.set()
        if not cancel.wait(5) or not release.wait(5):
            raise TimeoutError("测试未取消或释放后台任务")
        return UploadReceipt(0, 0)

    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: heartbeat.append(get_ident()))
    timer.start()
    subject = _dialog(parent=upload_card, upload=upload)
    subject.show()
    try:
        for _ in range(200):
            QTest.qWait(5)
            if subject._worker is None:
                break
        assert subject._upload_button.isEnabled()
        subject._upload_button.click()
        for _ in range(100):
            QTest.qWait(5)
            if started.is_set() and len(heartbeat) >= 5:
                break
        assert subject.windowModality() == Qt.WindowModality.WindowModal
        assert QApplication.activeModalWidget() is subject
        assert started.is_set() and len(heartbeat) >= 5
        QTest.mouseClick(subject._cancel, Qt.MouseButton.LeftButton)
        assert subject._cancel_event.is_set()
        assert "正在取消" in subject._status.text()
        assert not subject._cancel.isEnabled()
        assert QApplication.activeModalWidget() is subject
    finally:
        subject._cancel_event.set()
        release.set()
        for _ in range(200):
            QTest.qWait(5)
            if subject._worker is None:
                break
        timer.stop()
        assert subject._worker is None
        subject.close()
        qapp.processEvents()
        assert QApplication.activeModalWidget() is not subject
        edits = []
        edit_button.clicked.connect(lambda: edits.append(True))
        QTest.mouseClick(edit_button, Qt.MouseButton.LeftButton)
        assert edits == [True]
        parent.close()
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
