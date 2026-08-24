from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.application.security.hitl import ConfirmationToken
from transbridge.ui.foundation.builtins import DEFAULT_THEME_ID, create_builtin_registry
from transbridge.ui.foundation.model import ThemeScheme
from transbridge.ui.foundation.qt_palette import compile_palette
from transbridge.ui.operations.plan_dialog import OperationPlanDialog
from transbridge.ui.operations.plan_view import EditableFieldState, OperationKind, OperationPlanViewState
from transbridge.ui.operations.preflight_view import (
    OperationPreflightResult,
    PreflightCheckState,
    PreflightCheckStatus,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_palette_revision_preserves_plan_digest_token_preflight_and_emits_no_business_signal(qapp) -> None:
    original_palette = QPalette(qapp.palette())
    registry = create_builtin_registry()
    light = compile_palette(registry.resolve(DEFAULT_THEME_ID, ThemeScheme.LIGHT))
    dark = compile_palette(registry.resolve(DEFAULT_THEME_ID, ThemeScheme.DARK))
    qapp.setPalette(light)
    digest = "a" * 64
    plan = OperationPlanViewState(
        "session-one",
        3,
        OperationKind.WRITE,
        "写回计划",
        "output.esp",
        "3 个对象",
        "原子写回",
        "有冲突时停止",
        "覆盖前备份",
        (("objects", 3),),
        editable_fields=(EditableFieldState("target", "输出路径", "chosen.esp"),),
        request_digest=digest,
    )
    token = ConfirmationToken("token-one", "gui", digest, 999999.0, "signature")
    preflight = OperationPreflightResult(
        OperationKind.WRITE,
        digest,
        "target-r1",
        (PreflightCheckState("OUTPUT", "输出可写", PreflightCheckStatus.PASSED),),
        ("写入 output.esp",),
        token,
    )
    dialog = OperationPlanDialog(plan)
    dialog.render_preflight(preflight)
    calls = []
    dialog.preflight_requested.connect(lambda *args: calls.append(("preflight", args)))
    dialog.return_to_edit_requested.connect(lambda *args: calls.append(("edit", args)))
    dialog.confirm_requested.connect(lambda *args: calls.append(("confirm", args)))
    field = dialog._field_edits["target"]
    before_window = dialog.palette().color(QPalette.ColorRole.Window)

    qapp.setPalette(dark)
    qapp.processEvents()

    try:
        assert dialog.palette().color(QPalette.ColorRole.Window) != before_window
        assert dialog._plan is plan
        assert dialog._plan.request_digest == digest
        assert dialog._preflight is preflight
        assert dialog._preflight.confirmation_token is token
        assert field.text() == "chosen.esp"
        assert dialog._checks.property("tbStatusId") == "passed"
        assert "✓" in dialog._checks.text()
        assert calls == []
    finally:
        dialog.close()
        qapp.setPalette(original_palette)
