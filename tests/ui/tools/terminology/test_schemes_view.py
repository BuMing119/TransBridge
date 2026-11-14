from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox
import pytest

from transbridge.application.terminology_profiles import TerminologyProfileImportPolicy
from transbridge.ui.tools.terminology.schemes_controller import TerminologySchemesController
from transbridge.ui.tools.terminology.schemes_view import TerminologySchemesView
from transbridge.ui.workbench.terminology_profile_bar import (
    TerminologyProfileBarState,
    TerminologyProfileChoice,
)

_APP = QApplication.instance() or QApplication([])


class _Profiles(QObject):
    state_changed = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__()
        self.identity = ("project", "variant")
        self.state = TerminologyProfileBarState(
            choices=(TerminologyProfileChoice("classic", "经典译名"),),
            selected_profile_id="classic",
            enabled=True,
            can_manage=True,
            detail="正在使用“经典译名”。",
        )
        self.selected = []
        self.manager_opened = 0
        self.manager_intents = []

    def select(self, profile_id) -> None:
        self.selected.append(profile_id)

    def open_manager(self, **intent) -> None:
        self.manager_opened += 1
        self.manager_intents.append(intent)


def test_scheme_switcher_keeps_selection_and_management_visible() -> None:
    profiles = _Profiles()
    view = TerminologySchemesView()
    controller = TerminologySchemesController(view, SimpleNamespace(), profiles, view)

    assert controller.parent() is view
    assert view.scheme_combo.currentData() == "classic"
    assert view.scheme_combo.currentText() == "经典译名"
    assert view.scheme_combo.accessibleName() == "当前译名方案"
    assert view.actions_button.text() == "更多…"
    assert view.import_button.text() == "导入术语…"
    assert view.import_button.property("tbTerminologyPrimary")
    assert controller._imports._select_after_create
    assert view.actions_button.isEnabled()
    assert view.manage_button.text() == "管理方案…"
    assert not view.manage_button.isHidden()
    assert view.status_label.isHidden()
    assert view.scheme_combo.accessibleDescription() == "经典译名"

    view.scheme_combo.setCurrentIndex(0)
    view.manage_button.click()

    assert profiles.selected == [None]
    assert profiles.manager_opened == 1
    view.close()


def test_direct_scheme_actions_distinguish_default_from_named_profile():
    profiles = _Profiles()
    view = TerminologySchemesView()
    controller = TerminologySchemesController(view, SimpleNamespace(), profiles, view)
    view.rename_action.trigger()
    view.new_action.trigger()
    edits = []
    view.edit_requested.connect(lambda: edits.append(True))
    view.edit_button.click()
    assert profiles.manager_intents == [{"rename": True}, {"create_from_base": True}]
    assert edits == [True]
    view.render(replace(profiles.state, selected_profile_id=None))
    assert not view.rename_action.isEnabled()
    assert view.new_action.isEnabled()
    assert "不是独立方案" in view.rename_action.toolTip()
    assert view.edit_button.text() == "编辑当前术语…"
    assert controller.parent() is view
    view.close()


def test_scheme_switcher_displays_none_when_no_library_is_selected() -> None:
    view = TerminologySchemesView()
    selections = []
    view.selection_requested.connect(selections.append)
    state = TerminologyProfileBarState(enabled=True, can_manage=True)
    view.render(state)

    assert view.scheme_combo.currentText() == "使用项目默认译名"
    assert view.scheme_combo.currentData() is None
    assert view.scheme_combo.accessibleDescription() == "使用项目默认译名"
    assert view.metadata.isHidden()
    assert view.metadata.text() == ""

    selected = _Profiles().state
    view.render(selected)
    assert view.scheme_combo.currentText() == "经典译名"
    view.render(replace(selected, selected_profile_id=None))
    assert view.scheme_combo.currentText() == "使用项目默认译名"
    assert view.scheme_combo.count() == 2
    assert selections == []
    view.close()


def test_import_button_selects_a_file_before_policy_and_background_read(monkeypatch) -> None:
    profiles = _Profiles()
    view = TerminologySchemesView()
    controller = TerminologySchemesController(view, SimpleNamespace(), profiles, view)
    chosen = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: ("D:/chosen/terms.csv", ""))

    class _Policy:
        def __init__(self, selection, _parent) -> None:
            chosen.append(selection)
            self.policy = TerminologyProfileImportPolicy.USE_IMPORTED

        @staticmethod
        def exec():
            return QDialog.DialogCode.Accepted

    captured = []
    monkeypatch.setattr(
        "transbridge.ui.tools.terminology.schemes_controller.TerminologyImportPolicyDialog",
        _Policy,
    )
    controller._imports.start_with_reader = lambda request, **kwargs: captured.append((request, kwargs))

    view.import_button.click()

    assert captured == [
        (
            chosen[0].request,
            {
                "default_name": "terms译名方案",
                "reader_factory": None,
                "policy": TerminologyProfileImportPolicy.USE_IMPORTED,
            },
        )
    ]
    assert Path(chosen[0].request.file_path) == Path("D:/chosen/terms.csv")
    view.close()


@pytest.mark.parametrize("cancel_stage", ["file", "policy"])
def test_cancelled_import_does_not_read_or_create(monkeypatch, cancel_stage):
    view = TerminologySchemesView()
    controller = TerminologySchemesController(view, SimpleNamespace(), _Profiles(), view)
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *_args: ("", "") if cancel_stage == "file" else ("D:/chosen/terms.json", ""),
    )

    class Policy:
        def __init__(self, *_args):
            assert cancel_stage == "policy"

        def exec(self):
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr("transbridge.ui.tools.terminology.schemes_controller.TerminologyImportPolicyDialog", Policy)
    controller._imports.start_with_reader = lambda *_args, **_kwargs: pytest.fail("cancel must not read or create")
    view.import_button.click()
    view.close()


def test_paratranz_entry_distinguishes_unbound_unavailable_and_available_targets():
    context = SimpleNamespace(
        config=SimpleNamespace(base_url="https://paratranz.cn/api", user_id=7),
        paratranz_binding=None,
    )
    view = TerminologySchemesView()
    controller = TerminologySchemesController(view, context, _Profiles(), view)
    assert view.paratranz_button.text() == "ParaTranz 未绑定"
    assert not view.paratranz_button.isEnabled()
    assert "请先绑定" in view.paratranz_button.toolTip()
    context.paratranz_binding = {
        "project_id": 42,
        "project_name": "当前绑定",
        "endpoint": "https://elsewhere.example/api",
    }
    controller._refresh_paratranz()
    assert view.paratranz_button.text() == "ParaTranz 不可用"
    assert "地址" in view.paratranz_button.toolTip()
    context.paratranz_binding["endpoint"] = "https://paratranz.cn/api"
    controller._refresh_paratranz()
    assert view.paratranz_button.text() == "ParaTranz 导入…"
    assert view.paratranz_button.isEnabled()
    assert "当前绑定" in view.paratranz_button.toolTip()
    view.set_import_busy(True)
    assert not view.import_button.isEnabled() and not view.paratranz_button.isEnabled()
    view.render(controller._profiles.state)
    assert not view.import_button.isEnabled() and not view.paratranz_button.isEnabled()
    view.set_import_busy(False)
    assert view.import_button.isEnabled() and view.paratranz_button.isEnabled()
    view.close()


@pytest.mark.parametrize("policy", tuple(TerminologyProfileImportPolicy))
@pytest.mark.parametrize("accepted", [False, True])
def test_paratranz_has_its_own_bound_project_route_without_opening_a_file(monkeypatch, policy, accepted):
    context = SimpleNamespace(
        config=SimpleNamespace(token="token", user_id=7, base_url="https://paratranz.cn/api", timeout=15, headers={}),
        paratranz_binding={"project_id": 42, "project_name": "当前绑定", "endpoint": "https://paratranz.cn/api"},
    )
    view = TerminologySchemesView()
    controller = TerminologySchemesController(view, context, _Profiles(), view)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: pytest.fail("ParaTranz must not select a file"))
    choices = []

    class Policy:
        def __init__(self, selection, _parent):
            choices.append(selection)
            self.policy = policy

        def exec(self):
            return QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected

    monkeypatch.setattr("transbridge.ui.tools.terminology.schemes_controller.TerminologyImportPolicyDialog", Policy)
    calls = []
    controller._imports.start_with_reader = lambda request, **kwargs: calls.append((request, kwargs))
    view.paratranz_button.click()
    assert len(choices) == 1
    assert len(calls) == int(accepted)
    if accepted:
        assert calls[0][0].source_id == "paratranz" and calls[0][0].paratranz_project_id == 42
        assert calls[0][1]["policy"] is policy
        assert calls[0][1]["reader_factory"] is choices[0].reader_factory
    view.close()


def test_busy_import_does_not_open_another_file_or_policy_dialog(monkeypatch):
    view = TerminologySchemesView()
    controller = TerminologySchemesController(view, SimpleNamespace(), _Profiles(), view)
    controller._imports._active_task = object()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: pytest.fail("busy import must remain exclusive"))
    controller.create_from_source()
    controller.create_from_paratranz()
    view.close()


@pytest.mark.parametrize("signal_name", ["config_changed", "user_changed", "paratranz_binding_changed"])
def test_paratranz_availability_refreshes_when_binding_endpoint_or_account_changes(signal_name):
    class Context(QObject):
        config_changed = pyqtSignal()
        user_changed = pyqtSignal()
        paratranz_binding_changed = pyqtSignal(object)

        def __init__(self):
            super().__init__()
            self.config = SimpleNamespace(base_url="https://wrong.example/api", user_id=8)
            self.paratranz_binding = {
                "project_id": 42,
                "project_name": "当前绑定",
                "endpoint": "https://paratranz.cn/api",
                "account_user_id": 7,
            }

    context, view = Context(), TerminologySchemesView()
    controller = TerminologySchemesController(view, context, _Profiles(), view)
    assert not view.paratranz_button.isEnabled()
    assert view.paratranz_button.text() == "ParaTranz 不可用"
    context.config.base_url = "https://paratranz.cn/api"
    context.config.user_id = 7
    signal = getattr(context, signal_name)
    signal.emit(None) if signal_name == "paratranz_binding_changed" else signal.emit()
    assert view.paratranz_button.isEnabled()
    assert view.paratranz_button.text() == "ParaTranz 导入…"
    controller.close()
    view.close()


def test_invalid_file_extension_is_explained_without_reading(monkeypatch):
    view = TerminologySchemesView()
    controller = TerminologySchemesController(view, SimpleNamespace(), _Profiles(), view)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: ("D:/plugin.esp", ""))
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: errors.append(_args[2]))
    controller._imports.start_with_reader = lambda *_args, **_kwargs: pytest.fail("invalid file must not read")
    view.import_button.click()
    assert len(errors) == 1 and "JSON、CSV、XLS 或 XLSX" in errors[0]
    view.close()


@pytest.mark.parametrize("source", ["json", "paratranz"])
def test_async_read_error_restores_both_import_entries(monkeypatch, source):
    view = TerminologySchemesView()
    controller = TerminologySchemesController(view, SimpleNamespace(), _Profiles(), view)
    view.set_paratranz_target(True, bound=True)
    errors, busy = [], []
    controller._imports.busy_changed.connect(busy.append)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: errors.append(_args[2]))
    monkeypatch.setattr(
        "transbridge.ui.tools.terminology_profiles.source_import_controller.QThreadPool.globalInstance",
        lambda: SimpleNamespace(start=lambda task: task.run()),
    )

    class FailedReader:
        def read(self, _request):
            assert not view.import_button.isEnabled() and not view.paratranz_button.isEnabled()
            raise OSError(f"{source}读取失败")

    controller._imports.start_with_reader(
        SimpleNamespace(source_id=source), default_name="失败来源", reader_factory=FailedReader
    )
    assert errors == [f"{source}读取失败"]
    assert busy == [True, False]
    assert view.import_button.isEnabled() and view.paratranz_button.isEnabled()
    view.close()


@pytest.mark.parametrize("new_binding", [None, {"project_id": 99, "project_name": "其他项目", "endpoint": "invalid"}])
def test_paratranz_binding_changed_during_policy_does_not_read_the_old_project(monkeypatch, new_binding):
    context = SimpleNamespace(
        config=SimpleNamespace(token="token", user_id=7, base_url="https://paratranz.cn/api", timeout=15, headers={}),
        paratranz_binding={"project_id": 42, "project_name": "当前绑定", "endpoint": "https://paratranz.cn/api"},
    )
    view = TerminologySchemesView()
    controller = TerminologySchemesController(view, context, _Profiles(), view)

    class Policy:
        def __init__(self, *_args):
            self.policy = TerminologyProfileImportPolicy.KEEP_EXISTING

        def exec(self):
            context.paratranz_binding = new_binding
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr("transbridge.ui.tools.terminology.schemes_controller.TerminologyImportPolicyDialog", Policy)
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: messages.append(_args[2]))
    controller._imports.start_with_reader = lambda *_args, **_kwargs: pytest.fail("changed binding must not read")
    view.paratranz_button.click()
    assert len(messages) == 1
    assert view.import_button.isEnabled()
    view.close()
