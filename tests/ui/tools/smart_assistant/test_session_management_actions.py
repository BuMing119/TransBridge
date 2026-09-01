from __future__ import annotations

from PyQt6.QtWidgets import QApplication, QInputDialog, QWidget
import pytest

from tests.application.sessions.test_gui_session_management import build_session_services
from transbridge.application.contracts import RequestContext
from transbridge.application.sessions import ControllerSnapshot
from transbridge.ui.tools.smart_assistant import panel as module

_APP = QApplication.instance() or QApplication([])


class _Chat(QWidget):
    def __init__(self, context, *, theme=None):
        super().__init__()
        self.context = context
        self.messages = []
        self.loads = []

    def configure_session_port(self, **_kwargs):
        pass

    def set_task_monitor(self, _monitor):
        pass

    def recovery_snapshot(self):
        return list(self.messages), ControllerSnapshot()

    def load_session(self, values):
        self.messages = list(values.get("messages", []))
        self.loads.append(list(self.messages))

    def shutdown(self, **_kwargs):
        pass


@pytest.fixture
def panel_services(tmp_path, monkeypatch):
    commands, lifecycle, repository, projection = build_session_services(tmp_path)
    monkeypatch.setattr(module, "ChatWidget", _Chat)
    monkeypatch.setattr(module.SmartAssistantPanel, "_init_skills", lambda _self: None)
    monkeypatch.setattr(module.SmartAssistantPanel, "_configured_model_name", staticmethod(lambda: "test-model"))
    monkeypatch.setattr(module, "set_window_app_user_model_id", lambda *_args: False)
    panel = module.SmartAssistantPanel(
        object(), session_commands=commands, session_projection=projection, runtime_context=RequestContext("owner")
    )
    _APP.processEvents()
    yield panel, commands, lifecycle, repository
    panel.dispose()
    panel.deleteLater()
    projection.close()
    _APP.processEvents()


def test_rename_menu_persists_name_and_keeps_current_chat_visible(panel_services, monkeypatch):
    panel, commands, lifecycle, repository = panel_services
    ref = lifecycle.active.aggregate.ref
    panel.chat.messages = [{"role": "user", "content": "unsaved conversation"}]
    loads = len(panel.chat.loads)
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kwargs: ("Renamed", True))

    panel._session_list.rename_session.emit(ref.identity.value, "New conversation")

    assert commands.list_sessions()[0]["name"] == "Renamed"
    assert repository.load(ref).value.envelope.data["history"] == panel.chat.messages
    assert len(panel.chat.loads) == loads
    assert panel.chat.messages[0]["content"] == "unsaved conversation"


@pytest.mark.parametrize("with_other", [False, True])
def test_delete_menu_switches_to_existing_or_new_session(panel_services, with_other):
    panel, commands, lifecycle, repository = panel_services
    other = lifecycle.active.aggregate.ref if with_other else None
    if with_other:
        panel._on_create_session("Delete me")
    target = lifecycle.active.aggregate.ref
    panel.chat.messages = [{"role": "user", "content": "deleted conversation"}]

    panel._session_list.delete_session.emit(target.identity.value)

    assert lifecycle.active is not None
    current = lifecycle.active.aggregate.ref
    assert current != target
    if with_other:
        assert current == other
    assert all(row["session_id"] != target.identity.value for row in commands.list_sessions())
    assert panel.chat.messages == []
    assert not repository._filesystem.exists(repository.path_for(target))


def test_delete_menu_displays_failure_and_keeps_session_selectable(panel_services, monkeypatch):
    panel, commands, lifecycle, repository = panel_services
    target = lifecycle.active.aggregate.ref
    panel.chat.messages = [{"role": "user", "content": "must survive failed delete"}]
    warnings = []

    def fail_delete(_ref):
        raise OSError("record locked")

    monkeypatch.setattr(repository, "delete", fail_delete)
    monkeypatch.setattr(
        module.QMessageBox, "warning", lambda _parent, title, message: warnings.append((title, message))
    )

    panel._session_list.delete_session.emit(target.identity.value)

    assert lifecycle.active.aggregate.ref == target
    assert commands.list_sessions()[0]["session_id"] == target.identity.value
    assert panel.chat.messages == [{"role": "user", "content": "must survive failed delete"}]
    assert warnings[0][0] == "删除会话失败"
    assert "SESSION_DELETE_FAILED" in warnings[0][1]
