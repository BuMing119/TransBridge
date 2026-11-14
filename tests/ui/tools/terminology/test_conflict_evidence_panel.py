"""Selected conflict reads remain bounded, cancellable and safe against stale responses."""

from dataclasses import replace
from threading import Event
from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication

from tests.contracts.terminology.test_repository_contract import _build, _draft
from tests.ui.tools.terminology.test_workflow_routes import _wait
from transbridge.application.contracts import RequestContext
from transbridge.application.terminology.in_memory import InMemoryTerminologyRepository
from transbridge.application.terminology.models import BilingualEvidence, ConflictGroup, ConflictVariant
from transbridge.ui.tools.terminology.presenter import TerminologyPresenter, TerminologyUiServices
from transbridge.ui.tools.terminology.window import TerminologyWindow

_APP = QApplication.instance() or QApplication([])


def make_build(name="one", *, tied=False):
    evidence = tuple(
        BilingualEvidence(
            f"e{i}",
            "project-1",
            "variant-1",
            ("source",),
            "plugin",
            f"record-{i}",
            f"Full original {i}",
            f"完整译文 {i}",
            "plugin.sse",
            "fingerprint",
            plugin_scope="Example.esp",
        )
        for i in range(3)
    )
    conflict = ConflictGroup(
        "conflict",
        "project-1",
        "variant-1",
        "dragon",
        (
            ConflictVariant("巨龙", ("a",), ("e0",) if tied else ("e0", "e1")),
            ConflictVariant("飞龙", ("b",), ("e2",)),
        ),
    )
    return replace(_build(name), evidence=evidence, conflicts=(conflict,))


def review_details(build, conflict):
    return SimpleNamespace(
        conflict=conflict,
        evidence=build.evidence,
        decisions=(),
        plugin_ids=("Example.esp",),
        build_ref=build.ref,
        draft_ref=_draft().ref,
    )


def review_window(repo, read, *, show=True):
    window = TerminologyWindow(
        TerminologyPresenter(
            TerminologyUiServices(queries=repo, commands=SimpleNamespace(conflict_details=read)),
            RequestContext("test", project_id="project-1", variant_id="variant-1"),
        )
    )
    window._preflight_started = True
    if show:
        window.terms_view.show_attention()
    return window


def _close(window):
    window.conflicts_view.pane.saved()
    window.close()
    window._actions._pool.waitForDone(3000)


def test_counts_candidate_examples_and_new_snapshot():
    repo = InMemoryTerminologyRepository()
    build = make_build()
    repo.put_build(build)
    window = review_window(repo, lambda group, context, **kwargs: review_details(build, group))
    try:
        window.bind_page("conflicts", build.ref)
        pane = window.conflicts_view.pane
        _wait(lambda: pane.details is not None)
        assert [button.text() for button in pane.candidate_buttons] == ["巨龙  ·  2 次", "飞龙  ·  1 次"]
        assert not pane.save_button.isEnabled()
        pane.candidate_buttons[1].click()
        assert pane.original_text.toPlainText() == "Full original 2"
        assert "Example.esp" in pane.source_label.text()
        pane.candidate_buttons[0].click()
        assert pane.original_text.toPlainText() == "Full original 0"
        pane.next_example.click()
        assert pane.original_text.toPlainText() == "Full original 1"
        pane.saved()
        build = make_build("two", tied=True)
        repo.put_build(build)
        window.bind_page("conflicts", build.ref)
        _wait(lambda: pane.details is not None and pane.details.build_ref == build.ref)
        assert [button.text() for button in pane.candidate_buttons] == ["巨龙  ·  1 次", "飞龙  ·  1 次"]
        assert not pane.has_unsaved_inputs
    finally:
        _close(window)


def test_failed_detail_read_is_visible_and_refresh_retries():
    repo = InMemoryTerminologyRepository()
    build = make_build()
    repo.put_build(build)

    def fail(group, context, **kwargs):
        raise ValueError("证据不可用")

    window = review_window(repo, fail)
    try:
        window.bind_page("conflicts", build.ref)
        pane = window.conflicts_view.pane
        _wait(lambda: "证据不可用" in pane.error.text())
        assert not pane.save_button.isEnabled()
        window.presenter.services.commands.conflict_details = lambda group, context, **kwargs: review_details(
            build, group
        )
        window.conflicts_model.refresh()
        _wait(lambda: pane.details is not None)
        assert not pane.error.text()
    finally:
        _close(window)


def test_conflict_window_fits_screen_with_long_judgment():
    repo = InMemoryTerminologyRepository()
    build = make_build()
    build = replace(build, conflicts=(replace(build.conflicts[0], notes="判断说明很长。" * 1000),))
    repo.put_build(build)
    window = review_window(repo, lambda group, context, **kwargs: review_details(build, group))
    try:
        window.bind_page("conflicts", build.ref)
        _wait(lambda: window.conflicts_view.pane.details is not None)
        dialog = window.terms_view.conflict_dialog
        dialog.resize(4000, 3000)
        dialog.open_task()
        _APP.processEvents()
        available = dialog.screen().availableGeometry()
        assert available.contains(dialog.frameGeometry())
        window.conflicts_view.pane.judgment_toggle.click()
        _APP.processEvents()
        assert available.contains(dialog.frameGeometry())
        dialog.close()
        dialog.open_task()
        _APP.processEvents()
        assert available.contains(dialog.frameGeometry())
    finally:
        _close(window)


def test_only_selected_group_is_read_and_example_paging_needs_no_more_io():
    repo = InMemoryTerminologyRepository()
    build = make_build()
    extra = replace(build.conflicts[0], conflict_group_id="other", normalized_original="other")
    build = replace(build, conflicts=(*build.conflicts, extra))
    repo.put_build(build)
    reads = []

    def read(group, context, *, cancellation):
        cancellation.raise_if_cancelled()
        reads.append(group.conflict_group_id)
        return review_details(build, group)

    window = review_window(repo, read, show=False)
    try:
        window.bind_page("conflicts", build.ref)
        pane = window.conflicts_view.pane
        _wait(lambda: window.conflicts_model.rowCount() == 2)
        assert reads == []
        window.terms_view.show_attention()
        _wait(lambda: pane.details is not None)
        assert reads == ["conflict"]
        pane.candidate_buttons[0].click()
        pane.next_example.click()
        assert reads == ["conflict"]
        pane.saved()
        window.conflicts_view.table.selectRow(1)
        _wait(lambda: pane.details is not None and pane.details.conflict == extra)
        assert reads == ["conflict", "other"]
    finally:
        _close(window)


def test_late_evidence_response_cannot_replace_new_selection():
    repo = InMemoryTerminologyRepository()
    build = make_build()
    extra = replace(build.conflicts[0], conflict_group_id="other", normalized_original="other")
    build = replace(build, conflicts=(*build.conflicts, extra))
    repo.put_build(build)
    started, release = Event(), Event()

    def read(group, context, *, cancellation):
        if group.conflict_group_id == "conflict":
            started.set()
            assert release.wait(5)
        return review_details(build, group)

    window = review_window(repo, read)
    try:
        window.bind_page("conflicts", build.ref)
        _wait(started.is_set)
        window.conflicts_view.table.selectRow(1)
        pane = window.conflicts_view.pane
        _wait(lambda: pane.details is not None and pane.details.conflict == extra)
        release.set()
        window._conflict_review._runner._pool.waitForDone(3000)
        _APP.processEvents()
        assert pane.title.text() == "other"
        assert pane.details.conflict == extra
    finally:
        release.set()
        _close(window)


def test_close_cancels_in_progress_selected_detail_read():
    started, stopped = Event(), Event()
    repo = InMemoryTerminologyRepository()
    build = make_build()
    repo.put_build(build)

    def read(group, context, *, cancellation):
        started.set()
        try:
            for _ in range(500):
                cancellation.raise_if_cancelled()
                stopped.wait(0.01)
        finally:
            stopped.set()
        return review_details(build, group)

    window = review_window(repo, read)
    try:
        window.bind_page("conflicts", build.ref)
        _wait(started.is_set)
        window.close()
        assert stopped.wait(0.75)
    finally:
        stopped.set()
        _close(window)


def test_sqlite_review_uses_indexed_reads_without_loading_complete_build(tmp_path):
    from transbridge.persistence.terminology import SqliteTerminologyRepository

    repo = SqliteTerminologyRepository.open(tmp_path, "project-1")
    build = make_build()
    repo.put_build(build)
    repo.create_draft(_draft())

    def reject_full_build(*_args):
        raise AssertionError("UI must not decode the complete build")

    repo.get_build = reject_full_build
    repo.latest_build = reject_full_build
    window = review_window(
        repo, lambda group, context, **kwargs: repo.conflict_reads.details(group, "project-1", "variant-1", **kwargs)
    )
    try:
        window.bind_page("conflicts", build.ref)
        pane = window.conflicts_view.pane
        _wait(lambda: pane.details is not None)
        assert pane.candidate_buttons[0].text() == "巨龙  ·  2 次"
        pane.candidate_buttons[1].click()
        assert pane.original_text.toPlainText() == "Full original 2"
    finally:
        _close(window)
        repo.close()
