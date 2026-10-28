from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication, QInputDialog, QMainWindow, QWidget
import pytest

from transbridge.application.projections import ProjectionSnapshot, ProjectionStore
from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode
from transbridge.ui import context as context_module
from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.foundation.builtins import create_builtin_registry
from transbridge.ui.foundation.theme_service import ThemeService
from transbridge.ui.main_window import MainWindow
from transbridge.ui.shell.action_catalog import IntentId
from transbridge.ui.workbench.widget import WorkbenchWidget

_APP = QApplication.instance() or QApplication([])


@pytest.mark.parametrize("width", [900, 1130, 1600])
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
                "variants": [{"id": "default", "name": "default"}],
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
        for state in (bar.set_save_saving, lambda: bar.set_save_failed("磁盘暂时不可写，请重试")):
            state()
            _APP.processEvents()
            assert widget.width() == width
            assert bar._project_label.width() >= 120
            assert bar._save_status.width() >= 120
            assert bar._project_label.toolTip() == context.project_name
            assert widget._collection_bar.y() > bar.y() + bar.height()
            assert widget._remote_target.geometry().right() < widget.width()
    finally:
        widget.close()
        theme_view.close()
        service.close()
        context.close_projection()
        store.close()
        _APP.setStyleSheet(original_style)
        _APP.setPalette(original_palette)


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
