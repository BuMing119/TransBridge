import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget
import pytest

from tests.ui.workbench.terminology_async_support import wait_until
from transbridge.ui.shell.window_lifecycle import WindowLifecycle
from transbridge.ui.workbench import alignment_consent as consent_module
from transbridge.ui.workbench.alignment_consent import AlignmentConsent, get_alignment_consent

_APP = QApplication.instance() or QApplication([])


class _Settings:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.writes = []

    def value(self, key, default=None, *, type=None):
        value = self.values.get(key, default)
        return value if type is None else type(value)

    def setValue(self, key, value):  # noqa: N802 - QSettings API
        self.values[key] = value
        self.writes.append((key, value))

    def remove(self, key):
        self.values.pop(key, None)
        self.writes.append((key, None))

    def sync(self):
        self.writes.append(("sync", None))


def _ask(service):
    parent = QWidget()
    service.request(parent)
    assert service._dialog is None
    parent.show()
    wait_until(lambda: service._dialog is not None)
    return parent, service._dialog


@pytest.mark.parametrize("enabled", [True, False])
def test_startup_choice_is_once_per_session_and_not_persisted_by_default(enabled):
    settings = _Settings()
    service = AlignmentConsent(settings=settings)
    changes = []
    service.changed.connect(lambda: changes.append((service.enabled, service.decided)))
    parent, dialog = _ask(service)
    second = QWidget()
    second.show()
    service.request(second)
    assert service._dialog is dialog
    button = QMessageBox.StandardButton.Yes if enabled else QMessageBox.StandardButton.No
    dialog.button(button).click()
    assert service.decided and service.enabled is enabled
    assert changes == [(enabled, True)]
    assert settings.writes == []
    service.request(second)
    assert service._dialog is None
    next_start = AlignmentConsent(settings=settings)
    assert not next_start.decided and not next_start.enabled
    parent.close()
    second.close()


@pytest.mark.parametrize("enabled", [True, False])
def test_never_ask_again_remembers_both_enable_and_disable(enabled):
    settings = _Settings()
    service = AlignmentConsent(settings=settings)
    parent, dialog = _ask(service)
    assert dialog.checkBox().text() == "永不再问"
    dialog.checkBox().setChecked(True)
    button = QMessageBox.StandardButton.Yes if enabled else QMessageBox.StandardButton.No
    dialog.button(button).click()
    assert settings.values == {"remember": True, "enabled": enabled}
    next_start = AlignmentConsent(settings=settings)
    next_start.request(parent)
    assert next_start.decided and next_start.enabled is enabled and next_start._dialog is None
    parent.close()


@pytest.mark.parametrize("dismiss", ["reject", "close", "destroy_parent"])
def test_dismissal_disables_only_this_launch_without_persisting_checkbox(dismiss):
    settings = _Settings()
    service = AlignmentConsent(settings=settings)
    parent, dialog = _ask(service)
    dialog.checkBox().setChecked(True)
    if dismiss == "destroy_parent":
        sip.delete(parent)
    elif dismiss == "close":
        dialog.close()
    else:
        dialog.reject()
    wait_until(lambda: service.decided)
    assert not service.enabled
    assert settings.writes == []
    if not sip.isdeleted(parent):
        parent.close()


def test_first_visible_window_owns_the_one_prompt():
    service = AlignmentConsent(settings=_Settings())
    first, second = QWidget(), QWidget()
    service.request(first)
    service.request(second)
    second.show()
    wait_until(lambda: service._dialog is not None)
    dialog = service._dialog
    first.show()
    _APP.processEvents()
    assert dialog is service._dialog and dialog.parent() is second
    dialog.reject()
    first.close()
    second.close()


def test_configure_can_change_remembered_choice_and_restore_startup_question():
    settings = _Settings({"remember": True, "enabled": False})
    service = AlignmentConsent(settings=settings)
    parent = QWidget()
    parent.show()
    service.configure(parent)
    dialog = service._dialog
    assert dialog.checkBox().isChecked()
    dialog.checkBox().setChecked(False)
    dialog.button(QMessageBox.StandardButton.Yes).click()
    assert service.enabled and service.decided
    assert settings.values == {"remember": False}
    assert not AlignmentConsent(settings=settings).decided
    parent.close()


def test_getter_uses_one_qapplication_owned_service(monkeypatch):
    monkeypatch.setattr(_APP, consent_module._APP_ATTRIBUTE, None)
    settings = _Settings()
    monkeypatch.setattr(consent_module, "QSettings", lambda *_args: settings)
    first, second = get_alignment_consent(), get_alignment_consent()
    assert first is second
    assert first.parent() is _APP
    assert settings.writes == []
    first.deleteLater()


def test_deferred_old_dialog_destruction_does_not_dismiss_reopened_settings():
    service = AlignmentConsent(settings=_Settings())
    parent, first = _ask(service)
    first.button(QMessageBox.StandardButton.No).click()
    service.configure(parent)
    second = service._dialog
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert sip.isdeleted(first)
    assert service._dialog is second and second.isVisible()
    second.button(QMessageBox.StandardButton.Yes).click()
    assert service.enabled
    parent.close()


def test_dialog_disconnects_callbacks_when_consent_service_is_destroyed():
    service = AlignmentConsent(settings=_Settings())
    parent, dialog = _ask(service)
    changes = []
    service.changed.connect(lambda: changes.append(True))
    sip.delete(service)
    dialog.button(QMessageBox.StandardButton.Yes).click()
    sip.delete(parent)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert changes == []


def test_window_start_does_not_request_alignment_consent(monkeypatch):
    class Context(QObject):
        dirty_changed = pyqtSignal()

    calls = []
    host = QWidget()
    host.context = Context()
    service = SimpleNamespace(request=lambda parent: calls.append(parent))
    monkeypatch.setattr(consent_module, "get_alignment_consent", lambda: service)
    lifecycle = WindowLifecycle.__new__(WindowLifecycle)
    lifecycle._host = host
    lifecycle.auto_saver = SimpleNamespace(start=lambda: None, trigger_debounce=lambda: None)
    lifecycle.start()
    assert calls == []
    assert not host.isVisible()
    host.close()
