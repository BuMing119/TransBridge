"""Production download routing keeps batch launchers and single-source plans reachable."""

from __future__ import annotations

import gc
import os
from types import SimpleNamespace
import weakref

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import fields

from PyQt6.QtCore import QEvent, QObject, pyqtSignal
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMainWindow, QMessageBox
import pytest

from tests.ui.operations.test_production_facade import _ConfirmingDialog, _runtime, _Signal
from transbridge.application.contracts import RequestContext
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.coordinators.operation_coordinator import OperationCoordinator
from transbridge.ui.operations import batch_download_session, production
from transbridge.ui.operations.batch_download_dialog import BatchDownloadDialog
from transbridge.ui.operations.batch_download_view import DownloadPreview, DownloadReceipt, DownloadSourceOption
from transbridge.ui.operations.plan_view import OperationKind
from transbridge.ui.shell.menu_builder import MenuBuilder, MenuCallbacks
from transbridge.ui.workbench.cards.download_card import DownloadCard
from transbridge.ui.workbench.step3 import Step3OpsWidget

_APP = QApplication.instance() or QApplication([])


class Window:
    def __init__(self):
        self.destroyed = _Signal()


def context(*, authoritative=True):
    return SimpleNamespace(
        uses_authoritative_projection=authoritative,
        collection=TranslationEntryCollection((TranslationEntry("key", "key", "Original", "Local", 1, "INFO:NAM1"),)),
        slots={"one": object(), "two": object()},
        esp_path="source.esp",
        config=SimpleNamespace(
            token="configured",
            base_url="https://paratranz.cn",
            user_id=7,
            config_revision=1,
        ),
        current_user={"id": 7},
    )


@pytest.fixture
def composed(monkeypatch):
    launches, uploads = [], []
    runtime = _runtime()

    def launch(ctx, actual_runtime):
        launches.append((ctx, actual_runtime))
        return Window()

    def upload(ctx, *, batch):
        uploads.append((ctx, batch))
        return Window()

    monkeypatch.setattr(production, "open_batch_download", launch)
    monkeypatch.setattr(production, "open_file_upload", upload)
    monkeypatch.setattr(
        production, "ParaTranzSyncDialog", lambda plan, _context, parent: _ConfirmingDialog(plan, parent)
    )
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *_args: pytest.fail("A supported production download must not show fallback")
    )
    facade = production.build_operation_plan_facade(runtime, RequestContext("batch-production"))
    return facade, runtime, launches, uploads


def test_default_production_launches_batch_download_with_exact_process_runtime(composed):
    facade, runtime, launches, uploads = composed
    ctx = context()

    assert facade.supports("download", ctx, batch=True)
    assert facade.supports(OperationKind.DOWNLOAD, ctx, batch=True)
    window = facade.begin_download(ctx, batch=True)

    assert isinstance(window, Window)
    assert launches == [(ctx, runtime)]
    assert uploads == []
    assert facade.tasks is runtime.tasks
    assert facade.active_plan_count == 1
    window.destroyed.emit()
    assert facade.active_plan_count == 0


@pytest.mark.parametrize("entrypoint", ["card", "menu"])
def test_batch_card_and_menu_use_supported_production_launcher(composed, entrypoint):
    facade, runtime, launches, _uploads = composed
    ctx = context()
    card = DownloadCard(ctx, lambda **_kwargs: pytest.fail("Direct download worker must not run"))
    card.bind_operation_plan_facade(facade)
    host = SimpleNamespace(context=ctx, download_card=card, operation_plan_facade=facade)
    try:
        if entrypoint == "card":
            card.batch_btn.click()
        else:
            OperationCoordinator(host).batch_download()
        assert launches == [(ctx, runtime)]
        assert facade.active_plan_count == 1
        next(iter(facade._download_windows.values())).destroyed.emit()
        assert facade.active_plan_count == 0
        assert next(iter(ctx.collection)).translation == "Local"
    finally:
        card.close()
        card.deleteLater()


@pytest.mark.parametrize("entrypoint", ["facade", "card", "menu"])
@pytest.mark.parametrize("authoritative", [False, True])
def test_current_project_single_download_uses_unified_ui_and_legacy_keeps_plan(composed, entrypoint, authoritative):
    facade, runtime, launches, uploads = composed
    ctx = context(authoritative=authoritative)
    card = DownloadCard(ctx, lambda **_kwargs: pytest.fail("Direct download worker must not run"))
    card.bind_operation_plan_facade(facade)
    host = SimpleNamespace(context=ctx, download_card=card, operation_plan_facade=facade)
    try:
        assert facade.supports("download", ctx)
        if entrypoint == "facade":
            window = facade.begin_download(ctx)
        elif entrypoint == "card":
            card.btn.click()
            windows = facade._download_windows if authoritative else facade._coordinator._owned_windows
            window = next(iter(windows.values()))
        else:
            window = OperationCoordinator(host).download()
        assert isinstance(window, Window if authoritative else _ConfirmingDialog)
        if not authoritative:
            assert window.shown
            assert window.plan.kind is OperationKind.DOWNLOAD
        assert launches == ([(ctx, runtime)] if authoritative else [])
        assert uploads == []
        assert facade.active_plan_count == 1
        window.destroyed.emit()
        assert facade.active_plan_count == 0
    finally:
        card.close()
        card.deleteLater()


def test_current_project_without_current_collection_can_open_the_source_selector(composed):
    facade, runtime, launches, _uploads = composed
    ctx = context()
    ctx.collection = None
    host = SimpleNamespace(context=ctx, operation_plan_facade=facade)
    window = OperationCoordinator(host).download()
    assert launches == [(ctx, runtime)]
    assert isinstance(window, Window)
    assert facade.active_plan_count == 1
    window.destroyed.emit()
    assert facade.active_plan_count == 0


def test_workbench_download_entry_stays_enabled_with_no_current_collection_and_has_no_batch_button():
    class ProjectContext(QObject):
        collection_changed = pyqtSignal(object)
        paratranz_binding_changed = pyqtSignal(object)
        navigate_to = pyqtSignal(int)

    ctx = ProjectContext()
    ctx.uses_authoritative_projection = True
    ctx.active_project_id = "project"
    ctx.collection = None
    ctx.slots = {"one": object(), "two": object()}
    widget = Step3OpsWidget(ctx)
    try:
        assert widget._card_download.btn.isEnabled()
        assert widget._card_download.title() == "从paratranz上同步"
        assert widget._card_download.btn.text() == "从paratranz上同步"
        assert widget._card_download.batch_btn.isHidden()
        assert not widget._card_upload.btn.isEnabled()
        ctx.collection_changed.emit(None)
        assert widget._card_download.btn.isEnabled()
        assert widget._card_download.batch_btn.isHidden()
    finally:
        widget.close()
        widget.deleteLater()


def test_batch_window_is_retained_until_destroyed_and_count_includes_other_uploads(composed):
    facade, _runtime_value, _launches, _uploads = composed
    ctx = context()
    window = facade.begin_download(ctx, batch=True)
    retained = weakref.ref(window)
    upload = facade.begin_upload(ctx, batch=True)
    del window
    gc.collect()
    assert retained() is not None
    assert facade.active_plan_count == 2
    retained().destroyed.emit()
    gc.collect()
    assert retained() is None
    assert facade.active_plan_count == 1
    upload.destroyed.emit()
    assert facade.active_plan_count == 0


@pytest.mark.parametrize(
    "legacy_context", [SimpleNamespace(collection=()), SimpleNamespace(uses_authoritative_projection=False)]
)
def test_default_production_does_not_claim_legacy_batch_support(composed, legacy_context):
    facade, _runtime_value, launches, _uploads = composed
    assert not facade.supports("download", legacy_context, batch=True)
    assert launches == []


def test_launcher_returning_no_window_does_not_leave_active_plan(monkeypatch, composed):
    facade, _runtime_value, _launches, _uploads = composed
    monkeypatch.setattr(production, "open_batch_download", lambda *_args: None)
    assert facade.begin_download(context(), batch=True) is None
    assert facade.active_plan_count == 0


def test_custom_dialog_factory_preserves_feature_routing_without_batch_launcher(monkeypatch):
    monkeypatch.setattr(production, "open_batch_download", lambda *_args: pytest.fail("Unexpected production launcher"))
    facade = production.build_operation_plan_facade(
        _runtime(), RequestContext("custom-production"), dialog_factory=_ConfirmingDialog
    )
    ctx = context()
    assert not facade.supports("download", ctx, batch=True)
    window = facade.begin_download(ctx)
    assert isinstance(window, _ConfirmingDialog)
    assert window.shown
    window.destroyed.emit()
    assert facade.active_plan_count == 0


@pytest.mark.parametrize("source_count", [1, 2])
@pytest.mark.parametrize("entrypoint", ["menu", "card", "legacy_alias"])
@pytest.mark.parametrize("current_collection", [False, True], ids=["no-current-source", "current-source"])
def test_real_shell_and_current_project_routes_open_the_same_source_selector(
    monkeypatch, source_count, entrypoint, current_collection
):
    calls = []

    class Session:
        def __init__(self, ctx, runtime):
            self.context = ctx
            self.target = SimpleNamespace(project_name="云端项目", project_id=42)
            self.selected = ()
            calls.append(("session", ctx, runtime))

        def load_sources(self, _progress, _cancel):
            return tuple(
                DownloadSourceOption(
                    str(index), f"插件{index}.esp", 3, selected=bool(self.context.collection) and index == 0
                )
                for index in range(source_count)
            )

        def compare(self, source_ids, policy, decisions, _progress, _cancel):
            self.selected = source_ids
            calls.append(("compare", source_ids, policy, decisions))
            return DownloadPreview(len(source_ids), 0, 0)

        def apply(self, _progress, _cancel):
            calls.append(("apply", self.selected))
            return DownloadReceipt(len(self.selected))

    def wait_for(predicate):
        for _ in range(200):
            QTest.qWait(5)
            if predicate():
                return
        pytest.fail("统一下载窗口后台阶段未完成")

    monkeypatch.setattr(batch_download_session, "BatchDownloadSession", Session)
    runtime = _runtime()
    ctx = context()
    ctx.project_name, ctx.active_variant = "测试工程", "默认版本"
    ctx.slots = {str(index): object() for index in range(source_count)}
    if not current_collection:
        ctx.collection = None
    facade = production.build_operation_plan_facade(runtime, RequestContext("unified-download"))
    host = QMainWindow()
    host.context, host.operation_plan_facade = ctx, facade
    coordinator = OperationCoordinator(host)
    host.download_card = DownloadCard(ctx, lambda **_kwargs: pytest.fail("Unexpected direct worker"), parent=host)
    host.download_card.bind_operation_plan_facade(facade)
    callbacks = {field.name: (lambda: None) for field in fields(MenuCallbacks)}
    callbacks.update(download=coordinator.download, batch_download=coordinator.batch_download)
    host.operation_menu = MenuBuilder(host, MenuCallbacks(**callbacks)).build()
    coordinator.update_operation_menu_state()
    host.download_card.update_batch_visibility()
    window = None
    try:
        assert host.operation_menu.download.text() == "从paratranz上同步"
        assert not host.operation_menu.batch_download.isVisible()
        assert host.download_card.batch_btn.isHidden()
        if entrypoint == "menu":
            host.operation_menu.download.trigger()
        elif entrypoint == "card":
            host.download_card.btn.click()
        else:
            coordinator.batch_download()
        window = next(iter(facade._download_windows.values()))
        assert isinstance(window, BatchDownloadDialog)
        assert "批量" not in window.windowTitle()
        wait_for(lambda: window._sources is not None)
        assert window._source_list.count() == source_count
        assert window.selection() == (("0",) if current_collection else ())
        assert window._primary_button.text() == "下载并保存"
        if not current_collection:
            assert not window._primary_button.isEnabled()
            window._select_all.click()
        window._primary_button.click()
        wait_for(lambda: window._worker is None)
        assert window._primary_button.text() == "已保存"
        assert not window._primary_button.isEnabled()
        assert calls[-2][0] == "compare"
        assert calls[-1][0] == "apply"
        assert "保存到当前本地版本" in window._status.text()
        assert facade.active_plan_count == 1
    finally:
        if window is not None:
            while window._worker is not None:
                QTest.qWait(5)
            window.close()
        host.close()
        _APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert facade.active_plan_count == 0
