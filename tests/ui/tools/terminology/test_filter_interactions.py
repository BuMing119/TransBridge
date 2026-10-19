"""Keyboard and clear/reset filtering keep visible controls and queries aligned."""

from types import SimpleNamespace

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QToolButton
import pytest

from tests.application.terminology.test_build import _entry
from tests.ui.responsive_layout_support import settle_layout, themed_layout_app as themed_layout_app
from tests.ui.tools.terminology import test_workflow_routes as workflow_tests
from tests.ui.tools.terminology.test_workflow_routes import _wait
from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode
from transbridge.ui.foundation.builtins import create_builtin_registry
from transbridge.ui.foundation.model import ThemeScheme
from transbridge.ui.foundation.theme_service import ThemeApplyStatus, ThemePreference, ThemeService
from transbridge.ui.tools.terminology.asset_snapshot import WorkbenchAssets


@pytest.fixture(name="workbench")
def filter_workbench(tmp_path, monkeypatch):
    yield from workflow_tests.workbench.__wrapped__(tmp_path, monkeypatch)


def _clear_text(editor):
    clear = next(button for button in editor.findChildren(QToolButton) if button.isVisible())
    QTest.mouseClick(clear, Qt.MouseButton.LeftButton)


def test_term_enter_clear_and_reset_restore_actual_rows_with_one_query(workbench, layout_app):
    window, harness = workbench
    harness.parser.entries["source-a"] += (_entry("source-a", "two", "Sword", "剑"),)
    harness.build()
    window._bind_existing_assets()
    _wait(lambda: window.draft_model.rowCount() == 2)
    window.terms_view.show_draft()
    window.show()
    settle_layout(layout_app)
    filters = window.draft_view.filters
    queries = []
    filters.changed.connect(queries.append)
    QTest.keyClicks(filters.search, "Dragon")
    assert not queries
    QTest.keyClick(filters.search, Qt.Key.Key_Return)
    _wait(lambda: window.draft_model.rowCount() == 1)
    assert len(queries) == 1
    _clear_text(filters.search)
    _wait(lambda: window.draft_model.rowCount() == 2)
    assert len(queries) == 2 and queries[-1].search == ""
    filters.scope.setCurrentIndex(2)
    _wait(lambda: window.draft_model.rowCount() == 0)
    assert len(queries) == 3
    QTest.mouseClick(filters.reset_button, Qt.MouseButton.LeftButton)
    _wait(lambda: window.draft_model.rowCount() == 2)
    assert len(queries) == 4
    assert filters.scope.currentData() is None and filters.status.currentData() is None
    assert not filters.reset_button.isEnabled()


def test_conflict_keyboard_risk_clear_and_reset_emit_one_consistent_query(workbench, layout_app):
    window, _harness = workbench
    view = window.conflicts_view
    window.terms_view.show_attention()
    window.show()
    settle_layout(layout_app)
    queries = []
    view.query_changed.connect(lambda *query: queries.append(query))
    QTest.keyClicks(view.search, "Dragon")
    assert not queries
    QTest.keyClick(view.search, Qt.Key.Key_Return)
    assert queries == [("Dragon", "all")]
    QTest.keyClick(view.risk, Qt.Key.Key_Down)
    assert queries[-1] == ("Dragon", "high") and len(queries) == 2
    _clear_text(view.search)
    assert queries[-1] == ("", "high") and len(queries) == 3
    QTest.mouseClick(view.reset_button, Qt.MouseButton.LeftButton)
    assert queries[-1] == ("", "all") and len(queries) == 4
    assert not view.reset_button.isEnabled()
    assert view.search.accessibleName() and view.risk.accessibleName()


@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
def test_terminology_action_shapes_and_filter_keyboard_fit_narrow_window(workbench, layout_app, mode):
    preferences = SimpleNamespace(
        load=lambda: SimpleNamespace(theme_mode=mode, theme_id=DEFAULT_THEME_ID, diagnostics=())
    )
    service = ThemeService(layout_app, create_builtin_registry(), preferences)
    assert service.start().status is not ThemeApplyStatus.FAILED
    result = service.set_preference(ThemePreference(mode, DEFAULT_THEME_ID), persist=False)
    assert result.status is not ThemeApplyStatus.FAILED
    assert result.snapshot.effective_scheme is (ThemeScheme.LIGHT if mode is ThemeMode.LIGHT else ThemeScheme.DARK)
    window, _harness = workbench
    try:
        window._workflow.render(
            WorkbenchAssets(
                draft=SimpleNamespace(decisions=()),
                build=SimpleNamespace(completeness="full", freshness="current"),
                review_counts=SimpleNamespace(review_required_terms=1, unresolved_conflicts=1),
            )
        )
        window.resize(920, 620)
        window.show()
        terms = window.terms_view
        terms.show_draft()
        settle_layout(layout_app)
        filters = window.draft_view.filters
        actions = (
            filters.apply_button,
            filters.reset_button,
            window.conflicts_view.review_button,
            terms.build_details_button,
            window.build_view.build_button,
            terms.review_button,
        )
        reference_height = window.conflicts_view.pagination.next_button.sizeHint().height()
        for button in actions:
            assert button.property("tbComponentKind") == "button"
            assert button.sizeHint().height() == reference_height
        filters.search.setText("not yet applied")
        window.activateWindow()
        filters.apply_button.setFocus()
        settle_layout(layout_app)
        assert filters.apply_button.hasFocus()
        QTest.keyClick(filters.apply_button, Qt.Key.Key_Tab)
        assert filters.reset_button.hasFocus()
        for page, controls in (
            (1, (filters.search, filters.scope, filters.status, filters.apply_button, filters.reset_button)),
            (
                2,
                (
                    window.conflicts_view.search,
                    window.conflicts_view.risk,
                    window.conflicts_view.apply_filter,
                    window.conflicts_view.reset_button,
                ),
            ),
        ):
            terms._set_page(page)
            settle_layout(layout_app)
            assert window.width() == 920
            for widget in controls:
                left = widget.mapTo(window, QPoint()).x()
                assert 0 <= left <= window.width() - widget.width()
            for before, after in zip(controls, controls[1:]):
                assert not before.geometry().intersects(after.geometry())
        terms.review_button.setEnabled(False)
        settle_layout(layout_app)
        assert terms.review_button.sizeHint().height() == reference_height
    finally:
        service.close()
