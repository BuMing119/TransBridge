import time

from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QApplication, QMessageBox
import pytest

from tests.application.terminology.test_build import _entry
from tests.integration.bootstrap.test_terminology_build_review import _Harness
from transbridge.persistence.terminology import SqliteTerminologyRepository
from transbridge.ui.tools.terminology.presenter import TerminologyPresenter, TerminologyUiServices
from transbridge.ui.tools.terminology.window import TerminologyWindow

_APP = QApplication.instance() or QApplication([])


def wait(predicate):
    deadline = time.monotonic() + 4
    while not predicate() and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.002)
    assert predicate()


@pytest.fixture
def review(tmp_path, monkeypatch):
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    harness = _Harness(repository, monkeypatch)
    harness.parser.entries = {
        "source-a": tuple(
            _entry("source-a", f"{i}-{j}", f"Term {i}", text)
            for i in range(5)
            for j, text in enumerate(("译名甲", "译名乙"))
        )
    }
    harness.build()
    presenter = TerminologyPresenter(
        TerminologyUiServices(queries=repository, commands=harness.commands), harness.context
    )
    window = TerminologyWindow(presenter)
    window._preflight_started = True
    window._bind_existing_assets()
    window.terms_view.show_attention()
    wait(lambda: window.conflicts_view.pane.details is not None)
    try:
        yield window, harness
    finally:
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
        wait(lambda: window._draft_writes.pending_count == 0)
        wait(lambda: not window._asset_refresh._active and not window._asset_refresh._dirty)
        window.close()
        QThreadPool.globalInstance().waitForDone(3000)
        _APP.processEvents()
        repository.close()


def test_save_next_undo_and_resolved_translation(review):
    window, harness = review
    view = window.conflicts_view
    pane = view.pane
    first = pane.details.conflict
    following = view.next_conflict()
    assert not pane.save_button.isEnabled()
    pane.candidate_buttons[1].click()
    assert pane.original_text.toPlainText()
    pane.save_button.click()
    wait(lambda: pane.details is not None and pane.details.conflict.conflict_group_id == following.conflict_group_id)
    assert window._conflict_filter.start_at == following.conflict_group_id
    assert view.undo_button.isEnabled()
    view.undo_button.click()
    wait(lambda: pane.details is not None and pane.details.conflict.conflict_group_id == first.conflict_group_id)
    assert pane.details.conflict.status == "unresolved"
    assert not pane.save_button.isEnabled()
    assert not view.undo_button.isEnabled()
    pane.candidate_buttons[0].click()
    selected = pane.translation.text()
    pane.save_button.click()
    wait(lambda: pane.details is not None and pane.details.conflict.conflict_group_id != first.conflict_group_id)
    view.tabs.setCurrentIndex(1)
    wait(lambda: pane.details is not None and pane.details.conflict.status == "unified")
    assert pane.translation.text() == selected
    assert view.model.rowCount() == 1
    assert pane.save_button.text() == "保存修改"


def test_one_click_exclude_saves_advances_and_can_be_undone(review, monkeypatch):
    window, harness = review
    view = window.conflicts_view
    pane = view.pane
    original = pane.details.conflict
    following = view.next_conflict()

    def unexpected_prompt(*args, **kwargs):
        raise AssertionError("Direct exclude must not ask for another confirmation")

    monkeypatch.setattr(QMessageBox, "question", unexpected_prompt)
    assert not pane.translation.text()
    pane.exclude_button.click()
    assert not pane.exclude_button.isEnabled()
    wait(lambda: pane.details is not None and pane.details.conflict == following)
    draft = harness.repository.active_draft("project-1", "main")
    assert (
        next(c for c in draft.conflict_resolutions if c.conflict_group_id == original.conflict_group_id).status
        == "ignored"
    )
    assert any(d.suppressed for d in draft.decisions if d.normalized_original == original.normalized_original)
    view.undo_button.click()
    wait(lambda: pane.details is not None and pane.details.conflict == original)
    assert pane.exclude_button.isEnabled()


def test_failed_exclude_stays_on_same_term_and_can_retry(review, monkeypatch):
    window, _ = review
    pane = window.conflicts_view.pane
    original = pane.details

    def fail(*args, **kwargs):
        raise ValueError("保存失败测试")

    monkeypatch.setattr(window.presenter, "resolve_review", fail)
    pane.exclude_button.click()
    wait(lambda: "保存失败测试" in pane.error.text())
    assert pane.details == original
    assert pane.exclude_button.isEnabled()


def test_failed_save_keeps_input_and_rejects_close(review, monkeypatch):
    window, _ = review
    pane = window.conflicts_view.pane
    pane.candidate_buttons[0].click()
    text = pane.translation.text()

    def fail(*args, **kwargs):
        raise ValueError("模拟过期草稿")

    monkeypatch.setattr(window.presenter, "resolve_review", fail)
    pane.save_button.click()
    wait(lambda: "模拟过期草稿" in pane.error.text())
    assert pane.translation.text() == text
    assert pane.has_unsaved_inputs
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    window.terms_view.conflict_dialog.close()
    assert window.terms_view.conflict_dialog.isVisible()


def test_window_is_bounded_and_queue_does_not_use_row_buttons(review):
    window, _ = review
    dialog = window.terms_view.conflict_dialog
    view = window.conflicts_view
    assert dialog.frameGeometry().height() <= dialog.screen().availableGeometry().height()
    assert view.table.indexWidget(view.model.index(0, 4)) is None
    assert view.splitter.sizes()[0] < view.splitter.sizes()[1]


def test_save_at_page_boundary_resumes_without_returning_to_first_page(review):
    window, _ = review
    view = window.conflicts_view
    view.model._page_size = 2
    view.model.set_query(view.model._query.snapshot_ref)
    wait(lambda: not view.model.is_loading and view.model.rowCount() == 2)
    view.model.navigate_next()
    wait(lambda: not view.model.is_loading and view.model.pagination.first_row == 3)
    view.table.selectRow(1)
    target = view.model.index(1, 0).data(256)
    wait(lambda: view.pane.details is not None and view.pane.details.conflict == target)
    view.pane.candidate_buttons[0].click()
    view.pane.save_button.click()
    wait(
        lambda: (
            view.pane.details is not None and view.pane.details.conflict.conflict_group_id > target.conflict_group_id
        )
    )
    assert view.model.rowCount() == 1
    assert view.reset_button.isEnabled()
    view.reset_button.click()
    wait(lambda: not view.model.is_loading and view.model.pagination.total == 4)


def test_rejecting_filter_change_preserves_unsaved_choice(review, monkeypatch):
    window, _ = review
    view = window.conflicts_view
    view.pane.candidate_buttons[0].click()
    original = view.pane.details.conflict
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    view.tabs.setCurrentIndex(1)
    assert view.review_state == "pending"
    assert view.pane.details.conflict == original
    assert view.pane.has_unsaved_inputs
