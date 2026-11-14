"""Result review batches real SQLite reads off Qt and discards stale delivery."""

from dataclasses import replace
from threading import Event, get_ident
from types import SimpleNamespace

from PyQt6.QtCore import QTimer
import pytest

from tests.persistence.project_store.test_entries import _stored_entry
from tests.ui.tools import test_ai_partial_task as support
from transbridge.application.io import FormatId
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.projects.storage_contracts import EntryStateChange, ExtractedSource, SaveBatch
from transbridge.persistence.project_store.format import ProjectStorageConflict
from transbridge.persistence.project_store.paged_reads import ProjectPagedRead, WorkbenchSource
from transbridge.persistence.project_store.repository import ProjectStore
from transbridge.persistence.v2.ids import ProjectId, VariantId
from transbridge.ui.project_rows import ProjectRowsCollection
from transbridge.ui.projection_types import CollectionSlot
from transbridge.ui.tools.ai_translator.task_merge_review import TaskMergeReview

qapp = support.qapp


@pytest.fixture
def case(tmp_path, request):
    namespace = SourceNamespace("review")
    count = getattr(request, "param", 1000)
    entries = tuple(_stored_entry(EntryKey(namespace, str(i)), f"Original {i}", f"当前 {i}") for i in range(count))
    source = ExtractedSource(namespace, "review.json", FormatId.JSON_DSD, "a" * 64, entries)
    project_id, variant_id = ProjectId("review-project"), VariantId("main")
    database = ProjectStore(tmp_path / "project.sqlite3")
    database.create(project_id, "Review", variant_id, "Main", sources=(source,))
    token = database.read_token(variant_id)
    workbench = WorkbenchSource("source", source.info, count=len(entries))
    collection = ProjectRowsCollection(ProjectPagedRead(database, token, (workbench,)), workbench)
    ctx = SimpleNamespace(
        active_version_identity=(project_id.value, variant_id.value),
        project_revision=token.project_revision,
        variant_revision=token.variant_revision,
        slots={"source": CollectionSlot("Source", collection)},
    )
    record = {"project_id": project_id.value, "variant_id": variant_id.value}
    rows = [{"entry_key": entry.state.entry_key.to_dict()} for entry in entries]
    results = SimpleNamespace(load=lambda _: record, load_results=lambda _: {"rows": rows})
    return SimpleNamespace(
        ctx=ctx,
        database=database,
        collection=collection,
        entries=entries,
        token=token,
        results=results,
        directory=tmp_path,
    )


def block_reads(case, monkeypatch):
    entered, release = Event(), Event()
    calls = []
    gui_thread = get_ident()
    read = case.database.read_entries

    def blocked(variant, keys, *, token=None):
        assert get_ident() != gui_thread, "Database reads must never run on Qt"
        keys = tuple(keys)
        calls.append((keys, token))
        entered.set()
        if not release.wait(5):
            raise RuntimeError("test did not release the database read")
        return read(variant, keys, token=token)

    monkeypatch.setattr(case.database, "read_entries", blocked)
    return entered, release, calls


@pytest.mark.parametrize("case", [1000, 20162], indirect=True)
def test_results_use_one_batch_and_leave_qt_responsive(qapp, case, monkeypatch):
    entered, release, calls = block_reads(case, monkeypatch)
    review = TaskMergeReview(case.ctx, case.directory, "run", store=case.results)
    heartbeats = []
    timer = QTimer()
    timer.setInterval(1)
    timer.timeout.connect(lambda: heartbeats.append(1))
    try:
        review.refresh()
        support._wait(qapp, entered.is_set)
        timer.start()
        support._wait(qapp, lambda: len(heartbeats) >= 3)
        assert review.busy and not review.current
        release.set()
        support._wait(qapp, lambda: not review.busy)
        assert not review.error and len(review.current) == len(case.entries)
        assert review.current[case.entries[-1].state.entry_key].translation == case.entries[-1].state.translation
        assert len(calls) == 1 and len(calls[0][0]) == len(case.entries) and calls[0][1] == case.token
    finally:
        release.set()
        support._wait(qapp, lambda: not review.busy)
        timer.stop()
        review.close()


@pytest.mark.parametrize("change", ["identity", "revision", "collection", "close"])
def test_changes_while_reading_do_not_publish_stale_current_values(qapp, case, monkeypatch, change):
    entered, release, _ = block_reads(case, monkeypatch)
    review = TaskMergeReview(case.ctx, case.directory, "run", store=case.results)
    try:
        review.refresh()
        support._wait(qapp, entered.is_set)
        if change == "identity":
            case.ctx.active_version_identity = ("other", "main")
        elif change == "revision":
            case.ctx.variant_revision += 1
        elif change == "collection":
            from copy import copy

            case.ctx.slots["source"].collection = copy(case.collection)
        else:
            review.close()
        release.set()
        support._wait(qapp, lambda: not review.busy)
        assert not review.current
        if change == "close":
            assert not review.rows
        else:
            assert review.rows and review.error
            review.resolve({case.entries[0].state.entry_key}, keep=True)
            assert not review.busy
    finally:
        release.set()
        support._wait(qapp, lambda: not review.busy)
        review.close()


def test_frozen_batch_preserves_overlay_deletion_and_full_identity(case):
    first, second, third = case.entries[:3]
    case.collection._states = {
        first.state.entry_key: replace(first.state, translation="未保存修改"),
        second.state.entry_key: replace(second.state, tombstone=True),
    }
    read = case.collection.freeze_entries()
    case.collection._states.clear()
    unknown_source = EntryKey(SourceNamespace("other"), first.state.entry_key.local_key)
    rows = read((first.state.entry_key, second.state.entry_key, third.state.entry_key, unknown_source))
    assert len(rows) == 1
    assert rows[0].identity == first.state.entry_key and rows[0].translation == "未保存修改"


def test_frozen_database_token_rejects_later_commit(case):
    read = case.collection.freeze_entries()
    key = case.entries[0].state.entry_key
    token = case.token
    case.database.commit(
        SaveBatch(
            token.project_id,
            token.variant_id,
            token.project_revision,
            token.variant_revision,
            (EntryStateChange(key, (("translation", "later"),)),),
        )
    )
    with pytest.raises(ProjectStorageConflict):
        read((key,))
