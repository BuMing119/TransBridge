"""Selecting a skill is a preview, and launching submits the edited request once."""

from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication, QWidget
import pytest

from tests.ui.tools.smart_assistant.test_request_lifecycle_panel import _requests, _until
from transbridge.smart_assistant.skills import SkillRegistry
from transbridge.smart_assistant.skills.skill_loader import SkillSpec
from transbridge.ui.tools.smart_assistant.input_view import ChatInputActions
from transbridge.ui.tools.smart_assistant.quick_actions import QuickActionsChips

pytest_plugins = ["tests.ui.tools.smart_assistant.test_request_lifecycle_panel"]


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


class _Chat(QWidget):
    def __init__(self):
        super().__init__()
        self.context = SimpleNamespace(project_name="演示项目", selected_ids=["a", "b"])
        self.prompts, self.messages, self.notices = [], [], []
        self.skills = []
        self.accept = True

    def add_system_prompt(self, text):
        self.prompts.append(text)

    def add_system_message(self, text):
        self.notices.append(text)

    def send_user_message(self, text, *, skill_context=None):
        if self.accept:
            self.messages.append(text)
            self.skills.append(skill_context)
        return self.accept


def _actions(monkeypatch):
    spec = SkillSpec("review", "术语检查", description="检查术语。", prompt_template="Use the approved glossary.")
    monkeypatch.setattr(SkillRegistry, "_skills", {spec.name: spec})
    chat = _Chat()
    actions = ChatInputActions(
        chat_facade=chat,
        orchestrator=SimpleNamespace(),
        controller=SimpleNamespace(),
        auto_mode=False,
        notify=lambda _: None,
    )
    return chat, actions


def test_skill_selection_previews_and_explicit_launch_uses_edited_request(qapp, monkeypatch):
    chat, actions = _actions(monkeypatch)
    actions.select_skill("review")
    dialog = actions._skill_launcher.dialog
    try:
        assert dialog.isVisible()
        assert not chat.messages and not chat.prompts and not chat.notices
        assert "检查术语" in dialog.description.text()
        assert "演示项目" in dialog.scope_label.text() and "2 条" in dialog.scope_label.text()
        dialog.request.setPlainText("只检查城堡相关名称，先说明建议")
        dialog.run_button.click()
        assert chat.messages == ["[Skill: review] 只检查城堡相关名称，先说明建议"]
        assert not chat.prompts
        assert chat.skills == [{"name": "review", "display_name": "术语检查", "prompt": "Use the approved glossary."}]
        assert actions._skill_launcher.dialog is None
    finally:
        actions.close_skill_preview()
        chat.close()


def test_cancel_or_session_close_never_sends_and_rejected_launch_preserves_edit(qapp, monkeypatch):
    chat, actions = _actions(monkeypatch)
    try:
        actions.select_skill("review")
        actions._skill_launcher.dialog.cancel_button.click()
        assert not chat.messages and not chat.prompts
        actions.select_skill("review")
        dialog = actions._skill_launcher.dialog
        dialog.request.setPlainText("保留我编辑的要求")
        chat.accept = False
        dialog.run_button.click()
        assert dialog.isVisible() and dialog.request.toPlainText() == "保留我编辑的要求"
        assert dialog.run_button.isEnabled()
        assert not chat.messages and not chat.prompts and not chat.skills
        chat.accept = True
        dialog.run_button.click()
        assert chat.messages == ["[Skill: review] 保留我编辑的要求"]
        actions.select_skill("review")
        actions.close_skill_preview()
        assert actions._skill_launcher.dialog is None
        assert len(chat.messages) == 1
    finally:
        actions.close_skill_preview()
        chat.close()


def test_skill_entry_uses_chinese_and_explains_preview(qapp):
    quick = QuickActionsChips()
    assert quick._skill_btn.text() == "技能"
    assert "确认后启动" in quick._skill_btn.toolTip()
    quick.close()


def test_real_request_path_starts_only_after_explicit_skill_launch(environment, monkeypatch):
    spec = SkillSpec("review", "术语检查", description="检查术语。", prompt_template="Use the approved glossary.")
    monkeypatch.setattr(SkillRegistry, "_skills", {spec.name: spec})
    chat = environment.panel.chat
    chat._input_actions.select_skill("review")
    dialog = chat._input_actions._skill_launcher.dialog
    assert not environment.client.calls and not _requests(environment)
    dialog.request.setPlainText("Review the castle terminology and explain each suggestion")
    dialog.run_button.click()
    _until(lambda: bool(_requests(environment)) and all(request.terminal for request in _requests(environment)))
    assert len(_requests(environment)) == 1
    assert "castle terminology" in _requests(environment)[0].goal
    assert environment.client.calls
    assert chat._input_actions._skill_launcher.dialog is None


def test_real_session_switch_closes_unsent_skill_preview(environment, monkeypatch):
    spec = SkillSpec("review", "术语检查", description="检查术语。")
    monkeypatch.setattr(SkillRegistry, "_skills", {spec.name: spec})
    panel, chat = environment.panel, environment.panel.chat
    first_session = panel._current_session_id()
    chat._input_actions.select_skill("review")
    panel._on_create_session("另一个会话")
    _until(lambda: panel._current_session_id() != first_session)
    assert chat._input_actions._skill_launcher.dialog is None
    assert not environment.client.calls
