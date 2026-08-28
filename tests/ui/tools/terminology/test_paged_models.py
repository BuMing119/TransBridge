from __future__ import annotations

from PyQt6.QtWidgets import QApplication

from transbridge.application.terminology import CursorStaleError, Page, SnapshotCursor
from transbridge.ui.tools.terminology.paged_models import KeysetPagedTableModel, PagedColumn

_APP = QApplication.instance() or QApplication([])


def _cursor(snapshot: str, query: str, identity: str) -> SnapshotCursor:
    return SnapshotCursor(snapshot, query, (identity,), identity)


def test_model_discards_old_generation_and_keeps_bounded_visible_pages() -> None:
    model = KeysetPagedTableModel(
        lambda _ref, _request: Page((), "snapshot"),
        (PagedColumn("value", "值", str),),
        page_size=2,
        max_cached_pages=2,
    )
    first = model.set_query("ref-a")
    second = model.set_query("ref-b")

    assert not model.accept_page(first, Page(("old",), "snapshot"))
    assert model.accept_page(second, Page(("a", "b"), "snapshot", _cursor("snapshot", "all", "b")))
    assert model.accept_page(second, Page(("c", "d"), "snapshot", _cursor("snapshot", "all", "d")))
    assert model.accept_page(second, Page(("e",), "snapshot"))

    assert model.cached_page_count == 2
    assert model.rowCount() == 3
    assert [model.index(row, 0).data() for row in range(model.rowCount())] == ["c", "d", "e"]
    model.close()


def test_cursor_stale_clears_old_snapshot_and_requests_a_new_first_page() -> None:
    requests = []

    def load(ref, request):
        requests.append((ref, request.cursor))
        return Page((), "new-snapshot")

    model = KeysetPagedTableModel(load, (PagedColumn("value", "值", str),))
    generation = model.set_query("ref")
    model.accept_page(
        generation,
        Page(("old",), "old-snapshot", _cursor("old-snapshot", "all", "old")),
    )
    restarted = []
    model.cursor_restarted.connect(lambda: restarted.append(True))

    assert model.accept_error(generation, CursorStaleError("CURSOR_STALE"))
    assert model.generation == generation + 1
    assert model.rowCount() == 0
    assert restarted == [True]
    model.close()


def test_close_releases_query_ownership_and_rejects_late_results() -> None:
    model = KeysetPagedTableModel(lambda _ref, _request: Page((), "snapshot"), (PagedColumn("v", "值", str),))
    generation = model.set_query("ref")
    model.close()

    assert model.closed
    assert not model.accept_page(generation, Page(("late",), "snapshot"))
    assert model.rowCount() == 0
