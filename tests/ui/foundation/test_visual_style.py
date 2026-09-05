from __future__ import annotations

from pathlib import Path
import re

from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.config.repository import ConfigRepository
from transbridge.config.ui_preferences import ThemeMode, UiPreferenceRepository
from transbridge.ui.foundation import theme_service as theme_service_module
from transbridge.ui.foundation.builtins import DEFAULT_THEME_ID, create_builtin_registry
from transbridge.ui.foundation.theme_service import ThemePreference, ThemeService


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _preferences(path: Path) -> UiPreferenceRepository:
    return UiPreferenceRepository(
        ConfigRepository(path, legacy_path=path, credential_store=UnavailableCredentialStore())
    )


def test_theme_service_installs_one_compiled_skin_and_restores_previous_style(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    original = qapp.styleSheet()
    service = ThemeService(qapp, create_builtin_registry(), _preferences(tmp_path / "visual.ini"))
    started = service.start()

    assert started.snapshot is not None
    light = qapp.styleSheet()
    assert 'QToolButton[tbNavItem="true"]:checked' in light
    assert 'QToolButton[tbNavIntent="true"]:hover' in light
    assert "QPushButton#tbNavigationUser:hover" in light
    assert "QPushButton#tbNavigationUser:focus" in light
    assert 'QLabel[tbAvatar="true"]' in light
    assert 'QLabel[tbConnectionState="online"]' in light
    assert 'QMenuBar[tbComponentKind="menu"]' in light
    assert 'QMenuBar[tbComponentKind="menu"]::item:selected' in light
    assert 'QPushButton[tbSummaryItem="true"]' in light
    assert 'QTableView[tbComponentKind="table"]' in light
    assert '*[tbTaskDialog="true"]' in light
    assert 'QTabWidget[tbComponentKind="tabs"]::pane' in light
    assert 'QPushButton[tbComponentKind="button"][tbTaskPrimary="true"]' in light
    assert "QMenu::item:selected:enabled" in light
    assert "QMenu::item:selected:disabled" in light
    assert not re.search(r"#[0-9A-Fa-f]{6,8}\b", light)
    assert "palette(light)" in light
    primary_rule = re.search(r'QPushButton\[tbSemanticState="primary"\].*?\{(.*?)\}', light, re.DOTALL)
    assert primary_rule is not None
    assert "color: palette(link);" in primary_rule.group(1)
    assert "background: palette(light);" in primary_rule.group(1)
    assert "background: palette(link);" not in primary_rule.group(1)

    stylesheet_applies: list[str] = []
    real_set_stylesheet = qapp.setStyleSheet

    def count_stylesheet_apply(stylesheet: str) -> None:
        stylesheet_applies.append(stylesheet)
        real_set_stylesheet(stylesheet)

    monkeypatch.setattr(qapp, "setStyleSheet", count_stylesheet_apply)
    service.set_preference(ThemePreference(ThemeMode.DARK, DEFAULT_THEME_ID), persist=False)
    dark = qapp.styleSheet()
    assert dark == light
    assert stylesheet_applies == []
    assert len(service._stylesheet_cache) == 2

    service.close()
    assert qapp.styleSheet() == original


def test_stylesheet_apply_failure_restores_last_good_skin(qapp, tmp_path: Path, monkeypatch) -> None:
    service = ThemeService(qapp, create_builtin_registry(), _preferences(tmp_path / "failure.ini"))
    service.start()
    before_snapshot = service.snapshot()
    before_stylesheet = qapp.styleSheet()
    real_compile = theme_service_module.compile_application_stylesheet
    monkeypatch.setattr(
        "transbridge.ui.foundation.theme_service.compile_application_stylesheet",
        lambda snapshot: f"{real_compile(snapshot)}\n/* {snapshot.effective_scheme.value} */",
    )
    service._stylesheet_cache.clear()
    real_set_stylesheet = qapp.setStyleSheet
    calls = 0

    def fail_after_apply(stylesheet: str) -> None:
        nonlocal calls
        calls += 1
        real_set_stylesheet(stylesheet)
        if calls == 1:
            raise RuntimeError("injected stylesheet apply failure")

    monkeypatch.setattr(qapp, "setStyleSheet", fail_after_apply)
    result = service.set_preference(ThemePreference(ThemeMode.DARK, DEFAULT_THEME_ID), persist=False)

    assert result.snapshot is before_snapshot
    assert "theme_apply_failed" in result.diagnostics
    assert qapp.styleSheet() == before_stylesheet
    service.close()
