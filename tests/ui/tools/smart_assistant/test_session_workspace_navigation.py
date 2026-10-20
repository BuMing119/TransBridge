"""Real session persistence with a detached chat view exercises workspace navigation."""

from __future__ import annotations

from pathlib import Path
from threading import Event
from time import monotonic
from types import SimpleNamespace

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox, QPlainTextEdit, QWidget
import pytest

from tests.application.sessions.test_gui_session_management import build_session_services
from transbridge.application.contracts import RequestContext
from transbridge.application.sessions import ControllerSnapshot
from transbridge.smart_assistant.service_readiness import ServiceAvailability
from transbridge.ui.tools.smart_assistant import panel as module

_APP = QApplication.instance() or QApplication([])
pytest_plugins = ["tests.ui.tools.smart_assistant.test_request_lifecycle_panel"]


def _until(predicate):
    deadline = monotonic() + 5
    while not predicate() and monotonic() < deadline:
        _APP.processEvents()
        QTest.qWait(5)
    assert predicate()


class _Chat(QWidget):
    def __init__(self, context, *, theme=None, navigation=None):
        super().__init__()
        self.context = context
        self._input = QPlainTextEdit(self)
        self.values = {}
        self.loads = []
        self.notices = []

    def configure_session_port(self, **_kwargs):
        pass

    def set_task_monitor(self, _monitor):
        pass

    def recovery_snapshot(self):
        return list(self.values.get("backend_history", [])), ControllerSnapshot()

    def lifecycle_jobs(self):
        return ()

    def load_session(self, values):
        self.values = dict(values)
        self.loads.append(dict(values))

    def set_input(self, text):
        self._input.setPlainText(text)

    def add_system_message(self, text):
        self.notices.append(text)

    def shutdown(self, **_kwargs):
        pass


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    commands, lifecycle, repository, projection = build_session_services(tmp_path / "sessions")
    refs = {}
    contexts = {}
    for name, project, variant in (
        ("B", "project-b", "variant-b"),
        ("A-alt", "project-a", "variant-a-alt"),
        ("Legacy", None, None),
        ("A", "project-a", "variant-a"),
    ):
        context = RequestContext("owner", project_id=project, variant_id=variant)
        assert commands.create_and_activate(name, context).is_success
        ref = lifecycle.active.aggregate.ref
        messages = [{"role": "user", "content": name + " history"}]
        assert commands.save_conversation(ref, messages, messages, context).is_success
        refs[name], contexts[name] = ref, context
    monkeypatch.setattr(module, "ChatWidget", _Chat)
    monkeypatch.setattr(module.SmartAssistantPanel, "_init_skills", lambda _self: None)
    monkeypatch.setattr(
        module.SmartAssistantPanel,
        "_configured_service_availability",
        staticmethod(lambda: ServiceAvailability("test-model", True)),
    )
    monkeypatch.setattr(module, "set_window_app_user_model_id", lambda *_args: False)
    context = SimpleNamespace(active_version_identity=("project-a", "variant-a"))
    switches, warnings = [], []
    panel = module.SmartAssistantPanel(
        context,
        session_commands=commands,
        session_projection=projection,
        runtime_context=RequestContext("owner"),
        navigation={"switch_project": lambda target, finished: switches.append((target, finished))},
    )
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, _title, text: warnings.append(text))
    _APP.processEvents()

    def preview(name):
        panel._switch_to(refs[name].identity.value)
        _until(lambda: not panel._sessions.loading and panel._current_session_id() == refs[name].identity.value)

    yield SimpleNamespace(
        panel=panel,
        context=context,
        commands=commands,
        lifecycle=lifecycle,
        repository=repository,
        refs=refs,
        contexts=contexts,
        switches=switches,
        warnings=warnings,
        preview=preview,
    )
    panel.dispose()
    panel.deleteLater()
    projection.close()
    _APP.processEvents()


def test_cross_project_preview_does_not_activate_or_write_target_and_restores_drafts(workspace):
    case = workspace
    case.panel.chat.set_input("A draft")
    path = Path(case.repository.path_for(case.refs["B"]))
    before = path.read_bytes()
    case.preview("B")
    assert case.panel._sessions.preview
    assert case.panel.chat.values["_preview"]
    assert case.panel.chat.values["messages"] == [{"role": "user", "content": "B history"}]
    assert case.lifecycle.active.aggregate.ref == case.refs["A"]
    assert case.context.active_version_identity == ("project-a", "variant-a")
    assert case.panel._persist_authoritative_chat()
    assert path.read_bytes() == before
    case.panel.chat.set_input("B draft")
    case.preview("A")
    assert case.panel.chat._input.toPlainText() == "A draft"
    case.preview("B")
    assert case.panel.chat._input.toPlainText() == "B draft"
    assert path.read_bytes() == before


@pytest.mark.parametrize("name", ["B", "A-alt"])
def test_project_and_variant_mismatch_both_require_confirmation_and_keep_cancelled_draft(workspace, monkeypatch, name):
    case = workspace
    case.preview(name)
    case.panel.chat.set_input("continue this later")
    prompts, resumed = [], []
    monkeypatch.setattr(
        case.panel._workspace, "confirm_switch", lambda action, labels: prompts.append((action, labels)) or False
    )
    assert not case.panel._workspace.ensure("发送", lambda: resumed.append(True))
    assert len(prompts) == 1 and not resumed and not case.switches
    assert case.panel.chat._input.toPlainText() == "continue this later"
    assert case.lifecycle.active.aggregate.ref == case.refs["A"]


def test_confirmed_switch_rechecks_installed_scope_before_activating_and_resuming(workspace, monkeypatch):
    case = workspace
    case.preview("B")
    case.panel.chat.set_input("B pending draft")
    monkeypatch.setattr(case.panel._workspace, "confirm_switch", lambda *_: True)
    resumed = []
    assert not case.panel._workspace.ensure("发送", lambda: resumed.append(case.panel._workspace.bound_context()))
    assert not resumed and case.lifecycle.active.aggregate.ref == case.refs["A"]
    target, finished = case.switches.pop()
    assert (target.project_id, target.variant_id) == ("project-b", "variant-b")
    case.context.active_version_identity = (target.project_id, target.variant_id)
    finished(True)
    assert len(resumed) == 1 and resumed[0] == target
    assert case.lifecycle.active.aggregate.ref == case.refs["B"]
    assert not case.panel._sessions.preview
    assert case.panel.chat._input.toPlainText() == "B pending draft"


@pytest.mark.parametrize("success", [False, True])
def test_failed_or_wrong_workspace_switch_cannot_activate_preview_or_resume(workspace, monkeypatch, success):
    case = workspace
    case.preview("B")
    case.panel.chat.set_input("keep B draft")
    monkeypatch.setattr(case.panel._workspace, "confirm_switch", lambda *_: True)
    resumed = []
    case.panel._workspace.ensure("重试", lambda: resumed.append(True))
    case.switches.pop()[1](success)
    assert not resumed
    assert case.lifecycle.active.aggregate.ref == case.refs["A"]
    assert case.panel._sessions.preview
    assert case.panel.chat._input.toPlainText() == "keep B draft"


def test_changing_view_during_project_switch_invalidates_pending_execution(workspace, monkeypatch):
    case = workspace
    case.preview("B")
    monkeypatch.setattr(case.panel._workspace, "confirm_switch", lambda *_: True)
    resumed = []
    case.panel._workspace.ensure("继续任务", lambda: resumed.append(True))
    target, finished = case.switches.pop()
    case.preview("A")
    case.context.active_version_identity = (target.project_id, target.variant_id)
    finished(True)
    assert not resumed
    assert case.panel._current_session_id() == case.refs["A"].identity.value
    assert case.lifecycle.active.aggregate.ref == case.refs["A"]
    assert not case.panel._workspace.ready()


def test_creating_from_foreign_preview_binds_to_actual_workbench(workspace):
    case = workspace
    case.preview("B")
    case.panel._on_create_session("New actual project chat")
    active = case.lifecycle.active.aggregate.snapshot()
    assert active.project_id.value == "project-a"
    assert active.variant_id.value == "variant-a"
    assert active.ref not in case.refs.values()
    assert case.panel._workspace.ready()


def test_matching_preview_deferred_intent_is_fenced_by_navigation(workspace, monkeypatch):
    case = workspace
    case.preview("B")
    case.context.active_version_identity = ("project-b", "variant-b")
    monkeypatch.setattr(case.panel._workspace, "confirm_switch", lambda *_: pytest.fail("Already matching"))
    resumed = []
    assert not case.panel._workspace.ensure("发送", lambda: resumed.append(True))
    case.preview("A")
    _APP.processEvents()
    assert not resumed
    assert case.panel._current_session_id() == case.refs["A"].identity.value


def test_unbound_history_cannot_execute_or_inherit_current_project(workspace, monkeypatch):
    case = workspace
    case.preview("Legacy")
    path = Path(case.repository.path_for(case.refs["Legacy"]))
    before = path.read_bytes()
    monkeypatch.setattr(case.panel._workspace, "confirm_switch", lambda *_: pytest.fail("No binding to switch"))
    resumed = []
    assert not case.panel._workspace.ensure("发送", lambda: resumed.append(True))
    assert case.warnings and not resumed and not case.switches
    assert case.lifecycle.active.aggregate.ref == case.refs["A"]
    assert path.read_bytes() == before


def test_draft_changes_during_background_history_read_survive_return(workspace, monkeypatch):
    case = workspace
    entered, release = Event(), Event()
    read = case.commands.read_conversation

    def blocking_read(*args):
        entered.set()
        assert release.wait(5)
        return read(*args)

    monkeypatch.setattr(case.commands, "read_conversation", blocking_read)
    case.panel.chat.set_input("before loading")
    try:
        case.panel._switch_to(case.refs["B"].identity.value)
        assert entered.wait(2)
        case.panel.chat.set_input("typed while history was loading")
    finally:
        release.set()
    _until(lambda: not case.panel._sessions.loading)
    case.preview("A")
    assert case.panel.chat._input.toPlainText() == "typed while history was loading"


def _real_request_preview(environment, monkeypatch):
    panel, binding = environment.panel, environment.binding
    monkeypatch.setattr(binding, "wake", lambda *_: None)
    context = panel.chat.context
    context.active_version_identity = ("project-b", "variant-b")
    panel._on_create_session("B requests")
    target_session_id = panel._current_session_id()
    _until(lambda: binding._accepting == 0)
    context.active_version_identity = ("project-a", "variant-a")
    context.selected_ids = ["A-selected-entry"]
    panel._on_create_session("A requests")
    source_session_id = panel._current_session_id()
    _until(lambda: binding._accepting == 0)
    panel._switch_to(target_session_id)
    _until(
        lambda: (
            not panel._sessions.loading
            and panel._current_session_id() == target_session_id
            and binding.session_loading.preview
            and binding._accepting == 0
        )
    )
    return context, source_session_id, target_session_id


def test_real_send_keeps_cancelled_draft_and_captures_selection_only_after_target_install(environment, monkeypatch):
    panel, binding = environment.panel, environment.binding
    context, source_id, target_id = _real_request_preview(environment, monkeypatch)
    switches, accepted = [], []
    panel._navigation["switch_project"] = lambda target, finished: switches.append((target, finished))
    monkeypatch.setattr(panel._workspace, "confirm_switch", lambda *_: False)
    original_accept = environment.service.accept_input

    def capture_accept(request_context, text, **kwargs):
        accepted.append((request_context, text, kwargs["selection"]))
        return original_accept(request_context, text, **kwargs)

    monkeypatch.setattr(environment.service, "accept_input", capture_accept)
    panel.chat.set_input("Continue B")
    panel.chat._on_send()
    assert panel.chat._input.toPlainText() == "Continue B"
    assert not accepted and not switches
    assert environment.services.session_lifecycle.active.aggregate.ref.identity.value == source_id

    monkeypatch.setattr(panel._workspace, "confirm_switch", lambda *_: True)
    panel.chat._on_send()
    assert panel.chat._input.toPlainText() == "Continue B" and not accepted
    target, finished = switches.pop()
    context.active_version_identity = (target.project_id, target.variant_id)
    context.selected_ids = ["B-selected-entry"]
    finished(True)
    _until(lambda: bool(accepted) and binding._accepting == 0)
    assert len(accepted) == 1
    captured, text, selection = accepted[0]
    assert captured.session_id == target_id
    assert (captured.project_id, captured.variant_id) == ("project-b", "variant-b")
    assert text == "Continue B"
    assert selection["active_version_identity"] == ["project-b", "variant-b"]
    assert selection["selected_entry_ids"] == ["B-selected-entry"]
    assert panel.chat._input.toPlainText() == ""


def test_real_send_switch_failure_leaves_preview_and_unsent_text(environment, monkeypatch):
    panel = environment.panel
    _context, source_id, _target_id = _real_request_preview(environment, monkeypatch)
    switches = []
    panel._navigation["switch_project"] = lambda target, finished: switches.append(finished)
    monkeypatch.setattr(panel._workspace, "confirm_switch", lambda *_: True)
    monkeypatch.setattr(environment.service, "accept_input", lambda *_args, **_kwargs: pytest.fail("Input admitted"))
    panel.chat.set_input("Do not lose this")
    panel.chat._on_send()
    switches.pop()(False)
    assert panel.chat._input.toPlainText() == "Do not lose this"
    assert panel._sessions.preview and environment.binding.session_loading.preview
    assert environment.services.session_lifecycle.active.aggregate.ref.identity.value == source_id
