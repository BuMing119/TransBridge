from __future__ import annotations

from types import SimpleNamespace

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QPalette
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode
from transbridge.ui.foundation.accessibility import (
    AccessibleStateCue,
    ContrastPair,
    ContrastRole,
    validate_contrast_pairs,
    validate_state_cue,
)
from transbridge.ui.foundation.builtins import create_builtin_registry
from transbridge.ui.foundation.theme_service import ThemeService
from transbridge.ui.operations.plan_dialog import OperationPlanDialog
from transbridge.ui.operations.plan_view import OperationKind, OperationPlanViewState
from transbridge.ui.paratranz.strings_tab import StringsTab
from transbridge.ui.settings_dialog import SettingsDialog
from transbridge.ui.shell.status_presenter import ApiStatusIndicator
from transbridge.ui.tools.smart_assistant.thinking_indicator import ThinkingIndicator
from transbridge.ui.workbench.translation_table import TranslationTable


class _Preferences:
    def __init__(self, mode: ThemeMode = ThemeMode.SYSTEM) -> None:
        self.mode = mode

    def load(self):
        return SimpleNamespace(theme_mode=self.mode, theme_id=DEFAULT_THEME_ID, diagnostics=())

    def save_theme_preference(self, mode: ThemeMode, theme_id: str):
        self.mode = mode
        return SimpleNamespace(saved=True, diagnostic_code=None, message="")


class _ParaContext(QObject):
    paratranz_permissions_changed = pyqtSignal()
    project_selected = pyqtSignal(object)
    config = SimpleNamespace(token="")

    @staticmethod
    def is_admin() -> bool:
        return False


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _color_pairs(snapshot) -> list[ContrastPair]:
    semantic = snapshot.tokens.semantic
    pairs = [
        ContrastPair("primary/window", semantic.text_primary, semantic.window),
        ContrastPair("primary/surface", semantic.text_primary, semantic.surface),
        ContrastPair("secondary/window", semantic.text_secondary, semantic.window),
        ContrastPair("selection", semantic.selection_text, semantic.selection_background),
        ContrastPair("focus/window", semantic.focus, semantic.window, ContrastRole.FOCUS_INDICATOR),
        ContrastPair("link/window", semantic.link, semantic.window, ContrastRole.UI_COMPONENT),
    ]
    for category in ("stages", "labels", "diff", "translation", "task", "report"):
        for state in getattr(snapshot.tokens.domain, category):
            pairs.append(ContrastPair(f"{category}/{state.key}", state.foreground, state.background))
    return pairs


@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK, ThemeMode.SYSTEM])
def test_theme_matrix_declares_contrast_and_non_colour_state_contracts(qapp: QApplication, mode: ThemeMode) -> None:
    original_palette = QPalette(qapp.palette())
    preferences = _Preferences(mode)
    service = ThemeService(qapp, create_builtin_registry(), preferences)  # type: ignore[arg-type]
    try:
        result = service.start()
        assert result.snapshot is not None
        assert validate_contrast_pairs(_color_pairs(result.snapshot)).valid
        cues = (
            AccessibleStateCue("shell", visible_text="● 正常"),
            AccessibleStateCue("workbench", visible_text="有疑问"),
            AccessibleStateCue("smart", visible_text="正在思考中"),
            AccessibleStateCue("paratranz", visible_text="待审核"),
            AccessibleStateCue("operation", visible_text="✓ 预检通过"),
        )
        assert all(validate_state_cue(cue) == () for cue in cues)

        shell = ApiStatusIndicator()
        workbench = TranslationTable(on_progress=lambda *_: None, on_batch=lambda: None)
        smart = ThinkingIndicator()
        paratranz = StringsTab(_ParaContext())
        operation = OperationPlanDialog(
            OperationPlanViewState(
                session_id="matrix",
                revision=1,
                kind=OperationKind.UPLOAD,
                title="上传计划",
                target="ParaTranz",
                scope_summary="1 个文件",
                mode_summary="覆盖",
                conflict_summary="无",
                backup_summary="已启用",
                estimated_impact=(("files", 1),),
            )
        )
        assert shell.accessibleName() and shell.accessibleDescription()
        assert workbench.accessibleName() and workbench.accessibleDescription()
        assert smart.accessibleName() and smart.accessibleDescription()
        assert paratranz._table.accessibleName() and paratranz._table.accessibleDescription()
        assert operation.accessibleName() and operation.accessibleDescription()
        for widget in (shell, workbench, smart, paratranz, operation):
            widget.close()
    finally:
        service.close()
        qapp.setPalette(original_palette)


@pytest.mark.parametrize("scale", [1.0, 1.5, 2.0])
def test_settings_critical_controls_remain_reachable_with_font_scaling(qapp: QApplication, scale: float) -> None:
    original_palette = QPalette(qapp.palette())
    service = ThemeService(qapp, create_builtin_registry(), _Preferences())  # type: ignore[arg-type]
    service.start()
    dialog = SettingsDialog(service, _Preferences(), registry=create_builtin_registry())
    try:
        font = QFont(dialog.font())
        font.setPointSizeF(font.pointSizeF() * scale)
        dialog.setFont(font)
        dialog.resize(dialog.sizeHint())
        dialog.show()
        qapp.processEvents()
        assert dialog._scroll.widgetResizable()
        assert dialog._buttons.parentWidget() is dialog
        assert all(
            button.isVisible() for button in (dialog._apply_button, dialog._default_button, dialog._cancel_button)
        )
        assert dialog._mode_combo.accessibleDescription()
        assert dialog._theme_combo.accessibleDescription()
        assert dialog.accessibleDescription()
        dialog._mode_combo.setFocus()
        QTest.keyClick(dialog._mode_combo, Qt.Key.Key_Tab)
        assert dialog.focusWidget() is dialog._theme_combo
        requests: list[bool] = []
        dialog.service_settings_requested.connect(lambda: requests.append(True))
        dialog._api_button.setFocus()
        QTest.keyClick(dialog._api_button, Qt.Key.Key_Return)
        assert requests == [True]
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        assert dialog.result() == dialog.DialogCode.Rejected
    finally:
        dialog.reject()
        service.close()
        qapp.setPalette(original_palette)
