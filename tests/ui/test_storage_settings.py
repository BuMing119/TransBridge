from types import SimpleNamespace

from PyQt6.QtWidgets import QMessageBox
import pytest

from transbridge.ui.settings import storage_page, storage_restart
from transbridge.ui.settings.sections import SettingsSection
from transbridge.ui.shell.tool_windows import ToolWindows

from . import test_ui_settings_dialog
from .test_ui_settings_dialog import _dialog, _preferences

qapp = test_ui_settings_dialog.qapp


@pytest.mark.parametrize("answer", [QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Yes])
def test_settings_require_confirmation_for_final_directory(tmp_path, monkeypatch, qapp, answer):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    service, dialog = _dialog(qapp, _preferences(tmp_path), initial_section="storage", storage_blocker=lambda: "")
    page = dialog._settings_pages[SettingsSection.STORAGE]
    assert dialog.current_section is SettingsSection.STORAGE
    assert not page.move.isEnabled()
    monkeypatch.setattr(storage_page.QFileDialog, "getExistingDirectory", lambda *_: str(tmp_path / "custom"))
    page.choose.click()
    expected = tmp_path / "custom" / "TransBridge"
    assert page.preview.text() == str(expected)
    assert not expected.exists()
    assert dialog.storage_destination is None
    monkeypatch.setattr(QMessageBox, "question", lambda *_: answer)
    page.move.click()
    assert dialog.storage_destination == (expected if answer == QMessageBox.StandardButton.Yes else None)
    assert not expected.exists()
    dialog.reject()
    service.close()


@pytest.mark.parametrize("dirty,active", [(True, False), (False, True), (False, False)])
def test_move_waits_for_project_save_and_background_tasks(qapp, dirty, active):
    host = SimpleNamespace(
        context=SimpleNamespace(dirty=dirty),
        app_runtime=SimpleNamespace(
            tasks=SimpleNamespace(list=lambda actor: [SimpleNamespace(is_terminal=not active)])
        ),
        workbench=SimpleNamespace(ai_tasks=SimpleNamespace(busy=False)),
    )
    assert bool(ToolWindows(host)._storage_migration_blocker()) == (dirty or active)


def test_after_window_shutdown_move_finishes_before_restart(tmp_path, monkeypatch, qapp):
    events = []

    def move(destination, *, progress):
        events.append(destination)
        progress("正在复制")
        return ()

    monkeypatch.setattr(storage_restart, "migrate_storage", move)
    monkeypatch.setattr(
        storage_restart.QProcess, "startDetached", lambda program, args: events.append(tuple(args)) or (True, 1)
    )
    destination = tmp_path / "TransBridge"
    assert storage_restart.migrate_and_restart(destination) == 0
    assert events == [destination, ("-m", "transbridge.cli", "gui")]
