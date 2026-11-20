from types import SimpleNamespace

from PyQt6.QtWidgets import QMainWindow, QMessageBox

from transbridge.ui.shell import intent_composition
from transbridge.ui.shell.action_catalog import DEFAULT_ACTION_CATALOG, ActionSection, IntentId
from transbridge.ui.shell.intent_composition import ShellIntentComposition
from transbridge.ui.shell.menu_builder import MenuBuilder


class _Noop:
    def __getattr__(self, _name):
        return self

    def __call__(self, *_args, **_kwargs):
        return None


def _window():
    window = QMainWindow()
    window.context = SimpleNamespace(project_name=None, project_opening=False)
    for name in (
        "variant_coordinator",
        "project_transfer_coordinator",
        "tool_windows",
        "operation_coordinator",
        "parse_coordinator",
        "update_controller",
    ):
        setattr(window, name, _Noop())
    window.show_about = _Noop()
    window.show_message = _Noop()
    return window


def test_settings_intent_configures_consent_without_a_project(monkeypatch):
    calls = []
    monkeypatch.setattr(intent_composition, "get_alignment_consent", lambda: SimpleNamespace(configure=calls.append))
    window = _window()
    composition = ShellIntentComposition(window)
    try:
        descriptor = DEFAULT_ACTION_CATALOG.get(IntentId.SETTINGS_TERMINOLOGY_ALIGNMENT)
        assert descriptor.section is ActionSection.SETTINGS
        assert descriptor.label == "AI 术语定位偏好…"
        assert composition.dispatch(descriptor.intent_id).accepted
        assert calls == [window]
    finally:
        composition.close()
        window.close()


def test_settings_menu_can_reenable_remembered_rejection(monkeypatch, isolated_alignment_consent):
    service = isolated_alignment_consent
    assert service.decided and not service.enabled
    monkeypatch.setattr(intent_composition, "get_alignment_consent", lambda: service)
    window = _window()
    composition = ShellIntentComposition(window)
    try:
        MenuBuilder(window, composition.menu_callbacks()).build()
        settings = next(action.menu() for action in window.menuBar().actions() if action.text() == "设置")
        action = next(
            action for action in settings.actions() if action.data() == IntentId.SETTINGS_TERMINOLOGY_ALIGNMENT.value
        )
        assert action.isEnabled()
        action.trigger()
        dialog = window.findChild(QMessageBox)
        assert dialog is not None and dialog.isVisible()
        assert dialog.checkBox().isChecked()
        dialog.button(QMessageBox.StandardButton.Yes).click()
        assert service.enabled and service.decided
    finally:
        composition.close()
        window.close()


def test_ai_service_entry_opens_its_existing_settings_page():
    window = _window()
    opened = []
    window.tool_windows.show_ui_settings = opened.append
    composition = ShellIntentComposition(window)
    try:
        MenuBuilder(window, composition.menu_callbacks()).build()
        settings = next(action.menu() for action in window.menuBar().actions() if action.text() == "设置")
        action = next(action for action in settings.actions() if action.data() == IntentId.SETTINGS_SERVICES)
        assert action.text() == "AI 服务与 API…"
        action.trigger()
        assert opened == ["ai_service"]
    finally:
        composition.close()
        window.close()


def test_clear_cache_intent_is_disabled_without_a_variant():
    window = _window()
    composition = ShellIntentComposition(window)
    try:
        assert not composition.router.availability(IntentId.SETTINGS_ALIGNMENT_CACHE_CLEAR).enabled
        assert not composition.dispatch(IntentId.SETTINGS_ALIGNMENT_CACHE_CLEAR).accepted
    finally:
        composition.close()
        window.close()


def test_clear_cache_intent_uses_current_preview_and_rechecks_opening_state():
    window = _window()
    calls = []
    window.context.active_version_identity = ("project", "variant-b")
    window.workbench = SimpleNamespace(
        preview=SimpleNamespace(
            terminology_preview=SimpleNamespace(
                clear_alignment_cache=lambda: calls.append(window.context.active_version_identity)
            )
        )
    )
    composition = ShellIntentComposition(window)
    try:
        assert composition.router.availability(IntentId.SETTINGS_ALIGNMENT_CACHE_CLEAR).enabled
        assert composition.dispatch(IntentId.SETTINGS_ALIGNMENT_CACHE_CLEAR).accepted
        assert calls == [("project", "variant-b")]
        window.context.project_opening = True
        assert not composition.router.availability(IntentId.SETTINGS_ALIGNMENT_CACHE_CLEAR).enabled
        assert not composition.dispatch(IntentId.SETTINGS_ALIGNMENT_CACHE_CLEAR).accepted
        assert len(calls) == 1
    finally:
        composition.close()
        window.close()
