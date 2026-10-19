"""Keep cloud project navigation usable in its supported narrow sidebar."""

from __future__ import annotations

from dataclasses import replace
import os
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QPushButton
import pytest

from transbridge.ui.foundation.builtins import DEFAULT_THEME_ID, create_builtin_registry
from transbridge.ui.foundation.model import ThemeScheme
from transbridge.ui.foundation.qt_palette import compile_palette
from transbridge.ui.foundation.theme_service import ThemeSnapshot
from transbridge.ui.foundation.visual_style import compile_application_stylesheet
from transbridge.ui.paratranz import project_panel


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    stylesheet = app.styleSheet()
    app.setStyleSheet("")
    yield app
    app.setStyleSheet(stylesheet)


class _Context(QObject):
    config_changed = pyqtSignal(object)
    project_list_changed = pyqtSignal()

    def __init__(self, *, token=""):
        super().__init__()
        self.config = SimpleNamespace(token=token, user_id=7)
        self.active_project_id = "local-project"
        self.current_project = None
        self.mine_project_ids = set()


class _LoadWorker(QObject):
    result = pyqtSignal(object)
    error = pyqtSignal(str)

    def start(self):
        pass


def _apply_font_and_theme(panel, font_size, scheme):
    font = QFont(panel.font())
    font.setPointSizeF(font_size)
    panel.setFont(font)
    if scheme is None:
        return
    definition = create_builtin_registry().resolve(DEFAULT_THEME_ID, scheme)
    primitives = definition.tokens.primitives
    typography = replace(primitives.typography, body_size=font_size)
    tokens = replace(definition.tokens, primitives=replace(primitives, typography=typography))
    snapshot = ThemeSnapshot(
        1,
        definition.manifest.provider_id,
        definition.manifest.theme_id,
        scheme,
        definition.fingerprint,
        tokens,
        compile_palette(definition),
        "project-panel-layout-test",
    )
    panel.setPalette(snapshot.palette)
    panel.setStyleSheet(compile_application_stylesheet(snapshot))


@pytest.mark.parametrize("width", (200, 240, 300))
@pytest.mark.parametrize("font_size", (10, 14))
@pytest.mark.parametrize("scheme", (None, ThemeScheme.LIGHT, ThemeScheme.DARK), ids=("native", "light", "dark"))
def test_sidebar_actions_fit_narrow_width_and_larger_fonts(qapp, width, font_size, scheme):
    context = _Context()
    panel = project_panel.ProjectListPanel(context)
    _apply_font_and_theme(panel, font_size, scheme)
    panel.resize(width, 640)
    panel.show()
    qapp.processEvents()
    try:
        assert panel.width() == width
        assert panel.minimumSizeHint().width() <= width
        assert panel._bind_btn.geometry().top() > panel._list.geometry().bottom()
        buttons = panel.findChildren(QPushButton)
        assert {button.text() for button in buttons} == {"刷新", "新建", "设为同步目标"}
        for button in buttons:
            assert button.font().pointSizeF() == font_size
            assert panel.contentsRect().contains(button.geometry())
            assert button.width() >= button.minimumSizeHint().width()
            assert button.height() >= button.minimumSizeHint().height()
        assert panel._bind_btn.accessibleName() == "设为当前工程同步目标"
        assert "本地工程绑定" in panel._bind_btn.toolTip()
    finally:
        panel.close()
        panel.deleteLater()
        qapp.processEvents()


def test_initial_missing_token_reports_configuration_state_without_starting_worker(qapp, monkeypatch):
    worker = Mock(side_effect=AssertionError("No cloud request without a token"))
    monkeypatch.setattr(project_panel, "ApiWorker", worker)
    panel = project_panel.ProjectListPanel(_Context())
    try:
        assert panel._status.text() == "未配置 Token"
        assert panel._list.count() == 0
        assert not panel._bind_btn.isEnabled()
        worker.assert_not_called()
    finally:
        panel.close()


def test_load_failure_and_removed_token_keep_existing_status_feedback(qapp, monkeypatch):
    worker = _LoadWorker()
    monkeypatch.setattr(project_panel, "ApiWorker", lambda _fn: worker)
    context = _Context(token="test-only")
    panel = project_panel.ProjectListPanel(context)
    panel.resize(200, 640)
    panel.show()
    qapp.processEvents()
    try:
        assert panel._status.text() == "加载中…"
        error = "无法连接 ParaTranz，请检查网络连接后重试。" * 3
        worker.error.emit(error)
        qapp.processEvents()
        assert panel._status.text() == f"加载失败：{error}"
        assert panel.minimumSizeHint().width() <= 200
        assert panel.width() == 200

        context.config.token = ""
        context.config_changed.emit(None)
        assert panel._status.text() == "未配置 Token"
        assert panel._list.isEnabled()
        assert not panel._bind_btn.isEnabled()
    finally:
        panel.close()


@pytest.mark.parametrize("has_local_project", (True, False))
def test_project_browsing_preserves_explicit_binding_action(qapp, monkeypatch, has_local_project):
    bind = Mock()
    monkeypatch.setattr(project_panel.ProjectListPanel, "_bind_selected_project", bind)
    context = _Context()
    if not has_local_project:
        context.active_project_id = None
    panel = project_panel.ProjectListPanel(context)
    project = {"id": 42, "name": "Fixture project"}
    chosen = []
    panel.project_chosen.connect(chosen.append)
    try:
        panel._on_projects_loaded([project])
        item = panel._list.item(0)
        panel._list.setCurrentItem(item)
        panel._list.itemClicked.emit(item)

        assert context.current_project == project
        assert chosen == [project]
        assert panel._bind_btn.isEnabled() is has_local_project
        bind.assert_not_called()
        panel._bind_btn.click()
        assert bind.called is has_local_project

        panel._search.setText("no matching project")
        assert panel._list.count() == 0
        assert panel._status.text() == "没有匹配的项目（共 1 个）"
        context.config_changed.emit(None)
        assert panel._status.text() == "未配置 Token"
        assert not panel._bind_btn.isEnabled()
    finally:
        panel.close()
