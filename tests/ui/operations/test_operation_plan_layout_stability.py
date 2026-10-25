from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
import pytest

from tests.ui.responsive_layout_support import (
    apply_layout_font,
    settle_layout,
    themed_layout_app as themed_layout_app,
)
from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode
from transbridge.ui.foundation.builtins import create_builtin_registry
from transbridge.ui.foundation.theme_service import ThemePreference, ThemeService
from transbridge.ui.operations.plan_dialog import OperationPlanDialog
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
from transbridge.ui.operations.write_dialog import WritePlanDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_long_preflight_keeps_plan_footer_geometry_stable(qapp) -> None:
    digest = "d" * 64
    plan = OperationPlanViewState(
        "session",
        1,
        OperationKind.UPLOAD,
        "上传计划",
        "project",
        "当前文件",
        "覆盖远端",
        "冲突时停止",
        "远端操作",
        (),
        request_digest=digest,
    )
    dialog = OperationPlanDialog(plan)
    dialog.show()
    qapp.processEvents()
    before = (
        dialog.minimumSizeHint().height(),
        dialog._preflight_button.y(),
        dialog._confirm_button.y(),
        dialog._checks_scroll.height(),
    )
    checks = tuple(
        PreflightCheckState(
            f"check-{index}",
            f"检查 {index}",
            PreflightCheckStatus.WARNING,
            "很长的预检失败原因" * 100,
        )
        for index in range(12)
    )
    dialog.render_preflight(OperationPreflightResult(OperationKind.UPLOAD, digest, "revision", checks, ()))
    qapp.processEvents()

    assert (
        dialog.minimumSizeHint().height(),
        dialog._preflight_button.y(),
        dialog._confirm_button.y(),
        dialog._checks_scroll.height(),
    ) == before
    assert dialog._checks_scroll.verticalScrollBar().maximum() > 0
    dialog.close()


@pytest.mark.parametrize("count", [2, 80, 300])
@pytest.mark.parametrize("point_size", [9, 12])
def test_batch_write_paths_scroll_without_pushing_footer_off_screen(layout_app, count, point_size):
    paths = tuple(
        rf"D:\MyProgram\SteamLibrary\steamapps\common\Skyrim Special Edition\Data\plugin_{index}_translated.esm"
        for index in range(count)
    )
    plan = OperationPlanViewState(
        "session",
        1,
        OperationKind.WRITE,
        "批量写回插件",
        "；".join(paths),
        f"{count} 个来源文件，96306 个条目",
        "分别验证并写出各来源文件",
        "保留已有输出，继续写出其他文件",
        "覆盖输出文件前创建备份",
        (),
        editable_fields=(
            EditableFieldState("overwrite_confirmed", "覆盖确认", "false", control=EditableControl.BOOLEAN),
        ),
        is_batch=True,
    )
    dialog = WritePlanDialog(plan)
    preferences = SimpleNamespace(
        load=lambda: SimpleNamespace(theme_mode=ThemeMode.LIGHT, theme_id=DEFAULT_THEME_ID, diagnostics=())
    )
    theme = ThemeService(layout_app, create_builtin_registry(), preferences)
    apply_layout_font(dialog, theme.preview(ThemePreference()), point_size)
    dialog.resize(760, 520)
    dialog.show()
    settle_layout(layout_app)
    try:
        assert dialog.height() <= 520
        for button in (dialog._preflight_button, dialog._edit_button, dialog._confirm_button, dialog._close_button):
            assert dialog.rect().contains(button.geometry())
        assert all(path in dialog._summary.text() for path in paths)
        if count > 2:
            scroll = dialog._summary_scroll
            assert scroll.verticalScrollBar().maximum() > 0
            scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
            settle_layout(layout_app)
            bottom_left = dialog._summary.mapTo(scroll.viewport(), dialog._summary.rect().bottomLeft())
            assert scroll.viewport().rect().contains(bottom_left)
            size_before = dialog.size()
            dialog.render_plan(plan)
            settle_layout(layout_app)
            assert scroll.verticalScrollBar().value() == 0
            assert dialog.size() == size_before
        assert dialog._summary.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
    finally:
        dialog.close()
        theme.close()
        settle_layout(layout_app)
