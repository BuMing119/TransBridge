from __future__ import annotations

from dataclasses import replace
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.application.projects import (
    ParaTranzTargetSource,
    ParaTranzTargetStatus,
    ResolvedParaTranzTarget,
)
from transbridge.ui.workbench.remote_target_view import RemoteTargetView

_APP = QApplication.instance() or QApplication([])


class _Signal:
    def connect(self, _callback) -> None:
        pass


class _Context:
    active_project_id = "local-project"
    paratranz_binding_changed = _Signal()
    config_changed = _Signal()
    user_changed = _Signal()
    project_changed = _Signal()

    def __init__(self) -> None:
        self.target = self._target("短项目")

    @staticmethod
    def _target(name: str) -> ResolvedParaTranzTarget:
        return ResolvedParaTranzTarget(
            42,
            name,
            "https://paratranz.cn",
            7,
            ParaTranzTargetSource.PROJECT_BINDING,
            ParaTranzTargetStatus.AVAILABLE,
            1,
            "目标可用",
        )

    def resolve_paratranz_target(self) -> ResolvedParaTranzTarget:
        return self.target

    def set_unbound(self) -> None:
        self.target = ResolvedParaTranzTarget(
            None,
            None,
            "https://paratranz.cn",
            7,
            ParaTranzTargetSource.UNBOUND,
            ParaTranzTargetStatus.UNBOUND,
            1,
            "尚未绑定",
        )


def test_project_name_length_does_not_change_remote_target_layout_width() -> None:
    context = _Context()
    view = RemoteTargetView(context)
    view.resize(420, 48)
    view.show()
    _APP.processEvents()
    short_minimum_width = view.minimumSizeHint().width()
    short_choose_x = view._choose.x()

    long_name = "Remer-Custom Voiced Dwemer Specialist and Companion Simplified Chinese translation"
    context.target = context._target(long_name)
    view.refresh()
    _APP.processEvents()

    assert view.minimumSizeHint().width() == short_minimum_width
    assert view._choose.x() == short_choose_x
    assert view._label.full_text == f"ParaTranz · {long_name}"
    assert view._label.text() != view._label.full_text
    assert "…" in view._label.text()
    assert long_name in view._label.toolTip()
    view.close()


def test_binding_state_keeps_remote_target_action_geometry() -> None:
    context = _Context()
    context.set_unbound()
    view = RemoteTargetView(context)
    view.resize(420, 48)
    view.show()
    _APP.processEvents()
    unbound_choose = view._choose.geometry()
    reserved_more_width = view._more.width()
    assert view._more.isHidden()
    assert view._choose.text() == "绑定项目…"

    context.target = context._target("远端项目")
    view.refresh()
    _APP.processEvents()

    assert view._choose.geometry() == unbound_choose
    assert view._more.width() == reserved_more_width
    assert not view._more.isHidden()
    assert view._choose.text() == "更换…"
    assert [action.text() for action in view._more.menu().actions()] == ["解除绑定"]
    assert not view._more.menu().isVisible()
    view.close()


@pytest.mark.parametrize("width", [280, 360, 420, 980])
@pytest.mark.parametrize(
    "status,visible_status",
    [
        (ParaTranzTargetStatus.AVAILABLE, ""),
        (ParaTranzTargetStatus.ACCOUNT_MISMATCH, "账号不一致"),
        (ParaTranzTargetStatus.TEMPORARILY_UNAVAILABLE, "暂时无法验证"),
    ],
)
def test_binding_status_and_actions_fit_narrow_row(width, status, visible_status) -> None:
    context = _Context()
    context.target = replace(context._target("很长的远端项目名称" * 20), status=status, reason="目标状态的完整说明")
    view = RemoteTargetView(context)
    view.resize(width, 32)
    view.show()
    _APP.processEvents()
    try:
        assert view.width() == width
        assert view.rect().contains(view._choose.geometry())
        assert view.rect().contains(view._more.geometry())
        assert visible_status in view._label.full_text
        if width >= 420:
            assert visible_status in view._label.text()
        assert "目标状态的完整说明" in view._label.toolTip()
        assert context.target.project_name in view._label.toolTip()
        assert not view._label.text().endswith(context.target.project_name)
    finally:
        view.close()
