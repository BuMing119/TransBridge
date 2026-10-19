"""Many filter chips wrap while retaining their keyboard and filtering contracts."""

from PyQt6 import sip
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtTest import QTest

from tests.ui.responsive_layout_support import settle_layout, themed_layout_app as themed_layout_app
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.ui.project_labels import entry_label_key
from transbridge.ui.workbench.filters_presenter import FilterState
from transbridge.ui.workbench.filters_view import FiltersView


def _labels(view, count=30):
    entry = TranslationEntry("entry", "key", "Original", "译文", 0, "NPC_:FULL")
    library = {str(index): {"name": f"测试标签 {index}", "color": "#008877"} for index in range(count)}
    view.build_labels((entry,), library, {entry_label_key(entry): set(library)})


def test_many_labels_wrap_without_overlap_or_window_growth(layout_app):
    view = FiltersView(on_changed=lambda: None, on_manage_labels=lambda: None)
    _labels(view)
    view.set_content_visible(True)
    view.advanced_button.setChecked(True)
    view.resize(950, 500)
    view.show()
    try:
        settle_layout(layout_app)
        assert view.width() == 950
        buttons = [view.label_container.itemAt(index).widget() for index in range(view.label_container.count())]
        assert len({button.y() for button in buttons}) > 1
        for index, button in enumerate(buttons):
            assert button.parentWidget().rect().contains(button.geometry())
            assert all(not button.geometry().intersects(other.geometry()) for other in buttons[index + 1 :])
        view.resize(1200, 500)
        settle_layout(layout_app)
        assert view.width() == 1200
        assert all(button.parentWidget().rect().contains(button.geometry()) for button in buttons)
    finally:
        view.close()


def test_wrapped_labels_keep_keyboard_state_and_release_rebuilt_buttons(layout_app):
    changed = []
    view = FiltersView(on_changed=lambda: changed.append(True), on_manage_labels=lambda: None)
    _labels(view)
    view.set_content_visible(True)
    view.advanced_button.setChecked(True)
    view.resize(950, 500)
    view.show()
    try:
        settle_layout(layout_app)
        view.label_container.itemAt(0).widget().setFocus()
        for expected in view._label_buttons:
            QTest.keyClick(layout_app.focusWidget(), Qt.Key.Key_Tab)
            assert layout_app.focusWidget() is expected
        button = view._label_buttons[-1]
        button.setFocus()
        QTest.keyClick(button, Qt.Key.Key_Space)
        assert view.state().labels == frozenset({"29"})
        assert changed == [True]
        view.apply_state(FilterState(labels=frozenset({"2", "29"})))
        old_buttons = tuple(view._label_buttons)
        _labels(view)
        layout_app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        settle_layout(layout_app)
        assert all(sip.isdeleted(old) for old in old_buttons)
        assert view.label_container.count() == 31
        assert [index for index, current in enumerate(view._label_buttons) if current.isChecked()] == [2, 29]
        assert view.state().labels == frozenset({"2", "29"})
        view.advanced_button.setChecked(False)
        view.advanced_button.setChecked(True)
        settle_layout(layout_app)
        assert view.width() == 950
        assert view.state().labels == frozenset({"2", "29"})
    finally:
        view.close()
