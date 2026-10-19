"""Keep common terminology and history actions reachable with real large text."""

from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialogButtonBox, QLabel, QLineEdit, QMessageBox, QPushButton
import pytest

from tests.ui.responsive_layout_support import (
    apply_layout_font,
    settle_layout,
    themed_layout_app as themed_layout_app,
)
from tests.ui.tools.terminology.test_operation_feedback import _window as terminology_window
from tests.ui.tools.test_history_search_interactions import _window as history_window
from tests.ui.tools.test_history_search_window import _hit
from tests.ui.tools.test_terminology_profile_manager import _service
from transbridge.application.history_search import HistorySearchPage
from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology.models import DecisionStatus, TermDecision, TermScope
from transbridge.application.terminology_profiles import (
    TerminologyProfileImportService,
    TerminologySourceEntry,
    TerminologySourceSnapshot,
)
from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode
from transbridge.ui.foundation.builtins import create_builtin_registry
from transbridge.ui.foundation.theme_service import ThemeApplyStatus, ThemePreference, ThemeService
from transbridge.ui.tools.terminology.view_models import TerminologyNotice
from transbridge.ui.tools.terminology_profiles import TerminologyProfileManagerDialog, TerminologySourceImportDialog


@pytest.fixture
def theme(layout_app):
    preferences = SimpleNamespace(
        load=lambda: SimpleNamespace(theme_mode=ThemeMode.LIGHT, theme_id=DEFAULT_THEME_ID, diagnostics=())
    )
    service = ThemeService(layout_app, create_builtin_registry(), preferences)
    service.start()
    yield service
    service.close()


def _large_narrow(view, theme, app, mode):
    result = theme.set_preference(ThemePreference(mode, DEFAULT_THEME_ID), persist=False)
    assert result.status is not ThemeApplyStatus.FAILED
    assert result.snapshot.effective_scheme.value == mode.value
    apply_layout_font(view, result.snapshot, 14)
    view.resize(760, 560)
    view.show()
    settle_layout(app)
    assert view.font().pointSizeF() == 14
    assert view.width() == 760
    assert view.height() == 560


def _inside(view, widget):
    assert view.rect().contains(widget.mapTo(view, widget.rect().topLeft()))
    assert view.rect().contains(widget.mapTo(view, widget.rect().bottomRight()))


def test_long_profile_name_keeps_edit_save_and_cancel_reachable(layout_app, theme, monkeypatch):
    service, profile_id, _content = _service()
    name = "一个较长但真实可用的社区译名方案名称" * 4
    service.rename(profile_id, name)
    view = TerminologyProfileManagerDialog(service, "project-a", variant_id="variant-a")
    monkeypatch.setattr(
        "transbridge.ui.tools.terminology_profiles.manager_dialog.choose_unsaved_action",
        lambda *_args: QMessageBox.StandardButton.Cancel,
    )
    try:
        for mode in (ThemeMode.LIGHT, ThemeMode.DARK, ThemeMode.LIGHT):
            _large_narrow(view, theme, layout_app, mode)
            assert view.mapping_search.font().pointSizeF() == 14
            for button in (view.save_button, view.publish_button, view.publish_use_button, view.close_button):
                _inside(view, button)
            assert name in view.profile_combo.toolTip()
        table = view.mapping_table
        index = table.model().index(0, 2)
        table.setCurrentIndex(index)
        table.edit(index)
        editor = table.findChild(QLineEdit)
        assert editor is not None
        editor.selectAll()
        QTest.keyClicks(editor, "Saved Dragon")
        view.mapping_search.setText("Dragon")
        assert table.model().index(0, 2).data() == "Saved Dragon"
        view.close_button.click()
        assert view.isVisible()
        assert table.is_dirty
        view.save_button.click()
        assert service.list_profiles("project-a")[0].draft.mappings[0].translation == "Saved Dragon"
        assert not table.is_dirty
    finally:
        monkeypatch.setattr(
            "transbridge.ui.tools.terminology_profiles.manager_dialog.choose_unsaved_action",
            lambda *_args: QMessageBox.StandardButton.Discard,
        )
        view.close()


def _import_preview():
    service, _profile_id, _content = _service()
    decision = TermDecision(
        "one", "project-a", "main", "Whiterun", "whiterun", "雪漫城", TermScope.project(), DecisionStatus.ADOPTED
    )
    base = EffectiveTerminologySnapshot(
        "project-a",
        "main",
        EffectiveSnapshotStatus.READY,
        version_id="one",
        content_digest="a" * 64,
        decisions=(decision,),
    )
    source = TerminologySourceSnapshot.capture(
        "json",
        "本地 JSON · C:/Data/" + "CommunityTranslation/" * 10 + "terms.json",
        (TerminologySourceEntry("Whiterun", "白漫城"), TerminologySourceEntry("Riverwood", "河木镇")),
    )
    return TerminologyProfileImportService(service).preview("project-a", "main", base, source)


def test_long_import_path_preserves_preview_filter_details_and_cancel(layout_app, theme):
    preview = _import_preview()
    view = TerminologySourceImportDialog(preview, "社区方案")
    try:
        for mode in (ThemeMode.LIGHT, ThemeMode.DARK):
            _large_narrow(view, theme, layout_app, mode)
            assert view.name_edit.font().pointSizeF() == 14
            _inside(view, view.create_button)
        source = next(field for field in view.findChildren(QLineEdit) if field.isReadOnly())
        assert source.text() == preview.source.source_label
        assert source.toolTip() == preview.source.source_label
        view.search_edit.setText("Riverwood")
        QTest.qWait(300)
        settle_layout(layout_app)
        assert view.filtered.rowCount() == 1
        assert "Riverwood" in view.row_details.toPlainText()
        view.search_edit.clear()
        assert view.filtered.rowCount() == 2
        view.select_check.click()
        assert view.create_button.text() == "创建并选用"
        view.name_edit.clear()
        assert not view.create_button.isEnabled()
        view.name_edit.setText("保留完整预览")
        view.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Cancel).click()
        assert view.result() == view.DialogCode.Rejected
    finally:
        view.close()


def test_history_narrow_search_and_long_scope_keep_query_copy_flow(layout_app, theme):
    view, _tasks = history_window(layout_app)
    try:
        view.scope_combo.setItemText(1, "项目｜" + "很长的社区翻译项目名称" * 8)
        for mode in (ThemeMode.LIGHT, ThemeMode.DARK):
            _large_narrow(view, theme, layout_app, mode)
            assert view.search_edit.font().pointSizeF() == 14
            assert view.search_edit.width() >= 600
            for widget in (view.scope_combo, view.kind_combo, view.refresh_button, view.copy_button):
                _inside(view, widget)
        view.search_edit.setFocus()
        QTest.keyClicks(view.search_edit, "Whiterun")
        QTest.keyClick(view.search_edit, Qt.Key.Key_Return)
        assert len(view._pool.requests) == 1
        view._reject_query(view._generation, RuntimeError("synthetic failure"))
        assert not view.copy_button.isEnabled()
        QTest.keyClick(view.search_edit, Qt.Key.Key_Return)
        view._accept_query(view._generation, HistorySearchPage((_hit("Selected translation"),), 1))
        view.copy_button.click()
        assert layout_app.clipboard().text() == "Selected translation"
    finally:
        view.close()


def test_terminology_report_descriptions_and_retry_remain_reachable(layout_app, theme):
    view = terminology_window()
    view._preflight_started = True
    reports = view.reports_view
    try:
        view.workspace.set_current_index(2)
        for mode in (ThemeMode.LIGHT, ThemeMode.DARK):
            _large_narrow(view, theme, layout_app, mode)
            description = next(label for label in reports.findChildren(QLabel) if "面向发布说明" in label.text())
            assert description.font().pointSizeF() == 14
            assert description.height() >= description.fontMetrics().height()
            assert description.height() >= description.heightForWidth(description.width())
        reports.set_notice(
            TerminologyNotice(
                "生成失败",
                "写入报告时文件被占用。" * 8,
                "草稿和发布版本不受影响。",
                "关闭目标文件后重试。",
                "重试生成更新日志",
            )
        )
        retries = []
        reports.retry_requested.connect(lambda: retries.append(True))
        settle_layout(layout_app)
        export = next(button for button in reports.findChildren(QPushButton) if button.text() == "导出 Excel")
        export.setFocus()
        for _ in range(10):
            QTest.keyClick(layout_app.focusWidget(), Qt.Key.Key_Tab)
            settle_layout(layout_app)
            if reports.retry.hasFocus():
                break
        assert reports.retry.hasFocus()
        _inside(view, reports.retry)
        QTest.keyClick(reports.retry, Qt.Key.Key_Space)
        assert retries == [True]
    finally:
        view.close()
