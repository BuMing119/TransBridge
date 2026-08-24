from __future__ import annotations

import os
from pathlib import Path
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication, QWidget
import pytest

from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.config.repository import ConfigRepository
from transbridge.config.ui_preferences import ThemeMode, UiPreferenceRepository
from transbridge.ui.foundation import theme_service as theme_service_module
from transbridge.ui.foundation.builtins import DEFAULT_THEME_ID, create_builtin_registry
from transbridge.ui.foundation.model import ThemeScheme
from transbridge.ui.foundation.theme_service import ThemeApplyStatus, ThemePreference, ThemeService


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


def test_start_applies_palette_and_existing_widgets_follow_revision(qapp, tmp_path: Path) -> None:
    widget = QWidget()
    service = ThemeService(qapp, create_builtin_registry(), _preferences(tmp_path))
    changes = []
    service.theme_changed.connect(lambda revision, snapshot: changes.append((revision, snapshot.effective_scheme)))

    started = service.start()
    before = widget.palette().color(QPalette.ColorRole.Window)
    changed = service.set_preference(ThemePreference(ThemeMode.DARK, DEFAULT_THEME_ID), persist=False)
    qapp.processEvents()

    assert started.snapshot is not None
    assert changed.status is ThemeApplyStatus.APPLIED
    assert changed.snapshot is not None
    assert changed.snapshot.effective_scheme is ThemeScheme.DARK
    assert widget.palette().color(QPalette.ColorRole.Window) != before
    assert changes[-1][0] == changed.snapshot.revision
    service.close()
    widget.deleteLater()


def test_repeated_effective_theme_is_idempotent(qapp, tmp_path: Path) -> None:
    service = ThemeService(qapp, create_builtin_registry(), _preferences(tmp_path))
    service.start()
    first = service.set_preference(ThemePreference(ThemeMode.LIGHT, DEFAULT_THEME_ID), persist=False)
    emitted = []
    service.theme_changed.connect(lambda revision, _snapshot: emitted.append(revision))

    repeated = service.set_preference(ThemePreference(ThemeMode.LIGHT, DEFAULT_THEME_ID), persist=False)

    assert repeated.status is ThemeApplyStatus.UNCHANGED
    assert repeated.snapshot is first.snapshot
    assert emitted == []
    service.close()


def test_persist_failure_keeps_session_theme(qapp, tmp_path: Path) -> None:
    healthy = _preferences(tmp_path)
    healthy.save_theme_preference(ThemeMode.LIGHT, DEFAULT_THEME_ID)

    def fail_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        del source, target
        raise OSError("injected theme write failure")

    failing = _preferences(tmp_path, replace_func=fail_replace)
    service = ThemeService(qapp, create_builtin_registry(), failing)
    service.start()

    result = service.set_preference(ThemePreference(ThemeMode.DARK, DEFAULT_THEME_ID), persist=True)

    assert result.snapshot is not None
    assert result.snapshot.effective_scheme is ThemeScheme.DARK
    assert not result.persisted
    assert "ui_theme_preference_write_failed" in result.diagnostics
    assert healthy.load().theme_mode is ThemeMode.LIGHT
    service.close()


def test_unknown_theme_falls_back_atomically(qapp, tmp_path: Path) -> None:
    service = ThemeService(qapp, create_builtin_registry(), _preferences(tmp_path))
    service.start()

    result = service.set_preference(ThemePreference(ThemeMode.DARK, "missing.theme"), persist=False)

    assert result.status is ThemeApplyStatus.FALLBACK
    assert result.snapshot is not None
    assert result.snapshot.theme_id == DEFAULT_THEME_ID
    assert "theme_fallback_builtin" in result.diagnostics
    service.close()


def test_system_signal_uses_event_scheme_and_disconnects_outside_system(qapp, tmp_path: Path) -> None:
    service = ThemeService(qapp, create_builtin_registry(), _preferences(tmp_path))
    started = service.start()
    assert started.snapshot is not None

    qapp.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Dark)
    assert service.snapshot().effective_scheme is ThemeScheme.DARK
    dark_revision = service.snapshot().revision
    qapp.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Dark)
    assert service.snapshot().revision == dark_revision

    service.set_preference(ThemePreference(ThemeMode.LIGHT, DEFAULT_THEME_ID), persist=False)
    light_revision = service.snapshot().revision
    assert not service._style_hints_connected
    qapp.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Dark)
    assert service.snapshot().effective_scheme is ThemeScheme.LIGHT
    assert service.snapshot().revision == light_revision

    service.set_preference(ThemePreference(ThemeMode.SYSTEM, DEFAULT_THEME_ID), persist=False)
    assert service._style_hints_connected
    qapp.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Unknown)
    assert service.snapshot().effective_scheme is ThemeScheme.LIGHT
    service.close()


def test_close_is_idempotent_disconnects_and_rejects_late_apply(qapp, tmp_path: Path) -> None:
    service = ThemeService(qapp, create_builtin_registry(), _preferences(tmp_path))
    service.start()
    before = service.snapshot()

    service.close()
    service.close()
    qapp.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Dark)
    result = service.set_preference(ThemePreference(ThemeMode.DARK, DEFAULT_THEME_ID), persist=False)

    assert not service._style_hints_connected
    assert result.status is ThemeApplyStatus.FAILED
    assert result.snapshot is before
    assert result.diagnostics == ("theme_service_closed",)


def test_wrong_thread_is_fail_fast_without_palette_or_snapshot_mutation(qapp, tmp_path: Path) -> None:
    service = ThemeService(qapp, create_builtin_registry(), _preferences(tmp_path))
    service.start()
    before = service.snapshot()
    errors: list[BaseException] = []

    def apply_from_worker() -> None:
        try:
            service.set_preference(ThemePreference(ThemeMode.DARK, DEFAULT_THEME_ID), persist=False)
        except BaseException as exc:  # noqa: BLE001 - retain exact cross-thread failure evidence
            errors.append(exc)

    worker = threading.Thread(target=apply_from_worker)
    worker.start()
    worker.join()

    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)
    assert str(errors[0]) == "theme_wrong_thread"
    assert service.snapshot() is before
    service.close()


def test_palette_compiles_standard_roles_and_disabled_group(qapp, tmp_path: Path) -> None:
    service = ThemeService(qapp, create_builtin_registry(), _preferences(tmp_path))
    result = service.start()
    assert result.snapshot is not None
    snapshot = result.snapshot
    palette = snapshot.palette
    semantic = snapshot.tokens.semantic

    expected_roles = {
        QPalette.ColorRole.Window: semantic.window,
        QPalette.ColorRole.Text: semantic.text_primary,
        QPalette.ColorRole.Button: semantic.surface,
        QPalette.ColorRole.Link: semantic.focus,
        QPalette.ColorRole.LinkVisited: semantic.success,
    }
    for role, expected in expected_roles.items():
        assert palette.color(role) == QColor(expected.red, expected.green, expected.blue, expected.alpha)
    assert palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text) == QColor(
        semantic.disabled_text.red,
        semantic.disabled_text.green,
        semantic.disabled_text.blue,
        semantic.disabled_text.alpha,
    )
    assert palette.color(QPalette.ColorRole.Highlight) != QColor(
        semantic.selection_background.red,
        semantic.selection_background.green,
        semantic.selection_background.blue,
        semantic.selection_background.alpha,
    )
    service.close()


def test_palette_apply_failure_restores_last_good_snapshot_and_application_palette(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    service = ThemeService(qapp, create_builtin_registry(), _preferences(tmp_path))
    service.start()
    before_snapshot = service.snapshot()
    before_palette = QPalette(qapp.palette())
    real_set_palette = qapp.setPalette
    calls = 0

    def fail_after_apply(palette: QPalette) -> None:
        nonlocal calls
        calls += 1
        real_set_palette(palette)
        if calls == 1:
            raise RuntimeError("injected apply failure")

    monkeypatch.setattr(qapp, "setPalette", fail_after_apply)
    result = service.set_preference(ThemePreference(ThemeMode.DARK, DEFAULT_THEME_ID), persist=False)

    assert result.status is ThemeApplyStatus.FAILED
    assert result.snapshot is before_snapshot
    assert service.snapshot() is before_snapshot
    assert qapp.palette().color(QPalette.ColorRole.Window) == before_palette.color(QPalette.ColorRole.Window)
    assert calls == 2
    service.close()


def test_fusion_unavailable_is_a_non_blocking_fallback(qapp, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(theme_service_module.QStyleFactory, "create", lambda _name: None)
    service = ThemeService(qapp, create_builtin_registry(), _preferences(tmp_path))

    result = service.start()

    assert result.status is ThemeApplyStatus.FALLBACK
    assert result.snapshot is not None
    assert "theme_fusion_style_unavailable" in result.diagnostics
    service.close()


def test_preference_load_failure_uses_default_theme_without_blocking_start(qapp) -> None:
    class FailingPreferences:
        def load(self):
            raise OSError("sensitive config path")

    service = ThemeService(qapp, create_builtin_registry(), FailingPreferences())

    result = service.start()

    assert result.status is ThemeApplyStatus.FALLBACK
    assert result.snapshot is not None
    assert result.snapshot.theme_id == DEFAULT_THEME_ID
    assert result.diagnostics == ("theme_preference_load_failed",)
    service.close()


def test_unexpected_preference_write_exception_keeps_session_theme(qapp, tmp_path: Path, monkeypatch) -> None:
    preferences = _preferences(tmp_path)
    service = ThemeService(qapp, create_builtin_registry(), preferences)
    service.start()
    monkeypatch.setattr(
        preferences,
        "save_theme_preference",
        lambda *_args: (_ for _ in ()).throw(OSError("sensitive config path")),
    )

    result = service.set_preference(ThemePreference(ThemeMode.DARK, DEFAULT_THEME_ID), persist=True)

    assert result.status is ThemeApplyStatus.APPLIED
    assert result.snapshot is service.snapshot()
    assert result.snapshot.effective_scheme is ThemeScheme.DARK
    assert not result.persisted
    assert result.diagnostics == ("ui_theme_preference_write_failed",)
    service.close()
