from __future__ import annotations

from dataclasses import replace
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication, QInputDialog, QMainWindow, QMenu, QWidget
import pytest

from transbridge.application.projections import ProjectionSnapshot, ProjectionStore
from transbridge.application.projects import ParaTranzTargetSource, ParaTranzTargetStatus, ResolvedParaTranzTarget
from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode
from transbridge.ui import context as context_module
from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.foundation.builtins import create_builtin_registry
from transbridge.ui.foundation.theme_service import ThemeService
from transbridge.ui.main_window import MainWindow
from transbridge.ui.shell.action_catalog import IntentId
from transbridge.ui.tools.ai_translator.task_registry import AiTaskRegistry
from transbridge.ui.workbench.project_configuration_view import ProjectConfigurationView
from transbridge.ui.workbench.remote_target_view import RemoteTargetView
from transbridge.ui.workbench.terminology_profile_bar import TerminologyProfileBar, TerminologyProfileBarState
from transbridge.ui.workbench.widget import WorkbenchWidget

_APP = QApplication.instance() or QApplication([])


@pytest.mark.parametrize("width", [600, 750, 980])
def test_themed_context_keeps_project_save_and_content_readable(monkeypatch, width):
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: SimpleNamespace(token=""))
    project_name = "这是一个包含非常长名称的翻译工程" * 4
    store = ProjectionStore(
        ProjectionSnapshot(
            "project:layout",
            0,
            0,
            {
                "project_id": "layout",
                "project_name": project_name,
                "variant_id": "default",
                "variants": [{"id": "default", "name": "非常长的版本名称" * 6}],
                "sources": [],
                "entries": [],
            },
        )
    )
    context = context_module.AppContext(project_projection=store)
    assert context.project_name == project_name
    original_style, original_palette = _APP.styleSheet(), QPalette(_APP.palette())
    preferences = SimpleNamespace(
        load=lambda: SimpleNamespace(
            theme_mode=ThemeMode.LIGHT,
            theme_id=DEFAULT_THEME_ID,
            diagnostics=(),
        )
    )
    service = ThemeService(_APP, create_builtin_registry(), preferences)
    service.start()
    theme_view = ThemeView(service)
    widget = WorkbenchWidget(context, theme_view=theme_view)
    try:
        widget.project_bar.refresh()
        widget.resize(width, 700)
        widget.show()
        _APP.processEvents()
        bar = widget.project_bar
        for state, status in (
            (bar.set_save_saving, "正在保存"),
            (lambda: bar.set_save_failed("磁盘暂时不可写，请重试"), "保存失败，可重试"),
        ):
            state()
            _APP.processEvents()
            assert widget.width() == width
            assert bar._project_label.width() >= 120
            assert bar._save_status.width() >= 120
            assert status in bar._save_status.text()
            assert bar.rect().contains(bar._save_status.geometry())
            assert bar._project_label.toolTip() == context.project_name
            assert bar._variant_combo.toolTip() == context.project_variants[0]["name"]
            assert widget._collection_bar.y() > bar.y() + bar.height()
            actions = widget._workflow_actions
            assert abs(widget._collection_combo.mapTo(widget, QPoint()).y() - actions.mapTo(widget, QPoint()).y()) <= 3
            assert widget.rect().contains(actions.mapTo(widget, actions.rect().bottomRight()))
    finally:
        widget.close()
        theme_view.close()
        service.close()
        context.close_projection()
        store.close()
        _APP.setStyleSheet(original_style)
        _APP.setPalette(original_palette)


def test_project_name_menu_exposes_history_and_configuration(monkeypatch):
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: SimpleNamespace(token=""))
    operations = []
    monkeypatch.setattr(AiTaskRegistry, "open_history", lambda _registry: operations.append("history"))
    widget = WorkbenchWidget(context_module.AppContext())
    configuration = widget._project_configuration
    widget.project_bar.new_project_requested.connect(lambda: operations.append("new"))
    widget.project_bar.open_project_requested.connect(lambda: operations.append("open"))

    def execute(menu, *_args):
        items = {action.text(): action for action in menu.actions()}
        assert menu.toolTipsVisible()
        assert items["项目配置…"] is configuration.action
        assert "ParaTranz · 未绑定" in configuration.action.toolTip()
        for title in ("新建工程…", "打开工程…", "AI 任务记录…", "项目配置…"):
            items[title].trigger()

    monkeypatch.setattr(QMenu, "exec", execute)
    try:
        assert not configuration.isHidden()
        assert not widget._remote_target.isHidden()
        widget.project_bar._rename_btn.click()
        assert operations == ["new", "open", "history"]
        assert not configuration.panel.isHidden()
        configuration._collapse.click()
        assert not configuration.isHidden()
        assert configuration.panel.isHidden()
        assert not widget._remote_target.isHidden()
    finally:
        widget.close()


def test_content_menu_stays_hidden_until_opened_and_routes_existing_import(monkeypatch):
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: SimpleNamespace(token=""))
    widget = WorkbenchWidget(context_module.AppContext())
    widget.resize(750, 650)
    widget.show()
    _APP.processEvents()
    menu = widget._workflow_actions._more_button.menu()
    intents = []
    popup = []
    widget.intent_requested.connect(intents.append)

    def select_import():
        popup.append(menu.isVisible())
        menu.close()
        widget._btn_import.trigger()

    menu.aboutToShow.connect(lambda: QTimer.singleShot(0, select_import))
    try:
        assert menu.windowType() == Qt.WindowType.Popup
        assert not menu.isVisible()
        widget.open_management_menu()
        assert popup == [True]
        assert intents == [IntentId.SOURCE_MIGRATE.value]
    finally:
        widget.close()


def test_collapsed_configuration_exposes_load_preview_and_remote_failure(monkeypatch):
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: SimpleNamespace(token=""))
    context = context_module.AppContext()
    target = ResolvedParaTranzTarget(
        7,
        "远端工程",
        "https://paratranz.cn",
        1,
        ParaTranzTargetSource.PROJECT_BINDING,
        ParaTranzTargetStatus.AVAILABLE,
        1,
        "",
    )
    monkeypatch.setattr(context, "resolve_paratranz_target", lambda: target)
    remote = RemoteTargetView(context)
    profile = TerminologyProfileBar()
    view = ProjectConfigurationView(remote, profile)
    try:
        assert not view.isHidden()
        assert not remote.isHidden()
        assert "译名 · 项目默认" in view.action.toolTip()
        assert "ParaTranz · 远端工程" in view.action.toolTip()
        profile.render(TerminologyProfileBarState(loading=True, detail="正在读取译名方案…"))
        assert not view.isHidden()
        assert view._profile_notice.full_text == "正在读取译名方案…"
        profile.render(
            TerminologyProfileBarState(
                selected_revision=SimpleNamespace(name="预览方案", profile_id="sample", revision=1),
            )
        )
        assert "译名预览未应用 · 预览方案" in view._profile_notice.full_text
        assert "当前版本使用 项目默认" in view._profile_notice.full_text
        profile.render(TerminologyProfileBarState(detail="译名方案不可用：只读", load_error="只读"))
        target = replace(target, status=ParaTranzTargetStatus.ACCOUNT_MISMATCH, reason="登录账号已变化")
        remote.refresh()
        assert view.panel.isHidden()
        assert not view._profile_notice.isHidden()
        assert not remote.isHidden()
        assert "译名方案不可用：只读" in view._profile_notice.full_text
        assert "账号不一致" in remote._label.full_text
        assert "登录账号已变化" in remote._label.toolTip()
        view.expand()
        assert not remote.isHidden()
        assert not view.panel.isAncestorOf(remote)
        view._collapse.click()
        assert not remote.isHidden()
    finally:
        view.close()


@pytest.mark.parametrize(
    "signal_name,intent",
    [
        ("snapshot_save_requested", IntentId.PROJECT_SNAPSHOT_SAVE),
        ("snapshot_load_requested", IntentId.PROJECT_SNAPSHOT_LOAD),
    ],
)
def test_cancelled_snapshot_dialog_does_not_reopen(monkeypatch, signal_name, intent):
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: SimpleNamespace(token=""))
    monkeypatch.setattr("transbridge.ui.paratranz.widget.ParaTranzWidget", lambda *_a, **_kw: QWidget())
    monkeypatch.setattr("transbridge.ui.dialogue.controller.DialogueEditorController", lambda *_a, **_kw: None)
    shown = []

    def cancel(*_args, **_kwargs):
        shown.append(intent)
        return "", False

    monkeypatch.setattr(QInputDialog, "getText", cancel)
    window = QMainWindow()
    window._ctx = context_module.AppContext()
    window._theme_view = window._terminology_profile_factory = None
    window._app_runtime = window._runtime_context = None
    window._workers = []
    window._variant_coordinator = SimpleNamespace(switch_variant=lambda *_: None, delete_variant=lambda *_: None)

    def dispatch(received, *_args):
        assert received == intent
        QInputDialog.getText(window, "历史还原点", "名称")

    window._intent_composition = SimpleNamespace(dispatch=dispatch, callback=lambda value: lambda: dispatch(value))
    MainWindow._init_central(window)
    # ShellIntentComposition.start owns the canonical workbench -> dispatch binding.
    window._workbench.intent_requested.connect(dispatch)
    getattr(window._workbench.project_bar, signal_name).emit()
    _APP.processEvents()
    assert shown == [intent]
    window.close()
