from __future__ import annotations

import inspect
from pathlib import Path
import re

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication, QLabel, QSizePolicy, QWidget
import pytest

from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.foundation.builtins import DEFAULT_THEME_ID, create_builtin_registry
from transbridge.ui.foundation.model import ThemeScheme
from transbridge.ui.foundation.qt_palette import compile_palette, qcolor
from transbridge.ui.foundation.theme_service import ThemeSnapshot
from transbridge.ui.tools.smart_assistant.message_bubble import MessageBubble
from transbridge.ui.tools.smart_assistant.panel import SmartAssistantPanel
from transbridge.ui.tools.smart_assistant.quick_actions import QuickActionsChips
from transbridge.ui.tools.smart_assistant.session_list_widget import SessionListWidget
from transbridge.ui.tools.smart_assistant.task_monitor import TaskMonitorWidget
from transbridge.ui.tools.smart_assistant.theme_support import SmartAssistantTheme
from transbridge.ui.tools.smart_assistant.thinking_indicator import ThinkingIndicator


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _snapshot(scheme: ThemeScheme, revision: int) -> ThemeSnapshot:
    definition = create_builtin_registry().resolve(DEFAULT_THEME_ID, scheme)
    return ThemeSnapshot(
        revision=revision,
        provider_id=definition.manifest.provider_id,
        theme_id=definition.manifest.theme_id,
        effective_scheme=scheme,
        fingerprint=definition.fingerprint,
        tokens=definition.tokens,
        palette=compile_palette(definition),
        cache_namespace=f"smart-assistant-test:{definition.fingerprint[:16]}",
    )


class _FakeThemeService(QObject):
    theme_changed = pyqtSignal(int, object)

    def __init__(self, snapshot: ThemeSnapshot) -> None:
        super().__init__()
        self._snapshot = snapshot

    def snapshot(self) -> ThemeSnapshot:
        return self._snapshot

    def publish(self, snapshot: ThemeSnapshot) -> None:
        self._snapshot = snapshot
        self.theme_changed.emit(snapshot.revision, snapshot)


class _ThemeTarget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.revisions: list[int] = []
        self.shutdown_count = 0

    def apply_theme(self, theme: SmartAssistantTheme) -> None:
        self.revisions.append(theme.revision)

    def shutdown(self, *, wait_for_worker: bool = True) -> None:
        self.shutdown_count += 1


def test_panel_theme_view_is_keyword_only_and_releases_subscription(qapp, monkeypatch) -> None:
    signature = inspect.signature(SmartAssistantPanel.__init__)
    assert signature.parameters["theme_view"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["theme_view"].default is None

    targets: list[_ThemeTarget] = []

    def init_ui(panel, _ctx) -> None:
        targets.extend([_ThemeTarget(), _ThemeTarget(), _ThemeTarget()])
        panel._session_list, panel._chat, panel._task_monitor = targets

    monkeypatch.setattr(SmartAssistantPanel, "_init_skills", lambda self: None)
    monkeypatch.setattr(SmartAssistantPanel, "_init_session_manager", lambda self: setattr(self, "_session_mgr", None))
    monkeypatch.setattr(SmartAssistantPanel, "_init_ui", init_ui)
    monkeypatch.setattr(SmartAssistantPanel, "_restore_last_session", lambda self: None)
    service = _FakeThemeService(_snapshot(ThemeScheme.LIGHT, 1))
    view = ThemeView(service)  # type: ignore[arg-type]
    panel = SmartAssistantPanel(object(), theme_view=view)
    assert view.active_subscription_count == 1

    service.publish(_snapshot(ThemeScheme.DARK, 2))
    assert panel.theme_revision == 2
    assert [target.revisions for target in targets] == [[2], [2], [2]]

    panel.dispose(wait_for_worker=False)
    panel.dispose(wait_for_worker=False)
    assert view.active_subscription_count == 0
    assert targets[1].shutdown_count == 1


def test_theme_refresh_preserves_message_session_task_and_thinking_state(qapp) -> None:
    theme = SmartAssistantTheme(_snapshot(ThemeScheme.LIGHT, 1))
    bubble = MessageBubble("**streamed** content", "assistant", theme=theme)
    sessions = SessionListWidget(theme=theme)
    sessions.set_sessions([{"session_id": "s1", "name": "Keep me", "message_count": 3}])
    sessions.set_active("s1")
    tasks = TaskMonitorWidget(theme=theme)
    task_data = [{"task_id": "t1", "status": "running", "progress": {"current": 1, "total": 4}}]
    tasks.refresh(task_data)
    thinking = ThinkingIndicator(theme=theme)
    thinking.set_thought("retain reasoning text")

    theme.update(_snapshot(ThemeScheme.DARK, 2))
    bubble.apply_theme(theme)
    sessions.apply_theme(theme)
    tasks.apply_theme(theme)
    thinking.apply_theme(theme)

    assert bubble.text == "**streamed** content"
    assert sessions._active_sid == "s1"
    assert sessions._rows["s1"].property("active") is True
    assert tasks._tasks == {"t1": task_data[0]}
    assert thinking.thought_text == "retain reasoning text"
    assert "running" in tasks._cards[0].accessibleDescription()


def test_message_markdown_uses_explicit_readable_surface_colours(qapp) -> None:
    snapshot = _snapshot(ThemeScheme.LIGHT, 1)
    theme = SmartAssistantTheme(snapshot)
    assistant = MessageBubble("**助手正文**", "assistant", theme=theme)
    user = MessageBubble("用户正文", "user", theme=theme)

    primary = qcolor(snapshot.tokens.semantic.text_primary).name().lower()
    surface = qcolor(snapshot.tokens.semantic.surface).name().lower()
    surface_alt = qcolor(snapshot.tokens.semantic.surface_alt).name().lower()
    assert primary in assistant._content.styleSheet().lower()
    assert surface in assistant._content.styleSheet().lower()
    assert primary in user._content.styleSheet().lower()
    assert surface_alt in user._content.styleSheet().lower()

    assistant_labels = assistant._content.findChildren(QLabel)
    user_labels = user._content.findChildren(QLabel)
    assert assistant_labels
    assert user_labels
    assert all(primary in label.styleSheet().lower() for label in assistant_labels)
    assert all(primary in label.styleSheet().lower() for label in user_labels)
    assert all(primary in label.text().lower() for label in assistant_labels)
    assert all(primary in label.text().lower() for label in user_labels)
    assert assistant._content.layout().count() == 1
    assert user._content.layout().count() == 1
    assert assistant.sizePolicy().verticalPolicy() is QSizePolicy.Policy.Maximum
    assert user.sizePolicy().verticalPolicy() is QSizePolicy.Policy.Maximum
    assert assistant._content_column.sizePolicy().horizontalPolicy() is QSizePolicy.Policy.Expanding
    assert assistant.layout().stretch(1) == 1
    assert user._content_column.sizePolicy().horizontalPolicy() is QSizePolicy.Policy.Preferred
    assistant.resize(840, 200)
    assistant.show()
    qapp.processEvents()
    assert assistant._content_wrapper.width() >= 700
    assistant.hide()

    fallback = MessageBubble("无 ThemeView 时也必须可读", "assistant", theme=SmartAssistantTheme())
    palette = qapp.palette()
    assert palette.color(QPalette.ColorRole.Text).name().lower() in fallback._content.styleSheet().lower()
    assert palette.color(QPalette.ColorRole.Base).name().lower() in fallback._content.styleSheet().lower()
    assert all(
        palette.color(QPalette.ColorRole.Text).name().lower() in label.styleSheet().lower()
        for label in fallback._content.findChildren(QLabel)
    )
    assert all(
        palette.color(QPalette.ColorRole.Text).name().lower() in label.text().lower()
        for label in fallback._content.findChildren(QLabel)
    )


def test_session_list_search_and_collapse_are_functional(qapp) -> None:
    sessions = SessionListWidget(theme=SmartAssistantTheme(_snapshot(ThemeScheme.LIGHT, 1)))
    sessions.set_sessions([
        {"session_id": "s1", "name": "检查地名翻译", "message_count": 3},
        {"session_id": "s2", "name": "术语一致性", "message_count": 8},
    ])
    sessions._search_input.setText("术语")
    qapp.processEvents()
    assert set(sessions._rows) == {"s2"}

    sessions.set_collapsed(True)
    assert sessions.maximumWidth() == 48
    assert not sessions._scroll.isVisible()
    sessions.set_collapsed(False)
    assert sessions.maximumWidth() == 260


def test_quick_actions_and_status_widgets_have_non_colour_metadata(qapp) -> None:
    chips = QuickActionsChips()
    assert chips.accessibleName() == "快捷操作"
    assert all(button.accessibleName() for button in chips._buttons)
    thinking = ThinkingIndicator()
    assert thinking.accessibleName()
    assert thinking.accessibleDescription()


def test_quick_actions_reduce_secondary_tools_at_narrow_width(qapp) -> None:
    chips = QuickActionsChips()
    chips.show()
    chips.resize(220, 32)
    qapp.processEvents()
    assert [button.isVisible() for button in chips._buttons] == [True, True, False, False, False]

    chips.resize(500, 32)
    qapp.processEvents()
    assert all(button.isVisible() for button in chips._buttons)


def test_smart_assistant_source_has_no_raw_visual_colours() -> None:
    directory = Path(__file__).parents[4] / "src" / "transbridge" / "ui" / "tools" / "smart_assistant"
    pattern = re.compile(r"#[0-9a-fA-F]{3,8}|rgba?\(|\b(?:QColor|QBrush|QPen)\s*\(")
    findings = {
        path.name: sorted(set(pattern.findall(path.read_text(encoding="utf-8"))))
        for path in directory.glob("*.py")
        if pattern.search(path.read_text(encoding="utf-8"))
    }
    assert findings == {}
