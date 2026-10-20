"""Conversation scope notices distinguish mismatches from previews and loading."""

from types import SimpleNamespace

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget
import pytest

from tests.ui.tools.smart_assistant.test_theme_migration import _snapshot
from transbridge.application.contracts import RequestContext
from transbridge.ui.foundation.model import ThemeScheme
from transbridge.ui.foundation.qt_palette import qcolor
from transbridge.ui.tools.smart_assistant.session_workspace import SessionWorkspace
from transbridge.ui.tools.smart_assistant.theme_support import SmartAssistantTheme


class _Context(QObject):
    project_installed = pyqtSignal()
    project_changed = pyqtSignal()
    variant_changed = pyqtSignal(str)

    active_version_identity = ("project-a", "variant-a")
    project_opening = False
    project_view_failed = False
    authoritative_projection_diverged = False


@pytest.fixture
def scope():
    app = QApplication.instance() or QApplication([])
    panel = QWidget()
    panel._disposed = False
    panel._runtime_context = RequestContext("owner")
    panel._theme = SmartAssistantTheme(_snapshot(ThemeScheme.LIGHT, 1))
    panel._sessions = SimpleNamespace(
        values={"session_id": "session-a", "project_id": "project-a", "variant_id": "variant-a"},
        preview=True,
        loading=False,
    )
    panel._navigation = {}
    panel._chat = SimpleNamespace()
    context = _Context()
    workspace = SessionWorkspace(panel, context)
    panel._refresh_session_list = workspace.refresh_banner
    yield workspace
    workspace.banner.close()
    workspace.banner.deleteLater()
    panel.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("preview", [True, False])
def test_matching_conversation_has_no_switch_notice_and_keeps_activation_state(scope, preview):
    scope.panel._sessions.preview = preview
    scope.refresh_banner()
    assert scope.banner.isHidden()
    assert scope.banner.text() == ""
    assert scope.panel._sessions.preview is preview
    assert scope.ready() is not preview


@pytest.mark.parametrize(
    ("project", "variant", "kind", "label", "switch"),
    [
        ("project-b", "variant-b", "other-project", "其他工程 · 仅浏览", "确认切换工程"),
        ("project-a", "variant-b", "other-variant", "其他版本 · 仅浏览", "确认切换版本"),
    ],
)
def test_project_and_variant_previews_have_distinct_scope_notices(scope, project, variant, kind, label, switch):
    scope.panel._sessions.values.update(project_id=project, variant_id=variant)
    scope.refresh_banner()
    assert not scope.banner.isHidden()
    assert scope.banner.property("scopeState") == kind
    assert label in scope.banner.text()
    assert switch in scope.banner.text()
    assert scope.banner.accessibleDescription() == scope.banner.text()
    assert not scope.ready()


def test_returning_workbench_to_preview_scope_removes_switch_notice_without_resuming(scope):
    scope.panel._sessions.values.update(project_id="project-b", variant_id="variant-b")
    scope.refresh_banner()
    assert not scope.banner.isHidden()
    scope.context.active_version_identity = ("project-b", "variant-b")
    scope.context.project_installed.emit()
    assert scope.banner.isHidden()
    assert scope.panel._sessions.preview
    assert not scope.ready()


@pytest.mark.parametrize("flag", ["project_opening", "project_view_failed", "authoritative_projection_diverged"])
def test_incomplete_workbench_never_claims_same_project_is_a_foreign_preview(scope, flag, monkeypatch):
    setattr(scope.context, flag, True)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, title, message: warnings.append((title, message)))
    monkeypatch.setattr(scope, "confirm_switch", lambda *_: pytest.fail("Must not offer a spurious switch"))
    scope.context.project_changed.emit()
    assert scope.banner.property("scopeState") == "workbench-pending"
    assert "确认切换" not in scope.banner.text()
    assert "其他工程" not in scope.banner.text()
    assert not scope.ensure("发送", lambda: pytest.fail("Workbench is not ready"))
    assert warnings and warnings[0][0] == "工作台尚未就绪"


def test_unbound_history_explains_read_only_state_without_promising_a_switch(scope):
    scope.panel._sessions.values.update(project_id=None, variant_id=None)
    scope.refresh_banner()
    assert scope.banner.property("scopeState") == "unavailable"
    assert "仅供浏览" in scope.banner.text()
    assert "新建对话" in scope.banner.text()
    assert "切换" not in scope.banner.text()


def test_loading_and_switching_take_precedence_over_scope_mismatch(scope):
    scope.panel._sessions.values.update(project_id="project-b", variant_id="variant-b")
    scope.panel._sessions.loading = True
    scope.refresh_banner()
    assert scope.banner.property("scopeState") == "loading"
    scope.switching = True
    scope.refresh_banner()
    assert scope.banner.property("scopeState") == "switching"


def test_notice_refreshes_theme_without_changing_scope_or_visibility(scope):
    scope.panel._sessions.values.update(project_id="project-b", variant_id="variant-b")
    scope.refresh_banner()
    previous = scope.banner.text()
    snapshot = _snapshot(ThemeScheme.DARK, 2)
    scope.apply_theme(SmartAssistantTheme(snapshot))
    assert scope.banner.text() == previous
    assert not scope.banner.isHidden()
    assert scope.panel._sessions.preview
    assert scope.banner._surface.palette().color(QPalette.ColorRole.Base) == qcolor(
        snapshot.tokens.semantic.surface_alt
    )
    assert scope.banner._title.palette().color(QPalette.ColorRole.WindowText) == qcolor(snapshot.tokens.semantic.info)
    assert not scope.banner._icon.pixmap().isNull()


@pytest.mark.parametrize("scheme", [ThemeScheme.LIGHT, ThemeScheme.DARK])
def test_notice_renders_a_distinct_surface_after_show_and_resize(scope, scheme):
    scope.panel._sessions.values.update(project_id="project-b", variant_id="variant-b")
    snapshot = _snapshot(scheme, 2)
    scope.apply_theme(SmartAssistantTheme(snapshot))
    scope.refresh_banner()
    scope.banner.resize(680, 80)
    QApplication.processEvents()
    pixels = scope.banner._surface.grab().toImage()
    assert pixels.pixelColor(pixels.width() // 2, 3) == qcolor(snapshot.tokens.semantic.surface_alt)
