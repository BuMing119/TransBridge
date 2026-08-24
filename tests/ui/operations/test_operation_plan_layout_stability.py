from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.ui.operations.plan_dialog import OperationPlanDialog
from transbridge.ui.operations.plan_view import OperationKind, OperationPlanViewState
from transbridge.ui.operations.preflight_view import (
    OperationPreflightResult,
    PreflightCheckState,
    PreflightCheckStatus,
)


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
