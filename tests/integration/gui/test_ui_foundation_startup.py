from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.config.repository import ConfigRepository
from transbridge.config.ui_preferences import (
    DEFAULT_THEME_ID,
    GuidanceMode,
    ThemeMode,
    UiPreferenceRepository,
)
from transbridge.ui import app as app_module
from transbridge.ui.foundation.runtime import GuiFoundation


def _preferences(tmp_path: Path) -> UiPreferenceRepository:
    path = tmp_path / "transbridge.ini"
    return UiPreferenceRepository(
        ConfigRepository(
            path,
            legacy_path=path,
            credential_store=UnavailableCredentialStore(),
        )
    )


def test_real_qapplication_foundation_startup_preserves_atomic_ui_preferences(tmp_path: Path) -> None:
    application = QApplication.instance() or QApplication([])
    preferences = _preferences(tmp_path)
    assert preferences.save_guidance_mode(GuidanceMode.GUIDED).saved
    assert preferences.save_theme_preference(ThemeMode.DARK, DEFAULT_THEME_ID).saved
    assert preferences.save_locale("en-US").saved
    invalid_theme = preferences.save_theme_preference(ThemeMode.LIGHT, "example..theme")
    assert not invalid_theme.saved
    assert invalid_theme.diagnostic_code == "ui_theme_id_invalid"

    foundation = GuiFoundation.create(application, preferences)
    snapshot = preferences.load()

    assert foundation.initial_theme_result.snapshot is not None
    assert foundation.initial_theme_result.snapshot.effective_scheme.value == "dark"
    assert snapshot.guidance_mode is GuidanceMode.GUIDED
    assert snapshot.theme_mode is ThemeMode.DARK
    assert snapshot.theme_id == DEFAULT_THEME_ID
    assert snapshot.locale == "en-US"
    foundation.close()


@dataclass
class _UseCases:
    preferences: UiPreferenceRepository

    def names(self):
        return ("ui_preferences",)

    def resolve(self, name: str):
        if name == "ui_preferences":
            return self.preferences
        return object()


class _Runtime:
    def __init__(self, preferences: UiPreferenceRepository, events: list[str]) -> None:
        self.use_cases = _UseCases(preferences)
        self.tasks = object()
        self._events = events

    def close(self) -> None:
        self._events.append("runtime.close")


def test_gui_entrypoint_creates_foundation_before_window_and_closes_it_before_runtime(
    tmp_path: Path, monkeypatch
) -> None:
    application = QApplication.instance() or QApplication([])
    events: list[str] = []
    runtime = _Runtime(_preferences(tmp_path), events)
    captured: dict[str, object] = {}

    class Window:
        def __init__(self, **kwargs) -> None:
            foundation = kwargs["ui_foundation"]
            assert foundation.initial_theme_result.snapshot is not None
            captured.update(kwargs)
            events.append("window.create")

        def show(self) -> None:
            events.append("window.show")

    original_close = GuiFoundation.close

    def close_foundation(self: GuiFoundation) -> None:
        events.append("foundation.close")
        original_close(self)

    monkeypatch.setattr(app_module, "_setup_logging", lambda: None)
    monkeypatch.setattr(app_module, "bind_runtime", lambda *_args, **_kwargs: SimpleNamespace(context=object()))
    monkeypatch.setattr(app_module, "AppContext", lambda **_kwargs: object())
    monkeypatch.setattr(app_module, "MainWindow", Window)
    monkeypatch.setattr(app_module, "install_accidental_wheel_guard", lambda _application: object())
    monkeypatch.setattr(GuiFoundation, "close", close_foundation)
    monkeypatch.setattr(QApplication, "exec", lambda _self: 0)

    from transbridge.smart_assistant import agents, tools
    from transbridge.smart_assistant.tools.task_manager import TaskManager

    monkeypatch.setattr(agents.AgentRegistry, "init_presets", staticmethod(lambda: None))
    monkeypatch.setattr(tools, "register_all", lambda: None)
    monkeypatch.setattr(TaskManager, "bind_runtime", lambda _self, _runtime: None)

    assert app_module.main(runtime) == 0
    assert captured["runtime"] is runtime
    assert captured["ui_foundation"] is application._transbridge_ui_foundation
    assert events == ["window.create", "window.show", "foundation.close", "runtime.close"]


def test_gui_entrypoint_keeps_business_window_available_when_foundation_startup_fails(
    tmp_path: Path, monkeypatch
) -> None:
    QApplication.instance() or QApplication([])
    events: list[str] = []
    runtime = _Runtime(_preferences(tmp_path), events)
    captured: dict[str, object] = {}

    class Window:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)
            events.append("window.create")

        def show(self) -> None:
            events.append("window.show")

    monkeypatch.setattr(app_module, "_setup_logging", lambda: None)
    monkeypatch.setattr(app_module, "bind_runtime", lambda *_args, **_kwargs: SimpleNamespace(context=object()))
    monkeypatch.setattr(app_module, "AppContext", lambda **_kwargs: object())
    monkeypatch.setattr(app_module, "MainWindow", Window)
    monkeypatch.setattr(app_module, "install_accidental_wheel_guard", lambda _application: object())
    monkeypatch.setattr(
        GuiFoundation, "create", classmethod(lambda _cls, *_args: (_ for _ in ()).throw(RuntimeError()))
    )
    monkeypatch.setattr(QApplication, "exec", lambda _self: 0)

    from transbridge.smart_assistant import agents, tools
    from transbridge.smart_assistant.tools.task_manager import TaskManager

    monkeypatch.setattr(agents.AgentRegistry, "init_presets", staticmethod(lambda: None))
    monkeypatch.setattr(tools, "register_all", lambda: None)
    monkeypatch.setattr(TaskManager, "bind_runtime", lambda _self, _runtime: None)

    assert app_module.main(runtime) == 0
    assert captured["ui_foundation"] is None
    assert events == ["window.create", "window.show", "runtime.close"]
