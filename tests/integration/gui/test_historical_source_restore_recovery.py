from contextlib import contextmanager
from types import MethodType

from PyQt6.QtWidgets import QApplication, QInputDialog, QMessageBox
import pytest

from tests.integration.gui import test_project_paged_workbench as workbench
from tests.plugin_fixtures import write_plugin
from transbridge.persistence.project_store import historical_restore
from transbridge.persistence.project_store.repository import ProjectStore
from transbridge.ui.coordinators.project_transfer_coordinator import ProjectTransferCoordinator
from transbridge.ui.main_window import MainWindow

_APP: QApplication | None = None


@pytest.fixture(scope="module")
def qapp():
    global _APP
    with pytest.MonkeyPatch.context() as patch:
        _APP = workbench._application(patch)
        yield _APP


@pytest.mark.parametrize("lose_commit_reply", [False, True], ids=["committed", "lost-commit-reply"])
def test_cross_source_restore_refresh_retry_keeps_late_draft_without_another_backup(
    qapp, tmp_path, monkeypatch, lose_commit_reply
):
    root = tmp_path / "data"
    database, variant = workbench._create_batch_project(root, tmp_path)
    store = ProjectStore(database)
    replacement = write_plugin(tmp_path / "replacement.esp", [(0x800, "Item000", "Replacement original")])
    with workbench._workbench(root, qapp) as (host, project_coordinator, services):
        failures = workbench._begin_open(host, project_coordinator, database)
        workbench._wait_until(qapp, lambda: host.project_open_worker is None)
        assert not failures, failures
        view, request = host.context, host.runtime_context
        updates = services.project_source_updates
        prepared = updates.prepare(database, view.active_key, str(replacement), request)
        assert prepared.is_success, prepared.diagnostics
        updated = updates.commit(prepared.value.token, request)
        assert updated.is_success, updated.diagnostics
        failures = workbench._begin_open(host, project_coordinator, database)
        workbench._wait_until(qapp, lambda: host.project_open_worker is None)
        assert not failures, failures
        assert next(iter(view.collection)).original == "Replacement original"
        original_collection, active_identity = view.collection, view.active_version_identity
        previous_version = store.read_token(variant)

        host.project_coordinator = project_coordinator
        host._ctx, host._workbench, host._workers = view, host.workbench, host.workers
        host._foreground_worker = host._save_worker = host._project_open_worker = None
        host.start_foreground_task = MethodType(MainWindow.start_foreground_task, host)
        retries = []
        host.show_message = lambda _message, *, retry=None, **_options: retries.append(retry) if retry else None
        snapshots = services.project_snapshots
        host.app_runtime.use_cases.resolve = {
            "project_snapshots": snapshots,
            "project_projection": services.project_projection,
        }.__getitem__
        backups_before = snapshots.list(request)
        assert len(backups_before) == 1

        def fail_cache(*_args, **_kwargs):
            raise OSError("baseline registry unavailable after commit")

        monkeypatch.setattr(services.baselines, "replace_many", fail_cache)
        notify = services.project_projection._notify
        monkeypatch.setattr(services.project_projection, "_notify", lambda _snapshot: ())
        if lose_commit_reply:
            connect = historical_restore.project_connection

            @contextmanager
            def lose_reply(path, *, write=False):
                with connect(path, write=write) as connection:
                    yield connection
                if write:
                    raise OSError("lost SQLite commit reply")

            monkeypatch.setattr(historical_restore, "project_connection", lose_reply)

        restores = []
        restore = snapshots.restore

        def edit_after_restore(identity, context, **kwargs):
            result = restore(identity, context, **kwargs)
            assert result.is_success, result.diagnostics
            original = next(row for row in store.read_page(variant) if row.snapshot.original == "Original 000")
            key = original.state.entry_key
            edit = services.gui_project_commands.update_entry(key, context, translation="Draft after restore", stage=1)
            assert edit.is_success, edit.diagnostics
            restores.append((result, key))
            return result

        monkeypatch.setattr(snapshots, "restore", edit_after_restore)
        prepare = services.current_project_opener.prepare_identity

        def fail_first_refresh(*args, **kwargs):
            if not retries:
                raise OSError("first refresh failed")
            return prepare(*args, **kwargs)

        monkeypatch.setattr(services.current_project_opener, "prepare_identity", fail_first_refresh)
        monkeypatch.setattr(QInputDialog, "getItem", lambda _parent, _title, _label, items, *_args: (items[0], True))
        monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes)
        monkeypatch.setattr(QMessageBox, "warning", lambda *args: pytest.fail(str(args[2])))
        ProjectTransferCoordinator(host).load_snapshot()

        workbench._wait_until(qapp, lambda: bool(retries))
        assert view.project_view_failed and view.collection is original_collection
        bytes_before_retry = database.read_bytes()
        (retry,) = retries
        retry()
        workbench._wait_until(qapp, lambda: view.collection is not original_collection and not view.project_opening)

        ((result, key),) = restores
        assert any(item.code == "SOURCE_UPDATE_BASELINES_RELOAD" for item in result.diagnostics)
        committed = result.value["source_read_token"]
        assert committed == store.read_token(variant) == view.collection.token
        assert committed.project_revision == previous_version.project_revision + 1
        assert view.active_version_identity == active_identity and not view.project_view_failed
        assert view.collection.get(key).original == "Original 000"
        assert view.collection.get(key).translation == "Draft after restore" and view.dirty
        active = services.project_lifecycle.active
        rebuilt = services.baselines.provide(active.project, active.formal_variant_ref, request)
        assert {item.fingerprint for item in rebuilt} == set(active.variant.snapshot().source_fingerprints)
        assert not store.read_entries(variant, (key,))[0].state.translation
        assert database.read_bytes() == bytes_before_retry
        backups = snapshots.list(request)
        assert len(backups) == len(backups_before) + 1
        monkeypatch.setattr(services.project_projection, "_notify", notify)
        assert services.gui_project_commands.save(request).is_success
        assert not view.dirty
        assert store.read_entries(variant, (key,))[0].state.translation == "Draft after restore"
        assert snapshots.list(request) == backups
