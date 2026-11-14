from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import QMessageBox
import pytest

from tests.ui.responsive_layout_support import settle_layout, themed_layout_app as themed_layout_app
from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult
from transbridge.application.tasks import JobState
from transbridge.ui.operations.plan_view import (
    EditableControl,
    EditableFieldState,
    OperationKind,
    OperationPlanViewState,
)
from transbridge.ui.operations.preflight_view import (
    OperationPreflightResult,
    PreflightCheckState,
    PreflightCheckStatus,
)
from transbridge.ui.operations.write_destinations import DIRECTORY, ORIGINAL
from transbridge.ui.operations.write_dialog import WritePlanDialog
from transbridge.ui.operations.write_schemes import PREFIX

_LONG_PATH = "D:/翻译项目/" + "ExamplePlugin_" * 80 + ".esp"


def _plan():
    return OperationPlanViewState(
        "session",
        1,
        OperationKind.WRITE,
        "导出文件",
        "请选择输出位置",
        "当前文件",
        "导出当前文件的译文",
        "目标已存在时停止",
        "覆盖现有文件前先创建备份",
        (),
        editable_fields=(
            EditableFieldState(
                "output_format",
                "输出格式",
                "plugin.sse",
                control=EditableControl.CHOICE,
                options=(("plugin.sse", "原插件格式"), ("sst.ssu9", "xTranslator SST SSU9（实验性）")),
            ),
            EditableFieldState("plugin_path", "套用到插件", ""),
            EditableFieldState(
                "destination_mode",
                "保存方式",
                DIRECTORY,
                control=EditableControl.CHOICE,
                options=((DIRECTORY, "另存到目录"), (ORIGINAL, "保存回原文件")),
            ),
            EditableFieldState("output_directory", "输出目录", ""),
            EditableFieldState("target_path", "输出路径", ""),
            EditableFieldState("pack_bsa", "资源输出", "false", control=EditableControl.BOOLEAN),
            EditableFieldState("overwrite_confirmed", "覆盖确认", "false", control=EditableControl.BOOLEAN),
        ),
        request_digest="d" * 64,
    )


def _window_geometry(dialog):
    return (
        dialog.geometry(),
        dialog.minimumSize(),
        dialog._options_scroll.geometry(),
        dialog._feedback.geometry(),
        dialog._progress.geometry(),
        QRect(
            dialog._confirm_button.mapTo(dialog, dialog._confirm_button.rect().topLeft()), dialog._confirm_button.size()
        ),
        QRect(dialog._close_button.mapTo(dialog, dialog._close_button.rect().topLeft()), dialog._close_button.size()),
    )


def _check(dialog, *checks):
    return OperationPreflightResult(OperationKind.WRITE, dialog._plan.request_digest, "revision", checks, ())


@pytest.fixture
def dialog(layout_app):
    dialog = WritePlanDialog(_plan())
    dialog.show()
    settle_layout(layout_app)
    yield dialog
    dialog.set_execution_running(False)
    dialog.close()
    settle_layout(layout_app)


@pytest.mark.parametrize("failed", [False, True], ids=["success", "failure"])
def test_write_geometry_stays_stable_through_overwrite_and_execution(dialog, layout_app, monkeypatch, failed):
    geometry = _window_geometry(dialog)

    def assert_stable():
        settle_layout(layout_app)
        assert _window_geometry(dialog) == geometry

    revisions = []

    def start_check(_session, fields):
        revisions.append(dict(fields))
        plan = replace(dialog._plan, revision=dialog._plan.revision + 1)
        dialog.render_plan(plan)
        dialog.set_preflight_running(True)

    dialog.preflight_requested.connect(start_check)
    dialog._confirm_button.click()
    assert_stable()
    assert dialog._progress.isVisible()
    assert dialog._checks.property("tbStatusId") == "checking"

    overwrite = PreflightCheckState("OVERWRITE", "覆盖策略", PreflightCheckStatus.BLOCKED, "目标已存在，必须确认覆盖")
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes)
    dialog.render_preflight(_check(dialog, overwrite))
    assert_stable()
    dialog.set_preflight_running(False)
    assert_stable()
    assert tuple(fields["overwrite_confirmed"] for fields in revisions) == ("false", "true")
    assert dialog._progress.isVisible()

    dialog.render_preflight(_check(dialog))
    dialog.set_preflight_running(False)
    dialog.set_execution_running(True)
    assert_stable()
    assert dialog._progress.isVisible()
    assert dialog._checks.property("tbStatusId") == "running"

    status, result = (
        (JobState.FAILED, OperationResult.failed(DomainError(ErrorCategory.EXTERNAL, "FAILED", _LONG_PATH * 20)))
        if failed
        else (JobState.COMPLETED, OperationResult.completed(artifact_refs=(_LONG_PATH,) * 20))
    )
    dialog.render_execution(SimpleNamespace(is_terminal=True, state=status, run_id="run"), result)
    assert_stable()
    assert dialog._checks.isVisible()
    assert dialog._checks_scroll.verticalScrollBar().maximum() > 0
    assert dialog._progress.isVisible() is not failed
    dialog._details_button.click()
    assert_stable()
    assert dialog._details.isVisible()
    if failed:
        assert dialog._details.verticalScrollBar().maximum() > 0
    dialog._details_button.click()
    assert_stable()
    assert dialog._checks.isVisible()
    dialog._confirm_button.click()
    assert_stable()
    assert dialog._progress.isVisible()


def test_write_options_scroll_at_minimum_width_across_format_and_scheme_changes(dialog, layout_app):
    dialog.resize(dialog.minimumWidth(), 310)
    settle_layout(layout_app)
    geometry = _window_geometry(dialog)
    scheme = EditableFieldState(PREFIX + "a", "术语方案 A", "false", control=EditableControl.BOOLEAN)
    fields = (
        *dialog._plan.editable_fields,
        scheme,
        EditableFieldState("scheme_directory", "多方案输出目录", _LONG_PATH),
    )
    plan = replace(dialog._plan, revision=2, editable_fields=fields)
    dialog.render_plan(plan)
    settle_layout(layout_app)
    assert _window_geometry(dialog) == geometry
    assert dialog._options_scroll.verticalScrollBar().maximum() > 0
    assert not dialog._field_edits["scheme_directory"].isVisible()
    dialog._field_edits[scheme.field_id].setChecked(True)
    settle_layout(layout_app)
    assert _window_geometry(dialog) == geometry
    assert dialog._field_edits["scheme_directory"].isVisible()
    assert not dialog._field_edits["output_directory"].isVisible()
    scroll = dialog._options_scroll
    editor = dialog._field_edits["scheme_directory"]
    scroll.verticalScrollBar().setValue(editor.mapTo(scroll.widget(), editor.rect().topLeft()).y())
    settle_layout(layout_app)
    viewport = scroll.viewport()
    assert viewport.rect().contains(QRect(editor.mapTo(viewport, editor.rect().topLeft()), editor.size()))
    dialog._field_edits[scheme.field_id].setChecked(False)
    dialog._field_edits["output_format"].setCurrentIndex(1)
    settle_layout(layout_app)
    assert _window_geometry(dialog) == geometry
    assert dialog._field_edits["target_path"].isVisible()
    assert not dialog._field_edits["destination_mode"].isVisible()
    assert dialog.rect().contains(geometry[-2])
    assert dialog.rect().contains(geometry[-1])


def test_write_preflight_error_scrolls_and_keeps_the_footer_in_place(dialog, layout_app):
    geometry = _window_geometry(dialog)
    dialog.set_preflight_running(True)
    dialog.render_preflight_error("\n".join([_LONG_PATH] * 20))
    dialog.set_preflight_running(False)
    settle_layout(layout_app)
    assert _window_geometry(dialog) == geometry
    assert dialog._checks.isVisible()
    assert dialog._checks_scroll.verticalScrollBar().maximum() > 0
    assert dialog._checks_scroll.horizontalScrollBar().maximum() > 0
