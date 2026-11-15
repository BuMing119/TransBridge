from types import MethodType, SimpleNamespace

import pytest

from tests.integration.gui import test_project_paged_workbench as workbench
from tests.plugin_fixtures import write_plugin
from transbridge.persistence.project_store.repository import ProjectStore
from transbridge.ui.coordinators.source_update_coordinator import SourceUpdateCoordinator
from transbridge.ui.main_window import MainWindow
from transbridge.ui.project_rows import ProjectRowsCollection

qapp = workbench.qapp


@pytest.mark.parametrize(
    ("first_refresh_fails", "deliver_earlier_notification"),
    [(False, False), (False, True), (True, False)],
    ids=["recover", "queued-notification-is-stale", "retry-view-only"],
)
def test_source_replacement_recovers_after_baseline_failure_and_lost_qt_notification(
    qapp, tmp_path, monkeypatch, first_refresh_fails, deliver_earlier_notification
):
    root = tmp_path / "data"
    database, variant = workbench._create_batch_project(root, tmp_path)
    replacement = write_plugin(tmp_path / "replacement.esp", [(0x800, "Item000", "Replacement original")])
    store = ProjectStore(database)

    with workbench._workbench(root, qapp) as (host, project_coordinator, services):
        failures = workbench._begin_open(host, project_coordinator, database)
        workbench._wait_until(qapp, lambda: host.project_open_worker is None)
        assert not failures, failures
        view = host.context
        original_collection = view.collection
        active_id = view.active_version_identity
        original_source = view.active_key
        expected_base = store.read_token(variant)
        host.project_coordinator = project_coordinator
        host._ctx, host._workbench, host._workers = view, host.workbench, host.workers
        host._foreground_worker = host._save_worker = host._project_open_worker = None
        host.start_foreground_task = MethodType(MainWindow.start_foreground_task, host)
        retries = []
        host.show_message = lambda _message, *, retry=None, **_options: retries.append(retry) if retry else None
        service = services.project_source_updates
        host.app_runtime.use_cases.resolve = {
            "project_source_updates": service,
            "project_projection": services.project_projection,
        }.__getitem__
        updates = SourceUpdateCoordinator(host)
        request = updates._context(view.active_project_id)
        preview = service.prepare(database, original_source, str(replacement), request)
        assert preview.is_success, preview.diagnostics
        updates._preview_token = preview.value.token
        updates._dialog = SimpleNamespace(
            set_busy=lambda *_: None, show_pairing=lambda *_: None, accept=lambda: None, deleteLater=lambda: None
        )

        def fail_baselines(*_args, **_kwargs):
            raise OSError("baseline update failed after SQLite commit")

        monkeypatch.setattr(services.baselines, "replace_many", fail_baselines)
        notify = services.project_projection._notify

        def lose_notification(snapshot):
            return notify(snapshot) if deliver_earlier_notification and not snapshot.dirty else ()

        monkeypatch.setattr(services.project_projection, "_notify", lose_notification)
        commit = service.commit
        committed = []

        def add_unsaved_edit_after_commit(token, context):
            result = commit(token, context)
            if result.is_success:
                row = next(row for row in store.read_page(variant) if row.snapshot.original == "Replacement original")
                changed = services.gui_project_commands.update_entry(
                    row.state.entry_key, context, translation="Unsaved after replacement", stage=1
                )
                assert changed.is_success, changed.diagnostics
                committed.append((result, row.state.entry_key))
            return result

        monkeypatch.setattr(service, "commit", add_unsaved_edit_after_commit)
        prepare = host.current_project_opener.prepare_identity

        def prepare_view(*args, **kwargs):
            if first_refresh_fails and not retries:
                raise OSError("first view preparation failed")
            return prepare(*args, **kwargs)

        monkeypatch.setattr(host.current_project_opener, "prepare_identity", prepare_view)
        updates._commit(preview.value.token, request, recovery=False)
        if first_refresh_fails:
            workbench._wait_until(qapp, lambda: bool(retries))
            assert view.collection is original_collection
            assert view.project_view_failed
            assert services.project_lifecycle.active.dirty
            bytes_before_retry = database.read_bytes()
            (retry,) = retries
            retry()

        workbench._wait_until(
            qapp, lambda: host.project_open_worker is None and bool(committed) and not view.project_opening
        )
        assert view.collection is not original_collection
        ((result, edited_key),) = committed
        assert any(item.code == "SOURCE_UPDATE_BASELINES_RELOAD" for item in result.diagnostics)
        assert not view.project_view_failed
        assert view.active_version_identity == active_id and view.active_key == original_source
        assert isinstance(view.collection, ProjectRowsCollection)
        assert view.collection.token == store.read_token(variant) == result.value.active_token
        assert view.project_revision == expected_base.project_revision + 1
        assert view.collection.get(edited_key).original == "Replacement original"
        assert view.collection.get(edited_key).translation == "Unsaved after replacement"
        assert view.dirty and services.project_lifecycle.active.dirty
        assert not store.read_entries(variant, (edited_key,))[0].state.translation
        active = services.project_lifecycle.active
        restored = services.baselines.provide(active.project, active.formal_variant_ref, request)
        assert {item.fingerprint for item in restored} == set(active.variant.snapshot().source_fingerprints)
        backups = services.project_snapshots.list(request)
        assert len(backups) == 1 and result.value.backup_path.endswith(backups[0].identity.removesuffix("-0"))
        if first_refresh_fails:
            assert database.read_bytes() == bytes_before_retry
        assert not commit(preview.value.token, request).is_success
        assert services.project_snapshots.list(request) == backups
        monkeypatch.setattr(services.project_projection, "_notify", notify)
        assert services.gui_project_commands.save(request).is_success
        assert not view.dirty
        assert store.read_entries(variant, (edited_key,))[0].state.translation == "Unsaved after replacement"
        assert services.project_snapshots.list(request) == backups
