"""The comparison stays usable with the real skin, long content and small windows."""

from dataclasses import replace
from types import SimpleNamespace

from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QDialogButtonBox, QScrollArea
import pytest

from tests.ui.responsive_layout_support import apply_layout_font, settle_layout, themed_layout_app  # noqa: F401
from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode
from transbridge.ui.foundation.builtins import create_builtin_registry
from transbridge.ui.foundation.theme_service import ThemeService
from transbridge.ui.operations.sync_review_dialog import SyncReviewDialog
from transbridge.ui.operations.sync_review_view import SyncReviewCandidate, SyncReviewRow


@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
@pytest.mark.parametrize("point_size", [9, 12])
def test_small_window_keeps_footer_reachable_and_long_text_scrollable(layout_app, mode, point_size):
    preferences = SimpleNamespace(
        load=lambda: SimpleNamespace(theme_mode=mode, theme_id=DEFAULT_THEME_ID, diagnostics=())
    )
    theme = ThemeService(layout_app, create_builtin_registry(), preferences)
    result = theme.start()
    assert result.snapshot is not None
    candidate = SyncReviewCandidate("cloud", "x" * 3000, "原文很长\n" * 200, "译文很长\n" * 200, "场景\n" * 200)
    row = SyncReviewRow("local", "key" * 1000, candidate.original, candidate.translation, candidate.context, "原文不同")
    subject = SyncReviewDialog((replace(row, candidates=(candidate,)),))
    apply_layout_font(subject, result.snapshot, point_size)
    subject.resize(900, 620)
    subject.show()
    subject._detail.candidates.setCurrentIndex(1)
    settle_layout(layout_app)
    try:
        assert subject.size().width() <= 900
        assert subject.size().height() <= 620
        assert subject._table.rowHeight(0) >= subject._table.fontMetrics().height() * 3 + 28
        finish = subject._buttons.button(QDialogButtonBox.StandardButton.Apply)
        origin = finish.mapTo(subject, QPoint(0, 0))
        assert subject.rect().contains(origin)
        assert subject.rect().contains(origin + QPoint(finish.width() - 1, finish.height() - 1))
        assert subject._detail.remote.translation.verticalScrollBar().maximum() > 0
        for card in (subject._detail.local, subject._detail.remote):
            assert card.original.geometry().bottom() < card.translation.geometry().top()
            assert card.translation.geometry().bottom() < card.context.geometry().top()
            assert card.rect().contains(card.context.geometry())
        scroll = subject.findChild(QScrollArea)
        scroll.ensureWidgetVisible(subject._detail.use_remote)
        settle_layout(layout_app)
        action_origin = subject._detail.use_remote.mapTo(scroll.viewport(), QPoint(0, 0))
        assert scroll.viewport().rect().contains(action_origin)
        assert subject.decisions() == {}
    finally:
        subject.close()
        theme.close()
