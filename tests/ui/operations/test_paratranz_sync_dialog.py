from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLineEdit
import pytest

from transbridge.ui.operations.paratranz_dialog import ParaTranzSyncDialog
from transbridge.ui.operations.plan_view import (
    EditableControl,
    EditableFieldState,
    OperationKind,
    OperationPlanViewState,
)
from transbridge.ui.operations.preflight_view import OperationPreflightResult


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _plan() -> OperationPlanViewState:
    return OperationPlanViewState(
        "session",
        1,
        OperationKind.DOWNLOAD,
        "从 ParaTranz 更新本地翻译",
        "天际中文翻译 · 当前工程已绑定",
        "天际汉化 / 正式版 / 8,300 条翻译内容",
        "使用 ParaTranz 内容更新本地",
        "云端内容优先",
        "下载前自动创建历史还原点",
        (("local_entries", 8300),),
        editable_fields=(
            EditableFieldState(
                "paratranz_project_id",
                "云端项目",
                "18668",
                control=EditableControl.REMOTE_PROJECT,
                display_value="天际中文翻译",
            ),
            EditableFieldState("set_as_default", "以后默认使用这个云端项目", "false", enabled=False),
            EditableFieldState(
                "conflict_policy",
                "同步方式",
                "prefer_remote",
                control=EditableControl.CHOICE,
                options=(
                    ("prefer_remote", "使用 ParaTranz 内容更新本地（推荐）"),
                    ("prefer_local", "保留本地已有内容，只补充云端新增内容"),
                ),
            ),
            EditableFieldState(
                "apply_remote_deletions",
                "同步云端删除",
                "false",
                control=EditableControl.BOOLEAN,
            ),
        ),
        request_digest="d" * 64,
    )


def test_dialog_uses_project_name_and_automatically_requests_preflight(qapp) -> None:
    context = SimpleNamespace(config=SimpleNamespace(config_revision=0), active_project_id="local")
    dialog = ParaTranzSyncDialog(_plan(), context)
    requested = []
    dialog.preflight_requested.connect(lambda session_id, values: requested.append((session_id, dict(values))))
    dialog.show()
    qapp.processEvents()

    assert dialog._target_name.text() == "天际中文翻译"
    assert "18668" not in dialog._target_name.text()
    assert not dialog.findChildren(QLineEdit)
    assert dialog._confirm.text() == "下载并更新本地"
    assert requested and requested[-1][0] == "session"
    assert requested[-1][1] == {
        "paratranz_project_id": "18668",
        "paratranz_project_name": "天际中文翻译",
        "set_as_default": "false",
        "conflict_policy": "prefer_remote",
        "apply_remote_deletions": "false",
    }
    dialog.close()


def test_dialog_renders_plain_language_impact_and_enables_one_primary_action(qapp) -> None:
    dialog = ParaTranzSyncDialog(
        _plan(),
        SimpleNamespace(config=SimpleNamespace(config_revision=0), active_project_id="local"),
    )
    dialog.render_preflight(
        OperationPreflightResult(
            OperationKind.DOWNLOAD,
            "d" * 64,
            "remote:18668:7",
            (),
            ("在本地聚合事务中应用远端更新",),
            object(),
            (("update_local", 126), ("create_local", 23), ("skip", 8017), ("delete_local", 0)),
        )
    )

    assert dialog._impact.text() == "更新 126  ·  新增 23  ·  保留 8,017"
    assert dialog._confirm.isEnabled()
    assert dialog._status.text() == "检查完成，可以开始。"
    dialog.close()
