"""Unbroken names and diagnostic paths must not become window minimum widths."""

from dataclasses import replace
from types import SimpleNamespace

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QLabel
import pytest

from tests.ui.responsive_layout_support import settle_layout, themed_layout_app as themed_layout_app
from transbridge.ui.coordinators.guided_project_coordinator import GuidedProjectDraftState
from transbridge.ui.operations.batch_download_dialog import BatchDownloadDialog
from transbridge.ui.operations.categorized_upload_dialog import CategorizedUploadDialog
from transbridge.ui.shell.plugin_drop_dialog import PluginDropDialog
from transbridge.ui.shell.start_center import StartCenterViewState, StartCenterWidget, StartDestinationState
from transbridge.ui.tools.dictionary_panel import DictionaryPanel
from transbridge.ui.tools.terminology.source_browser import SourceBrowser
from transbridge.ui.workbench.remote_target_view import ParaTranzTargetDialog

LONG_NAME = "PluginName" * 18
LONG_PATH = f"D:/Translations/{LONG_NAME}.esm"
LONG_ERROR = f"Cannot read {LONG_PATH}"


def _assert_resizable(app, view, labels, *, widths=(850, 1200, 720, 850)):
    for width in widths:
        view.resize(width, 750)
        settle_layout(app)
        assert view.width() == width
        for label in labels:
            assert view.rect().contains(label.rect().translated(label.mapTo(view, QPoint())))
            assert label.height() >= label.heightForWidth(label.width())


@pytest.mark.parametrize("field", ["source_path", "project_name", "default_variant_name", "diagnostic_message"])
def test_draft_long_tokens_keep_full_text_and_window_resizable(layout_app, field):
    view = StartCenterWidget()
    state = GuidedProjectDraftState("D:/Base.esp", "Base")
    view.render_draft(state)
    view.resize(850, 750)
    view.show()
    try:
        settle_layout(layout_app)
        value = LONG_PATH if field == "source_path" else LONG_NAME
        changes = {field: value, "revision": 1}
        if field == "diagnostic_message":
            changes["diagnostic_code"] = "SOURCE_ERROR"
        updated = replace(state, **changes)
        view.render_draft(updated)
        settle_layout(layout_app)
        assert view.width() == 850
        labels = (view._source_label, view._draft_summary, view._draft_error)
        if field == "source_path":
            item = view._source_list.item(0)
            assert item.toolTip() == value
            assert item.data(Qt.ItemDataRole.AccessibleTextRole) == value
        elif field in {"project_name", "default_variant_name"}:
            editor = view._name_edit if field == "project_name" else view._variant_edit
            assert editor.text() == value
            assert editor.toolTip() == value
            assert view._draft_summary.full_text == updated.summary
            assert view._draft_summary.accessibleDescription() == updated.summary
        else:
            assert value in view._draft_error.text()
        _assert_resizable(layout_app, view, labels)
        view.render(StartCenterViewState(StartDestinationState.START_CENTER_USER_REQUESTED, 1))
        _assert_resizable(layout_app, view, ())
    finally:
        view.close()


def test_plugin_drop_long_path_and_notice_keep_actions_reachable(layout_app):
    view = PluginDropDialog(LONG_PATH, LONG_NAME, add_unavailable=LONG_ERROR)
    view.show()
    try:
        _assert_resizable(layout_app, view, (view.summary, view.notice))
        assert LONG_PATH in view.summary.text()
        assert view.notice.text() == LONG_ERROR
        assert view.rect().contains(view.new_button.rect().translated(view.new_button.mapTo(view, QPoint())))
        assert not view.add_button.isEnabled()
    finally:
        view.close()


@pytest.mark.parametrize("dialog_type", [BatchDownloadDialog, CategorizedUploadDialog])
@pytest.mark.parametrize("field", ["project", "variant", "target", "status"])
def test_transfer_dialog_long_context_and_diagnostics(layout_app, monkeypatch, dialog_type, field):
    # Layout checks must not launch source loading or any cloud worker.
    monkeypatch.setattr(dialog_type, "_load_sources", lambda self: None)
    callbacks = (
        dict(load_sources=lambda: (), compare=lambda: None, apply=lambda: None)
        if dialog_type is BatchDownloadDialog
        else dict(load_preview=lambda: (), upload=lambda: None)
    )
    names = {key: LONG_NAME if key == field else "Default" for key in ("project", "variant", "target")}
    view = dialog_type(names["project"], names["variant"], names["target"], **callbacks)
    view.resize(850, 750)
    view.show()
    try:
        if field == "status":
            view._status.setText(LONG_ERROR)
            view._summary.setText(LONG_PATH)
        settle_layout(layout_app)
        assert view.width() == 850
        scope = next(label for label in view.findChildren(QLabel) if label.text().startswith("当前工程："))
        if field != "status":
            assert names[field] in scope.text()
        else:
            assert view._status.text() == LONG_ERROR
        _assert_resizable(layout_app, view, (scope, view._status, view._summary))
    finally:
        view.close()


def test_target_picker_error_does_not_lock_width(layout_app):
    context = SimpleNamespace(
        config=SimpleNamespace(base_url="https://paratranz.cn", token="", user_id=None), current_user=None
    )
    view = ParaTranzTargetDialog(context, None, 0)
    view.resize(520, 750)
    view.show()
    try:
        view._failed(0, LONG_ERROR)
        settle_layout(layout_app)
        assert view.width() == 520
        assert LONG_ERROR in view._status.text()
        _assert_resizable(layout_app, view, (view._status,), widths=(520, 850, 520))
    finally:
        view.close()


def test_dictionary_load_error_does_not_lock_width(layout_app, monkeypatch):
    def fail_load():
        raise RuntimeError(LONG_ERROR)

    monkeypatch.setattr(
        "transbridge.ui.tools.dictionary_panel.TranslationMemoryManager",
        lambda **kwargs: SimpleNamespace(load=fail_load),
    )
    view = DictionaryPanel(None)
    view.show()
    try:
        assert LONG_ERROR in view._status.text()
        # The existing toolbar needs about 780 px even with a short status.
        _assert_resizable(layout_app, view, (view._status,), widths=(850, 1200, 800, 850))
    finally:
        view.close()


def test_mapping_search_error_does_not_lock_width(layout_app):
    view = SourceBrowser()
    view.resize(850, 750)
    view.show()
    try:
        view._search_status(False, LONG_ERROR)
        settle_layout(layout_app)
        assert view.width() == 850
        assert view.status.text() == LONG_ERROR
        _assert_resizable(layout_app, view, (view.status,))
    finally:
        view.close()
