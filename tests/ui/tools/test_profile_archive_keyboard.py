"""Enter while reviewing archive impact must choose the non-destructive default."""

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialogButtonBox, QListWidget
import pytest

from tests.ui.responsive_layout_support import settle_layout, themed_layout_app as themed_layout_app
from transbridge.ui.tools.terminology_profiles.edit_prompts import confirm_archive


@pytest.mark.parametrize("action,expected", [("enter", False), ("escape", False), ("archive", True)])
def test_archive_impact_requires_explicit_destructive_action(layout_app, action, expected):
    seen = []

    def interact():
        dialog = layout_app.activeModalWidget()
        settle_layout(layout_app)
        listing = dialog.findChild(QListWidget)
        listing.setFocus()
        seen.append(listing.count())
        if action == "archive":
            buttons = dialog.findChild(QDialogButtonBox)
            button = buttons.button(QDialogButtonBox.StandardButton.Ok)
            QTest.keyClick(listing, Qt.Key.Key_Tab)
            assert layout_app.focusWidget() is button
            QTest.keyClick(button, Qt.Key.Key_Tab)
            assert layout_app.focusWidget() is buttons.button(QDialogButtonBox.StandardButton.Cancel)
            QTest.keyClick(layout_app.focusWidget(), Qt.Key.Key_Backtab)
            assert layout_app.focusWidget() is button
            QTest.keyClick(button, Qt.Key.Key_Return)
        else:
            QTest.keyClick(listing, Qt.Key.Key_Return if action == "enter" else Qt.Key.Key_Escape)

    QTimer.singleShot(0, interact)
    assert confirm_archive(None, "社区方案", (), {}) is expected
    assert seen == [1]
