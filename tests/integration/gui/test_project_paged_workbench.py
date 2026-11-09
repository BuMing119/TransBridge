from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
from uuid import uuid4

from PyQt6.QtCore import (
    QCoreApplication,
    QEvent,
    QItemSelection,
    QItemSelectionModel,
    QPersistentModelIndex,
    Qt,
    QTimer,
)
from PyQt6.QtWidgets import QApplication, QMenu
import pytest

from tests.plugin_fixtures import write_plugin
from transbridge.application.contracts import RequestContext
from transbridge.application.io import ExternalEntryRef
from transbridge.application.projects import EntryStatePatch, ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.application.terminology_profiles import (
    InMemoryTerminologyProfileRepository,
    ProfileEntryOverride,
    TerminologyProfileContent,
    TerminologyProfileService,
)
from transbridge.application.translation.result_merge import ResultConflictPolicy, ResultEntryChange, ResultEntryState
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.paratranz.config_manager import ParatranzConfig
from transbridge.persistence.project_store import reads
from transbridge.persistence.project_store.paged_reads import WORKBENCH_PAGE_SIZE
from transbridge.persistence.project_store.paths import project_path
from transbridge.persistence.project_store.repository import ProjectStore
from transbridge.persistence.v2.deferred_variant_rows import DeferredVariantRows
from transbridge.ui.context import AppContext
from transbridge.ui.coordinators.project_coordinator import ProjectCoordinator
from transbridge.ui.project_labels import entry_label_key
from transbridge.ui.workbench.translation_table_columns import COL_INDEX, COL_ORIGINAL, COL_TRANSLATION
from transbridge.ui.workbench.widget import WorkbenchWidget

_CONTEXT = RequestContext("gui", run_id="paged-workbench")


def _application(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(ParatranzConfig, "create_or_load", lambda: ParatranzConfig())
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def qapp():
    with pytest.MonkeyPatch.context() as patch:
        yield _application(patch)


def _services(root):
    return build_persistence_v2_services(
        root, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "2026-11-09T00:00:00+08:00"
    )


def _wait_until(app, predicate, *, timeout=8):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        sleep(0.002)
    raise AssertionError("The workbench did not finish the requested operation")


@contextmanager
def _workbench(root, app):
    services = _services(root)
    context = AppContext(
        project_projection=services.project_projection,
        project_commands=services.gui_project_commands,
        project_catalog=services.project_catalog,
        runtime_context=_CONTEXT,
    )
    window = WorkbenchWidget(context)
    window.resize(1050, 650)
    window.show()
    host = SimpleNamespace(
        context=context,
        app_runtime=SimpleNamespace(use_cases=SimpleNamespace(resolve=lambda _name: services.project_projection)),
        current_project_opener=services.current_project_opener,
        project_commands=services.gui_project_commands,
        runtime_context=_CONTEXT,
        workbench=window,
        project_open_worker=None,
        save_worker=None,
        workers=[],
        show_message=lambda _message: None,
    )
    try:
        yield host, ProjectCoordinator(host), services
    finally:
        preview = window.preview
        preview.close()
        for worker in (
            *host.workers,
            preview._paging.worker,
            preview._paging._statistics_worker,
            preview._paging._resident_worker,
        ):
            if worker is not None:
                assert worker.wait(5000)
        app.processEvents()
        window.close()
        context.close_projection()
        services.close()
        window.deleteLater()
        context.deleteLater()
        QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
        QCoreApplication.sendPostedEvents(context, QEvent.Type.DeferredDelete)
        app.processEvents()


def _begin_open(host, coordinator, database):
    failures = []
    assert coordinator._start_current_project_open(
        lambda: coordinator._prepare("prepare_path", database, host.runtime_context),
        dirty_decision=None,
        success_verb="已打开",
        show_error_dialog=False,
        on_failure=lambda code, message: failures.append((code, message)),
    )
    return failures


def _walk_scrollbar(app, table, originals, *, backwards=False):
    model = table.model()
    assert model.rowCount() == len(originals)
    bar = table.verticalScrollBar()
    _wait_until(app, lambda: bar.maximum() > 4 * WORKBENCH_PAGE_SIZE)
    maximum = bar.maximum()
    position, end = (maximum, 0) if backwards else (0, maximum)
    visited = set()
    previous_top = None
    while True:
        bar.setValue(position)
        app.processEvents()
        top = table.rowAt(0)
        bottom = table.rowAt(table.viewport().height() - 1)
        if bottom == -1:
            bottom = model.rowCount() - 1
        visible = range(top, bottom + 1)
        # Qt can keep one partially visible preceding row at the bottom clamp.
        assert top == position or (position == maximum and top == position - 1)
        assert len(visible) > 1
        if previous_top is not None:
            assert top <= previous_top if backwards else top >= previous_top
        previous_top = top
        assert all(table.entry_at_row(row) is not None for row in visible)
        assert bar.value() == position and bar.maximum() == maximum
        assert table.rowAt(0) == top and model.rowCount() == len(originals)
        for row in visible:
            assert model.index(row, COL_INDEX).data() == str(row + 1)
            original = model.index(row, COL_ORIGINAL).data()
            assert original == originals[row]
            visited.add(row)
        if position == end:
            break
        step = len(visible) - 1
        position = max(end, position - step) if backwards else min(end, position + step)
    assert visited == set(range(len(originals)))


def test_large_workbench_scrolls_full_range_and_edits_the_same_entry_after_far_jumps(tmp_path, qapp):
    app = qapp
    root = tmp_path / "data"
    originals = tuple(f"{'Match' if index % 3 else 'Skip'} {index:04}" for index in range(1261))
    source = write_plugin(
        tmp_path / "Large.esp",
        [(0x800 + index, f"Item{index:04}", original) for index, original in enumerate(originals)],
    )
    setup = _services(root)
    try:
        created = setup.gui_project_commands.create_project(
            ProjectProvisioningRequest("Continuous scroll", source=ProjectSourceRequest(str(source))), _CONTEXT
        )
        assert created.is_success, created.diagnostics
        active = setup.project_lifecycle.active
        variant = active.formal_variant_ref.identity
        database = project_path(root, active.project_ref.identity)
    finally:
        setup.close()
    source.unlink()

    with _workbench(root, app) as (host, coordinator, services):
        failures = _begin_open(host, coordinator, database)
        _wait_until(app, lambda: host.project_open_worker is None)
        assert not failures, failures
        preview = host.workbench.preview
        table = preview._table
        model = table.model()
        assert preview.get_filtered_count() == len(originals)
        _wait_until(app, lambda: table.entry_at_row(len(originals) - 1) is not None)
        first = QPersistentModelIndex(model.index(0, COL_ORIGINAL))
        _walk_scrollbar(app, table, originals)
        target_row = len(originals) - 1
        entry = table.entry_at_row(target_row)
        assert entry.original == originals[-1]
        key = entry.identity
        table.selectRow(target_row)
        edit_index = QPersistentModelIndex(model.index(target_row, COL_TRANSLATION))
        _walk_scrollbar(app, table, originals, backwards=True)
        assert first.isValid() and first.data() == originals[0]
        assert edit_index.isValid()
        table.verticalScrollBar().setValue(650)
        table.scrollToBottom()
        assert table.verticalScrollBar().value() == table.verticalScrollBar().maximum()
        assert table.entry_at_row(target_row).identity == key
        assert table.selected_entry_ids() == (entry_label_key(entry),)
        assert model.setData(
            model.index(target_row, COL_TRANSLATION), "Edited at logical tail", Qt.ItemDataRole.EditRole
        )
        assert host.context.dirty
        saved = services.gui_project_commands.save(_CONTEXT)
        assert saved.is_success, saved.diagnostics
        table.scrollToTop()
        _wait_until(app, lambda: first.data() == originals[0])
        assert table.verticalScrollBar().value() == table.verticalScrollBar().minimum()

        span = range(10, 621)
        selected_rows = ProjectStore(database).read_page(variant, offset=span.start, limit=len(span))
        expected_selection = tuple(row.snapshot.legacy_id for row in selected_rows)
        table.selectionModel().select(
            QItemSelection(model.index(span.start, COL_ORIGINAL), model.index(span.stop - 1, COL_ORIGINAL)),
            QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows,
        )
        _wait_until(app, lambda: len(preview.selected_row_entry_ids()) == len(span))
        assert preview.selected_row_entry_ids() == expected_selection
        table.clearSelection()

        filtered = tuple(item for item in originals if item.startswith("Match"))
        preview._filters_view.search_original.setText("Match")
        _wait_until(app, lambda: preview.get_filtered_count() == len(filtered))
        header = table.horizontalHeader()
        header.sectionClicked.emit(COL_ORIGINAL)
        _wait_until(app, lambda: model.index(0, COL_ORIGINAL).data() == filtered[0])
        header.sectionClicked.emit(COL_ORIGINAL)
        descending = tuple(reversed(filtered))
        _wait_until(app, lambda: model.index(0, COL_ORIGINAL).data() == descending[0])
        _walk_scrollbar(app, table, descending)
        _walk_scrollbar(app, table, descending, backwards=True)

    reopened = ProjectStore(database)
    rows = tuple(reopened.iter_entries(variant))
    assert tuple(row.snapshot.original for row in rows) == originals
    assert [(row.state.entry_key, row.state.translation) for row in rows if row.state.translation] == [
        (key, "Edited at logical tail")
    ]


def _create_batch_project(root, tmp_path):
    first = write_plugin(
        tmp_path / "batch.esp",
        [(0x800 + index, f"Item{index:03}", f"Original {index:03}") for index in range(600)],
    )
    second = write_plugin(
        tmp_path / "unloaded.esp",
        [(0x800 + index, f"Other{index:03}", f"Other {index:03}") for index in range(300)],
    )
    setup = _services(root)
    try:
        created = setup.gui_project_commands.create_project(
            ProjectProvisioningRequest(
                "Batch project",
                source=ProjectSourceRequest(str(first)),
                additional_sources=(ProjectSourceRequest(str(second)),),
            ),
            _CONTEXT,
        )
        assert created.is_success, created.diagnostics
        active = setup.project_lifecycle.active
        variant = active.formal_variant_ref.identity
        database = project_path(root, active.project_ref.identity)
    finally:
        setup.close()
    first.unlink()
    second.unlink()
    return database, variant


def _record_decoded_keys(patch):
    decoded = []
    decode_entry, decode_state = reads.decode_entry, reads.decode_state

    def observe_entry(row):
        entry = decode_entry(row)
        decoded.append(entry.state.entry_key)
        return entry

    def observe_state(row, key):
        decoded.append(key)
        return decode_state(row, key)

    patch.setattr(reads, "decode_entry", observe_entry)
    patch.setattr(reads, "decode_state", observe_state)
    return decoded


def test_workbench_preloads_current_source_and_scrolls_without_range_reads(tmp_path, monkeypatch, qapp):
    root = tmp_path / "data"
    database, variant = _create_batch_project(root, tmp_path)
    store = ProjectStore(database)
    first, _ = store.sources()
    expected = store.read_page(variant, namespace=first.namespace, limit=600)

    with _workbench(root, qapp) as (host, coordinator, services):
        with monkeypatch.context() as patch:
            decoded = _record_decoded_keys(patch)
            failures = _begin_open(host, coordinator, database)
            _wait_until(qapp, lambda: host.project_open_worker is None)
            assert not failures, failures
            preview = host.workbench.preview
            table = preview._table
            model = table.model()
            assert model.rowCount() == len(expected)
            assert table.verticalScrollBar().value() == table.verticalScrollBar().minimum()
            _wait_until(
                qapp,
                lambda: all(table.entry_at_row(row) is not None for row in range(len(expected))),
                timeout=4,
            )
            assert {key.namespace for key in decoded} == {first.namespace}

        attempted_reads = []

        def reject_list_read(*_args, **options):
            attempted_reads.append(options)
            raise AssertionError("A fully loaded source requested another database range")

        def assert_jump(row, expected_row):
            index = model.index(row, COL_ORIGINAL)
            table.scrollTo(index)
            assert index.data() == expected_row.snapshot.original
            assert table.entry_at_row(row).identity == expected_row.state.entry_key

        with monkeypatch.context() as patch:
            patch.setattr(ProjectStore, "read_page", reject_list_read)
            patch.setattr(ProjectStore, "iter_entries", reject_list_read)
            for row in (599, 250, 0, 491, 100, 598, 1):
                assert_jump(row, expected[row])

            preview._filters_view.search_original.setText("Original 1")
            _wait_until(qapp, lambda: model.rowCount() == 100)
            table.scope_sort_requested.emit(COL_ORIGINAL, True)
            _wait_until(qapp, lambda: model.index(0, COL_ORIGINAL).data() == "Original 199")
            for row in (99, 40, 0, 98, 5):
                assert_jump(row, expected[199 - row])
            assert not attempted_reads

            target = expected[100].state.entry_key
            decoded = _record_decoded_keys(patch)
            assert model.setData(model.index(99, COL_TRANSLATION), "Retained edit", Qt.ItemDataRole.EditRole)
            assert host.context.dirty
            saved = services.gui_project_commands.save(_CONTEXT)
            assert saved.is_success, saved.diagnostics
            _wait_until(qapp, lambda: model.index(99, COL_TRANSLATION).data() == "Retained edit")
            assert not attempted_reads
            assert set(decoded) <= {target}

    (stored,) = store.read_entries(variant, (target,))
    assert stored.state.translation == "Retained edit"


@pytest.mark.parametrize("persist", [False, True], ids=["apply-then-save", "apply-and-save"])
def test_workbench_applies_results_to_cached_pages_and_filtered_rows(tmp_path, monkeypatch, qapp, persist):
    root = tmp_path / "data"
    database, variant = _create_batch_project(root, tmp_path)
    store = ProjectStore(database)
    first, second = store.sources()
    targets = tuple(
        store.read_page(variant, namespace=source.namespace, offset=offset, limit=1)[0]
        for source, offset in ((first, 0), (first, 599), (second, 0))
    )
    draft_key = store.read_page(variant, namespace=first.namespace, offset=1, limit=1)[0].state.entry_key
    changes = []
    for row in targets:
        base = ResultEntryState(row.state.entry_key, row.snapshot.original, "", 0, row.snapshot.context or "")
        changes.append(ResultEntryChange(base, replace(base, translation="Downloaded translation", stage=3)))

    with _workbench(root, qapp) as (host, coordinator, services):
        failures = _begin_open(host, coordinator, database)
        _wait_until(qapp, lambda: host.project_open_worker is None)
        assert not failures, failures
        preview = host.workbench.preview
        table = preview._table
        _wait_until(qapp, lambda: preview._paging.resident_ready)
        assert table.entry_at_row(0).translation == ""
        preview._filters_view.search_translation.setText("Downloaded translation")
        _wait_until(qapp, lambda: table.model().rowCount() == 0)

        def reject_range_read(*_args, **_kwargs):
            pytest.fail("Updating known entries must reuse the loaded source and cached pages")

        with monkeypatch.context() as patch:
            patch.setattr(ProjectStore, "read_page", reject_range_read)
            patch.setattr(ProjectStore, "iter_entries", reject_range_read)
            commands = services.gui_project_commands
            draft = commands.update_entry(draft_key, _CONTEXT, translation="Local draft", stage=2)
            assert draft.is_success, draft.diagnostics
            active = services.project_lifecycle.active
            with ThreadPoolExecutor(max_workers=1) as worker:
                result = worker.submit(
                    commands.merge_entry_states,
                    changes,
                    _CONTEXT,
                    expected_variant_ref=active.formal_variant_ref,
                    policy=ResultConflictPolicy.PREFER_INCOMING,
                    clear_external_scope=("paratranz", "project:42") if persist else None,
                    persist=persist,
                ).result(timeout=5)
            assert result.is_success, result.diagnostics
            assert len(result.value.applied) == len(targets)
            _wait_until(qapp, lambda: table.model().rowCount() == 2)
            if not persist:
                saved = commands.save(_CONTEXT)
                assert saved.is_success, saved.diagnostics
            assert all(table.entry_at_row(index).stage == 3 for index in range(2))
            for row in (targets[0], targets[-1]):
                key = row.state.entry_key
                collection = next(
                    slot.collection
                    for slot in host.context.slots.values()
                    if slot.source_namespace == key.namespace.value
                )
                assert collection.get(key).translation == "Downloaded translation"
            preview._filters_view.search_translation.clear()
            _wait_until(qapp, lambda: table.model().rowCount() == 600)
            assert table.entry_at_row(599).translation == "Downloaded translation"
            assert table.entry_at_row(1).translation == "Local draft"
            _wait_until(qapp, lambda: preview._paging.current_stage_counts(host.context.collection) is not None)
            assert preview._paging.current_stage_counts(host.context.collection)[3] == 2

    assert all(
        row.state.translation == "Downloaded translation"
        for row in store.read_entries(variant, tuple(row.state.entry_key for row in targets))
    )
    assert store.read_entries(variant, (draft_key,))[0].state.translation == "Local draft"


def test_workbench_reloads_when_a_saved_projection_is_delivered_out_of_order(tmp_path, monkeypatch, qapp):
    root = tmp_path / "data"
    database, _variant = _create_batch_project(root, tmp_path)
    with _workbench(root, qapp) as (host, coordinator, services):
        failures = _begin_open(host, coordinator, database)
        _wait_until(qapp, lambda: host.project_open_worker is None)
        assert not failures, failures
        preview = host.workbench.preview
        table = preview._table
        _wait_until(qapp, lambda: preview._paging.resident_ready)
        keys = tuple(table.entry_at_row(row).identity for row in (0, 1))
        translations = ("First saved translation", "Second saved translation")
        deliveries = []

        with monkeypatch.context() as patch, ThreadPoolExecutor(max_workers=1) as worker:
            patch.setattr(host.context, "safe_mutate", deliveries.append)
            for key, translation in zip(keys, translations, strict=True):
                result = worker.submit(
                    services.gui_project_commands.replace_entry_states,
                    {key: (translation, 3)},
                    _CONTEXT,
                    persist=True,
                ).result(timeout=5)
                assert result.is_success, result.diagnostics
            first, second = deliveries
            second()
            first()

        _wait_until(qapp, lambda: preview._paging.resident_ready)
        assert tuple(table.entry_at_row(row).translation for row in (0, 1)) == translations


def test_resident_workbench_scroll_updates_terminology_without_reinstalling_rows(tmp_path, monkeypatch, qapp):
    root = tmp_path / "data"
    database, variant = _create_batch_project(root, tmp_path)
    store = ProjectStore(database)
    first, _ = store.sources()
    keys = {
        row: store.read_page(variant, namespace=first.namespace, offset=row, limit=1)[0].state.entry_key
        for row in (0, 260, 599)
    }
    profiles = TerminologyProfileService(InMemoryTerminologyProfileRepository())
    profile = profiles.create_with_content(
        store.summary().identity.value,
        "Scroll overrides",
        TerminologyProfileContent(
            overrides=tuple(ProfileEntryOverride(key.serialize(), f"Override {row}") for row, key in keys.items())
        ),
    )
    published = profiles.publish(profile.profile_id, expected_draft_revision=0)

    with _workbench(root, qapp) as (host, coordinator, services):
        failures = _begin_open(host, coordinator, database)
        _wait_until(qapp, lambda: host.project_open_worker is None)
        assert not failures, failures
        preview = host.workbench.preview
        table = preview._table
        model = table.model()
        _wait_until(qapp, lambda: preview._paging.resident_ready)
        changed = services.gui_project_commands.replace_entry_states(
            {key: (f"Base {row}", 3) for row, key in keys.items()}, _CONTEXT
        )
        assert changed.is_success, changed.diagnostics
        assert services.gui_project_commands.save(_CONTEXT).is_success
        assert model.index(599, COL_TRANSLATION).data() == "Base 599"
        preview.set_terminology_profile(published)
        _wait_until(qapp, lambda: model.index(0, COL_TRANSLATION).data() == "Override 0")

        reads_attempted, installs, resets = [], [], []
        start_range = table.start_logical_range

        def reject_list_read(*_args, **options):
            reads_attempted.append(options)
            raise AssertionError("Terminology preview scrolling requested another database range")

        def record_install(*args, **kwargs):
            installs.append(True)
            return start_range(*args, **kwargs)

        monkeypatch.setattr(ProjectStore, "read_page", reject_list_read)
        monkeypatch.setattr(ProjectStore, "iter_entries", reject_list_read)
        monkeypatch.setattr(table, "start_logical_range", record_install)
        model.modelReset.connect(lambda: resets.append(True))
        first_index = QPersistentModelIndex(model.index(0, COL_ORIGINAL))
        for jump in (120, 370, 10, 599):
            table.scrollTo(model.index(jump, COL_ORIGINAL))
        for row in (599, 260, 0):
            original = model.index(row, COL_ORIGINAL)
            translated = model.index(row, COL_TRANSLATION)
            table.scrollTo(original)
            assert original.data() == f"Original {row:03}"
            assert table.entry_at_row(row).identity == keys[row]
            _wait_until(qapp, lambda: translated.data() == f"Override {row}")

        assert first_index.isValid() and first_index.data() == "Original 000"
        assert table.entry_at_row(599).translation == "Base 599"
        assert not host.context.dirty
        assert not (reads_attempted or installs or resets)

    (stored,) = store.read_entries(variant, (keys[599],))
    assert (stored.snapshot.original, stored.state.translation) == ("Original 599", "Base 599")


@pytest.mark.parametrize("during_load", ["save", "direct-save", "switch", "close"])
def test_workbench_preload_ignores_stale_rows(tmp_path, monkeypatch, qapp, during_load):
    root = tmp_path / "data"
    database, variant = _create_batch_project(root, tmp_path)
    store = ProjectStore(database)
    first, second = store.sources()
    target_row = 570
    (edit_target,) = store.read_page(variant, namespace=first.namespace, offset=target_row, limit=1)
    target = edit_target.state.entry_key
    captured, release = Event(), Event()
    iter_entries = ProjectStore.iter_entries

    def hold_first_result(current, *args, **options):
        yield from iter_entries(current, *args, **options)
        if options.get("namespace") == first.namespace and not captured.is_set():
            # The SQLite cursor is closed. The old rows have not been accepted by the view yet.
            captured.set()
            assert release.wait(8)

    monkeypatch.setattr(ProjectStore, "iter_entries", hold_first_result)
    decoded = _record_decoded_keys(monkeypatch)
    with _workbench(root, qapp) as (host, coordinator, services):
        try:
            failures = _begin_open(host, coordinator, database)
            _wait_until(qapp, lambda: host.project_open_worker is None)
            assert not failures, failures
            preview = host.workbench.preview
            table = preview._table
            model = table.model()
            _wait_until(qapp, captured.is_set)
            assert not preview._paging.resident_ready
            assert model.index(0, COL_ORIGINAL).data() == "Original 000"
            assert {key.namespace for key in decoded} == {first.namespace}
            if during_load == "direct-save":
                table.scrollTo(model.index(target_row, COL_ORIGINAL))
                _wait_until(qapp, lambda: model.index(target_row, COL_ORIGINAL).data() == "Original 570")

            commands = services.gui_project_commands
            if during_load == "direct-save":
                saved = commands.replace_entry_states({target: ("Saved during preload", 3)}, _CONTEXT, persist=True)
            else:
                changed = commands.update_entry(target, _CONTEXT, translation="Saved during preload", stage=3)
                assert changed.is_success, changed.diagnostics
                saved = commands.save(_CONTEXT)
            assert saved.is_success, saved.diagnostics
            assert not host.context.dirty
            if during_load == "direct-save":
                assert model.index(target_row, COL_TRANSLATION).data() == "Saved during preload"
            first_key = host.context.active_key
            second_key = next(key for key, slot in host.context.slots.items() if slot.label == "unloaded.esp")
            if during_load == "switch":
                host.context.activate_slot(second_key)
            elif during_load == "close":
                preview.close()
                updates_after_close = []
                model.modelReset.connect(lambda: updates_after_close.append("reset"))
                model.dataChanged.connect(lambda *_: updates_after_close.append("data"))
        finally:
            release.set()

        if during_load == "close":
            _wait_until(qapp, lambda: preview._paging._resident_worker is None)
            assert not preview._paging.resident_ready
            assert not updates_after_close
        else:
            if during_load == "switch":
                _wait_until(qapp, lambda: preview._paging.resident_ready)
                assert model.rowCount() == 300
                assert model.index(299, COL_ORIGINAL).data() == "Other 299"
                assert table.entry_at_row(299).identity.namespace == second.namespace
                host.context.activate_slot(first_key)

            _wait_until(qapp, lambda: preview._paging.resident_ready)
            assert model.rowCount() == 600
            assert model.index(target_row, COL_TRANSLATION).data() == "Saved during preload"
            assert table.entry_at_row(target_row).identity == target

            if during_load == "switch":
                decoded.clear()
                host.context.activate_slot(second_key)
                _wait_until(qapp, lambda: preview._paging.resident_ready)
                assert model.index(299, COL_ORIGINAL).data() == "Other 299"
                assert {key.namespace for key in decoded} == {second.namespace}

    (stored,) = store.read_entries(variant, (target,))
    assert stored.state.translation == "Saved during preload"


@pytest.mark.parametrize("batch_size", [1, 100])
def test_workbench_batch_replace_and_save_only_read_selected_keys(tmp_path, monkeypatch, qapp, batch_size):
    app = qapp
    root = tmp_path / "data"
    database, variant = _create_batch_project(root, tmp_path)

    with _workbench(root, app) as (host, coordinator, services):
        failures = _begin_open(host, coordinator, database)
        _wait_until(app, lambda: host.project_open_worker is None)
        assert not failures, failures
        preview = host.workbench.preview
        unloaded = next(slot.collection for slot in host.context.slots.values() if slot.label == "unloaded.esp")
        assert not unloaded.visible_rows()

        completed = []
        if not preview._paging.when_ready(lambda: completed.append(True)):
            _wait_until(app, lambda: bool(completed))
        before = tuple((entry.identity, entry.original) for entry in preview.filtered_entries())
        assert len(before) == 600
        selected = {key: (f"Translated {original}", 3) for key, original in before[-batch_size:]}
        last_key = before[-1][0]
        commands = services.gui_project_commands
        lifecycle = services.project_lifecycle
        active = lifecycle.active
        with monkeypatch.context() as patch:
            decoded = _record_decoded_keys(patch)
            changed = commands.replace_entry_states(
                selected,
                _CONTEXT,
                expected_variant_ref=active.formal_variant_ref,
                expected_variant_revision=active.variant.revision,
                expected_project_revision=active.project.envelope.revision,
            )
            assert changed.is_success, changed.diagnostics
            assert host.context.dirty
            staged_rows = lifecycle.active.variant.snapshot().entries
            assert isinstance(staged_rows, DeferredVariantRows)
            assert {entry.entry_key for entry in staged_rows.pending()} == selected.keys()

            edited = commands.update_entry(last_key, _CONTEXT, translation="Later manual edit", stage=5)
            assert edited.is_success, edited.diagnostics
            selected[last_key] = ("Later manual edit", 5)
            saved = commands.save(_CONTEXT)
            assert saved.is_success, saved.diagnostics
            assert not host.context.dirty
            saved_rows = lifecycle.active.variant.snapshot().entries
            assert isinstance(saved_rows, DeferredVariantRows)
            assert not saved_rows.pending()
        assert set(decoded) == selected.keys()
        assert len(decoded) <= batch_size * 2

        completed.clear()
        if not preview._paging.when_ready(lambda: completed.append(True)):
            _wait_until(app, lambda: bool(completed))
        after = preview.filtered_entries()
        assert tuple((entry.identity, entry.original) for entry in after) == before
        assert all((entry.translation, entry.stage) == selected.get(entry.identity, ("", 0)) for entry in after)

    reopened = ProjectStore(database)
    _, second_info = reopened.sources()
    assert {
        entry.state.entry_key: (entry.state.translation, entry.state.stage.value)
        for entry in reopened.read_entries(variant, selected)
    } == selected
    other = tuple(reopened.iter_entries(variant, namespace=second_info.namespace))
    assert [(row.snapshot.original, row.state.translation) for row in other] == [
        (f"Other {index:03}", "") for index in range(300)
    ]


def test_workbench_record_and_label_edits_preserve_unselected_entries(tmp_path, monkeypatch, qapp):
    app = qapp
    root = tmp_path / "data"
    database, variant = _create_batch_project(root, tmp_path)
    store = ProjectStore(database)
    first, second = store.sources()
    primary_rows = store.read_page(variant, namespace=first.namespace, offset=130, limit=2)
    target, neighbor = (row.state.entry_key for row in primary_rows)
    (unloaded,) = store.read_page(variant, namespace=second.namespace, offset=130, limit=1)
    other = unloaded.state.entry_key
    remote = ExternalEntryRef("paratranz", "project:42", 701)
    library = {"review": {"name": "Review", "color": "#ffcc00"}}

    with _workbench(root, app) as (host, coordinator, services):
        failures = _begin_open(host, coordinator, database)
        _wait_until(app, lambda: host.project_open_worker is None)
        assert not failures, failures
        commands = services.gui_project_commands
        labeled = commands.replace_labels({key: {"review"} for key in (target, neighbor, other)}, library, _CONTEXT)
        assert labeled.is_success, labeled.diagnostics
        assert commands.save(_CONTEXT).is_success

        with monkeypatch.context() as patch:
            decoded = _record_decoded_keys(patch)
            cleared = commands.replace_labels({target: set()}, library, _CONTEXT)
            assert cleared.is_success, cleared.diagnostics
            assert commands.save(_CONTEXT).is_success
        assert set(decoded) == {target}
        retained = {
            row.state.entry_key: row.state.labels for row in store.read_entries(variant, (target, neighbor, other))
        }
        assert retained == {target: (), neighbor: ("review",), other: ("review",)}
        with monkeypatch.context() as patch:
            decoded = _record_decoded_keys(patch)
            empty_patch = commands.replace_labels({}, library, _CONTEXT, only_listed=True)
            assert empty_patch.is_success, empty_patch.diagnostics
            assert not host.context.dirty
        assert not decoded

        with monkeypatch.context() as patch:
            decoded = _record_decoded_keys(patch)
            linked = commands.replace_entry_records({target: EntryStatePatch("Linked", 3, (remote,))}, _CONTEXT)
            assert linked.is_success, linked.diagnostics
            assert commands.save(_CONTEXT).is_success
        assert target in decoded and {key.namespace for key in decoded} == {first.namespace}

        conflicted = commands.replace_entry_records({neighbor: EntryStatePatch("Duplicate", 3, (remote,))}, _CONTEXT)
        assert not conflicted.is_success and conflicted.diagnostics[0].code == "EXTERNAL_REF_CONFLICT"
        assert not host.context.dirty
        with monkeypatch.context() as patch:
            decoded = _record_decoded_keys(patch)
            changed = commands.replace_entry_records({target: EntryStatePatch("Retained ref", 5, (remote,))}, _CONTEXT)
            assert changed.is_success, changed.diagnostics
            assert commands.save(_CONTEXT).is_success
        assert set(decoded) == {target}
        assert isinstance(services.project_lifecycle.active.variant.snapshot().entries, DeferredVariantRows)

        cleared_all = commands.replace_labels({}, library, _CONTEXT)
        assert cleared_all.is_success, cleared_all.diagnostics
        assert commands.save(_CONTEXT).is_success
        assert isinstance(services.project_lifecycle.active.variant.snapshot().entries, DeferredVariantRows)

    reopened = ProjectStore(database)
    rows = {row.state.entry_key: row.state for row in reopened.read_entries(variant, (target, neighbor, other))}
    assert all(not row.labels for row in rows.values())
    assert (rows[target].translation, rows[target].stage.value, rows[target].external_refs) == (
        "Retained ref",
        5,
        (remote,),
    )
    assert (rows[neighbor].translation, rows[neighbor].external_refs) == ("", ())
    assert rows[other].entry_key == unloaded.state.entry_key
    assert (rows[other].translation, rows[other].external_refs) == ("", ())


def test_workbench_context_menu_toggle_preserves_labels_across_offscreen_selected_rows(tmp_path, monkeypatch, qapp):
    root = tmp_path / "data"
    database, variant = _create_batch_project(root, tmp_path)
    store = ProjectStore(database)
    first, second = store.sources()
    selected = tuple(row.state.entry_key for row in store.read_page(variant, namespace=first.namespace, limit=521))
    (outside_row,) = store.read_page(variant, namespace=first.namespace, offset=550, limit=1)
    (other_row,) = store.read_page(variant, namespace=second.namespace, offset=130, limit=1)
    outside, other = outside_row.state.entry_key, other_row.state.entry_key
    labels = {key: {"shared"} for key in (*selected, outside, other)}
    labels[selected[0]].add("front")
    labels[selected[-1]].add("tail")
    labels[outside].add("tail")
    labels[other].add("front")
    library = {
        "shared": {"name": "Shared", "color": "#ffcc00"},
        "front": {"name": "Front", "color": "#aa5500"},
        "tail": {"name": "Tail", "color": "#008844"},
    }

    with _workbench(root, qapp) as (host, coordinator, services):
        failures = _begin_open(host, coordinator, database)
        _wait_until(qapp, lambda: host.project_open_worker is None)
        assert not failures, failures
        preview = host.workbench.preview
        table = preview._table
        model = table.model()
        commands = services.gui_project_commands
        added = commands.replace_labels(labels, library, _CONTEXT, only_listed=True)
        assert added.is_success, added.diagnostics
        assert commands.save(_CONTEXT).is_success
        _wait_until(qapp, lambda: table.entry_at_row(len(selected) - 1) is not None)
        tail = model.index(len(selected) - 1, COL_ORIGINAL)
        assert not table.viewport().rect().intersects(table.visualRect(tail))
        opened = []

        def choose_shared(menu, *_args):
            label_menu = next((action.menu() for action in menu.actions() if action.text() == "标签"), None)
            if label_menu is None:
                opened.append(None)
                return None
            shared = next((action for action in label_menu.actions() if action.text() == "● Shared"), None)
            opened.append(None if shared is None else shared.isChecked())
            if shared is not None:
                shared.trigger()
            return None

        monkeypatch.setattr(QMenu, "exec", choose_shared)
        top = model.index(0, COL_ORIGINAL)
        table.selectionModel().select(
            QItemSelection(top, tail),
            QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows,
        )
        table.customContextMenuRequested.emit(table.visualRect(top).center())
        _wait_until(qapp, lambda: bool(opened))
        assert opened == [True]
        assert host.context.dirty
        saved = commands.save(_CONTEXT)
        assert saved.is_success, saved.diagnostics

    reopened = ProjectStore(database)
    rows = reopened.read_entries(variant, (*selected, outside, other))
    actual = {row.state.entry_key: set(row.state.labels) for row in rows}
    for key in selected:
        labels[key].remove("shared")
    assert actual == labels


def test_workbench_open_filter_sort_and_save_preserve_complete_range(tmp_path, monkeypatch, qapp):
    app = qapp
    root = tmp_path / "data"
    first = write_plugin(
        tmp_path / "first.esp",
        [
            (0x800 + index, f"Item{index:03}", f"{'Needle' if index >= 95 else 'Other'} {index:03}")
            for index in range(530)
        ],
    )
    second = write_plugin(tmp_path / "second.esp", [(0x800, "OtherSource", "Other source text")])
    setup = _services(root)
    try:
        created = setup.gui_project_commands.create_project(
            ProjectProvisioningRequest(
                "Paged project",
                source=ProjectSourceRequest(str(first)),
                additional_sources=(ProjectSourceRequest(str(second)),),
            ),
            _CONTEXT,
        )
        assert created.is_success, created.diagnostics
        active = setup.project_lifecycle.active
        variant = active.formal_variant_ref.identity
        database = project_path(root, active.project_ref.identity)
        store = ProjectStore(database)
        first_info, second_info = store.sources()
        (edit_target,) = store.read_page(variant, namespace=first_info.namespace, offset=130, limit=1)
        labels = setup.gui_project_commands.replace_labels(
            {edit_target.state.entry_key: {"far"}}, {"far": {"name": "Offscreen label", "color": "#ffcc00"}}, _CONTEXT
        )
        assert labels.is_success, labels.diagnostics
        saved = setup.gui_project_commands.save(_CONTEXT)
        assert saved.is_success, saved.diagnostics
    finally:
        setup.close()
    first.unlink()
    second.unlink()

    opened, release = Event(), Event()
    release_full = Event()
    decoded = []
    decode_entry = reads.decode_entry

    def observe_first_page(row):
        if not opened.is_set():
            opened.set()
            assert release.wait(5)
        if len(decoded) == WORKBENCH_PAGE_SIZE:
            assert release_full.wait(5)
        decoded.append(row["namespace"])
        return decode_entry(row)

    with _workbench(root, app) as (host, coordinator, services):
        ticks = []
        timer = QTimer()
        timer.setInterval(2)
        timer.timeout.connect(lambda: ticks.append(monotonic()))
        timer.start()
        try:
            with monkeypatch.context() as patch:
                patch.setattr(reads, "decode_entry", observe_first_page)
                failures = _begin_open(host, coordinator, database)
                _wait_until(app, lambda: opened.is_set() or host.project_open_worker is None)
                assert opened.is_set(), failures
                _wait_until(app, lambda: len(ticks) >= 3)
                assert host.context.active_version_identity is None
                release.set()
                _wait_until(app, lambda: host.project_open_worker is None)
                assert not failures, failures
                assert decoded == [first_info.namespace.value] * WORKBENCH_PAGE_SIZE
                preview = host.workbench.preview
                table = preview._table
                assert table.model().rowCount() == 530
                assert table.entry_at_row(0).original == "Other 000"
                assert table.entry_at_row(530 - 1) is None
                release_full.set()
                _wait_until(app, lambda: preview._paging.resident_ready)
        finally:
            release.set()
            release_full.set()
            timer.stop()
        assert set(decoded) == {first_info.namespace.value}
        other_key, other_slot = next(
            (key, slot) for key, slot in host.context.slots.items() if slot.label == "second.esp"
        )
        assert preview.get_filtered_count() == 530
        assert not other_slot.collection.visible_rows()

        with monkeypatch.context() as patch:
            saved_keys = _record_decoded_keys(patch)
            changed = services.gui_project_commands.update_entry(
                edit_target.state.entry_key, _CONTEXT, translation="Saved before filtering", stage=3
            )
            assert changed.is_success, changed.diagnostics
            assert host.context.dirty
            saved = services.gui_project_commands.save(_CONTEXT)
            assert saved.is_success, saved.diagnostics
            assert not host.context.dirty
        assert set(saved_keys) <= {edit_target.state.entry_key}

        _wait_until(app, lambda: preview._paging.statistics is not None)
        assert preview._paging.statistics.summary.total == 530
        label = next(button for button in preview._filters_view._label_buttons if "Offscreen label" in button.toolTip())
        assert label.toolTip().endswith("Offscreen label 1")
        label.click()
        _wait_until(app, lambda: preview.get_filtered_count() == 1)
        assert [entry.identity for entry in preview.filtered_entries()] == [edit_target.state.entry_key]
        preview._filters_view.toggle_label(None)
        _wait_until(app, lambda: preview.get_filtered_count() == 530)

        changed = services.gui_project_commands.update_entry(
            edit_target.state.entry_key, _CONTEXT, translation="Edited offscreen", stage=3
        )
        assert changed.is_success, changed.diagnostics
        assert host.context.dirty

        preview._filters_view.search_original.setText("Needle")
        _wait_until(app, lambda: preview.get_filtered_count() == 435)
        assert table.model().rowCount() == 435
        assert preview.filtered_entries()[0].original == "Needle 095"
        table.scope_sort_requested.emit(COL_ORIGINAL, True)
        _wait_until(app, lambda: table.model().index(0, COL_ORIGINAL).data() == "Needle 529")
        filtered = preview.filtered_entries()
        assert len(filtered) == 435
        assert [entry.original for entry in filtered] == [f"Needle {index:03}" for index in reversed(range(95, 530))]
        table.scope_select_all_requested.emit()
        assert len(preview.selected_entry_ids()) == 435
        edited = next(entry for entry in filtered if entry.identity == edit_target.state.entry_key)
        assert edited.translation == "Edited offscreen"

        saved = services.gui_project_commands.save(_CONTEXT)
        assert saved.is_success, saved.diagnostics
        _wait_until(app, lambda: not host.context.dirty and preview._paging.worker is None)

        preview._filters_view.search_original.setText("Other")
        host.context.activate_slot(other_key)
        _wait_until(app, lambda: table.model().index(0, COL_ORIGINAL).data() == "Other source text")
        assert [entry.original for entry in preview.filtered_entries()] == ["Other source text"]

    (stored,) = ProjectStore(database).read_entries(variant, (edit_target.state.entry_key,))
    assert stored.state.translation == "Edited offscreen"
    assert ProjectStore(database).read_page(variant, namespace=second_info.namespace)[0].snapshot.original == (
        "Other source text"
    )
