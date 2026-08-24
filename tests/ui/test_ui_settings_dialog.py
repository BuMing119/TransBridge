from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication, QDialog, QWidget
import pytest

from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.config.repository import ConfigRepository
from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode, UiPreferenceRepository
from transbridge.ui.foundation.builtins import create_builtin_registry
from transbridge.ui.foundation.theme_service import ThemePreference, ThemeService
from transbridge.ui.settings_dialog import PersistFailureChoice, SettingsDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _preferences(tmp_path: Path, *, replace_func=os.replace) -> UiPreferenceRepository:
    path = tmp_path / "ui.ini"
    return UiPreferenceRepository(
        ConfigRepository(
            path,
            legacy_path=path,
            credential_store=UnavailableCredentialStore(),
            replace_func=replace_func,
        )
    )


def _dialog(qapp, preferences, *, resolver=None):
    registry = create_builtin_registry()
    service = ThemeService(qapp, registry, preferences)
    service.start()
    dialog = SettingsDialog(
        service,
        preferences,
        registry=registry,
        persist_failure_resolver=resolver,
    )
    return service, dialog


def test_locale_service_translates_critical_settings_chrome(qapp, tmp_path: Path) -> None:
    class _Locale:
        @staticmethod
        def gettext(msgid: str) -> str:
            return f"译:{msgid}"

    preferences = _preferences(tmp_path)
    registry = create_builtin_registry()
    service = ThemeService(qapp, registry, preferences)
    service.start()
    dialog = SettingsDialog(service, preferences, registry=registry, locale_service=_Locale())

    assert dialog.windowTitle() == "译:通用设置"
    assert dialog._mode_combo.itemText(0) == "译:跟随系统"
    assert dialog._theme_combo.accessibleName() == "译:主题提供者"
    assert dialog._effective_scheme.text().startswith("译:")
    assert dialog._apply_button.text() == "译:应用"
    assert dialog._default_button.text() == "译:恢复默认"
    assert dialog._cancel_button.text() == "译:取消"
    dialog._show_notice(dialog._tr("所选主题当前不可用，请选择其他主题。"), error=True)
    assert dialog.last_notice == "译:所选主题当前不可用，请选择其他主题。"

    dialog.reject()
    service.close()


def test_preview_is_isolated_from_application_and_business_widgets(qapp, tmp_path: Path) -> None:
    original_palette = QPalette(qapp.palette())
    preferences = _preferences(tmp_path)
    preferences.save_theme_preference(ThemeMode.LIGHT, DEFAULT_THEME_ID)
    service, dialog = _dialog(qapp, preferences)
    business = QWidget()
    app_before = qapp.palette().color(QPalette.ColorRole.Window)
    business_before = business.palette().color(QPalette.ColorRole.Window)
    revision_before = service.snapshot().revision

    dialog.set_draft(ThemePreference(ThemeMode.DARK, DEFAULT_THEME_ID))
    qapp.processEvents()

    assert dialog.preview.snapshot is not None
    assert dialog.preview.snapshot.effective_scheme.value == "dark"
    assert dialog.preview.palette().color(QPalette.ColorRole.Window) != app_before
    assert qapp.palette().color(QPalette.ColorRole.Window) == app_before
    assert business.palette().color(QPalette.ColorRole.Window) == business_before
    assert service.snapshot().revision == revision_before
    assert service.preference.mode is ThemeMode.LIGHT
    assert preferences.load().theme_mode is ThemeMode.LIGHT
    dialog.reject()
    assert dialog.preview.disposed
    service.close()
    business.deleteLater()
    qapp.setPalette(original_palette)


def test_apply_persists_and_updates_existing_and_new_widgets(qapp, tmp_path: Path) -> None:
    original_palette = QPalette(qapp.palette())
    preferences = _preferences(tmp_path)
    preferences.save_theme_preference(ThemeMode.LIGHT, DEFAULT_THEME_ID)
    service, dialog = _dialog(qapp, preferences)
    existing = QWidget()
    before = existing.palette().color(QPalette.ColorRole.Window)
    dialog.set_draft(ThemePreference(ThemeMode.DARK, DEFAULT_THEME_ID))

    assert dialog.apply_draft()
    qapp.processEvents()
    created_after = QWidget()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.disposed
    assert service.preference.mode is ThemeMode.DARK
    assert preferences.load().theme_mode is ThemeMode.DARK
    assert existing.palette().color(QPalette.ColorRole.Window) != before
    assert created_after.palette().color(QPalette.ColorRole.Window) == existing.palette().color(
        QPalette.ColorRole.Window
    )
    service.close()
    existing.deleteLater()
    created_after.deleteLater()
    qapp.setPalette(original_palette)


@pytest.mark.parametrize(
    ("choice", "expected_mode"),
    [
        (PersistFailureChoice.KEEP_SESSION, ThemeMode.DARK),
        (PersistFailureChoice.RESTORE_PERSISTED, ThemeMode.LIGHT),
    ],
)
def test_write_failure_explicitly_keeps_session_or_restores_persisted(
    qapp,
    tmp_path: Path,
    choice: PersistFailureChoice,
    expected_mode: ThemeMode,
) -> None:
    original_palette = QPalette(qapp.palette())
    healthy = _preferences(tmp_path)
    healthy.save_theme_preference(ThemeMode.LIGHT, DEFAULT_THEME_ID)

    def fail_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        del source, target
        raise OSError(r"C:\secret\transbridge.ini")

    failing = _preferences(tmp_path, replace_func=fail_replace)
    service, dialog = _dialog(qapp, failing, resolver=lambda _result: choice)
    dialog.set_draft(ThemePreference(ThemeMode.DARK, DEFAULT_THEME_ID))

    assert dialog.apply_draft()

    assert service.preference.mode is expected_mode
    assert healthy.load().theme_mode is ThemeMode.LIGHT
    assert "C:\\secret" not in dialog.last_notice
    assert "transbridge.ini" not in dialog.last_notice
    assert dialog.disposed
    service.close()
    qapp.setPalette(original_palette)


def test_unknown_theme_is_rejected_before_apply_or_persistence(qapp, tmp_path: Path) -> None:
    original_palette = QPalette(qapp.palette())
    preferences = _preferences(tmp_path)
    service, dialog = _dialog(qapp, preferences)
    before = service.preference
    revision = service.snapshot().revision
    dialog.set_draft(ThemePreference(ThemeMode.DARK, "removed.provider"))

    assert not dialog.apply_draft()

    assert service.preference == before
    assert service.snapshot().revision == revision
    assert preferences.load().theme_id == DEFAULT_THEME_ID
    assert "不可用" in dialog.last_notice
    assert dialog.result() == 0
    dialog.reject()
    service.close()
    qapp.setPalette(original_palette)


def test_restore_default_is_only_a_draft_and_cancel_releases_preview(qapp, tmp_path: Path) -> None:
    original_palette = QPalette(qapp.palette())
    preferences = _preferences(tmp_path)
    preferences.save_theme_preference(ThemeMode.DARK, DEFAULT_THEME_ID)
    service, dialog = _dialog(qapp, preferences)
    revision = service.snapshot().revision

    dialog.restore_default()

    assert dialog.state.draft_preference == ThemePreference(ThemeMode.SYSTEM, DEFAULT_THEME_ID)
    assert dialog.state.dirty
    assert service.preference.mode is ThemeMode.DARK
    assert service.snapshot().revision == revision
    assert preferences.load().theme_mode is ThemeMode.DARK
    dialog.reject()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert dialog.preview.disposed
    service.close()
    qapp.setPalette(original_palette)


def test_service_settings_uses_an_integration_signal(qapp, tmp_path: Path) -> None:
    original_palette = QPalette(qapp.palette())
    service, dialog = _dialog(qapp, _preferences(tmp_path))
    requests: list[bool] = []
    dialog.service_settings_requested.connect(lambda: requests.append(True))

    dialog.service_settings_requested.emit()

    assert requests == [True]
    dialog.reject()
    service.close()
    qapp.setPalette(original_palette)


def test_preview_deduplicates_fingerprint_and_close_detaches_theme_listener(qapp, tmp_path: Path) -> None:
    original_palette = QPalette(qapp.palette())
    service, dialog = _dialog(qapp, _preferences(tmp_path))
    initial_apply_count = dialog.preview.apply_count

    dialog.set_draft(dialog.state.draft_preference)

    assert dialog.preview.apply_count == initial_apply_count
    dialog.reject()
    service.set_preference(ThemePreference(ThemeMode.DARK, DEFAULT_THEME_ID), persist=False)
    qapp.processEvents()
    assert dialog.preview.disposed
    assert dialog.preview.apply_count == initial_apply_count
    service.close()
    qapp.setPalette(original_palette)
