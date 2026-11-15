from types import MethodType

from PyQt6.QtWidgets import QMessageBox

from tests.integration.gui import test_project_paged_workbench as workbench
from tests.plugin_fixtures import write_plugin
from transbridge.persistence.project_store.repository import ProjectStore
from transbridge.ui.coordinators.source_add_operation import ProjectSourceAddOperation
from transbridge.ui.main_window import MainWindow
from transbridge.ui.project_rows import ProjectRowsCollection

qapp = workbench.qapp


def test_add_and_remove_restore_working_source_slots_without_saving(tmp_path, qapp, monkeypatch):
    root = tmp_path / "data"
    database, variant = workbench._create_batch_project(root, tmp_path)
    new_source = write_plugin(tmp_path / "added.esp", [(0x910, "Added", "Added original")])
    store = ProjectStore(database)

    with workbench._workbench(root, qapp) as (host, coordinator, services):
        failures = workbench._begin_open(host, coordinator, database)
        workbench._wait_until(qapp, lambda: host.project_open_worker is None)
        assert not failures, failures
        context = host.context
        identity, original = context.active_version_identity, context.active_key
        saved_bytes = database.read_bytes()
        original_collection = context.collection
        assert isinstance(original_collection, ProjectRowsCollection)
        key = next(iter(original_collection)).identity
        edit = services.gui_project_commands.update_entry(key, host.runtime_context, translation="Unsaved draft")
        assert edit.is_success
        host._ctx, host._workbench, host._workers = context, host.workbench, host.workers
        host._foreground_worker = host._save_worker = host._project_open_worker = None
        host.start_foreground_task = MethodType(MainWindow.start_foreground_task, host)
        host.show_workbench = lambda: None
        retries = []

        def message(_message, *, retry=None, **_options):
            if retry is not None:
                retries.append(retry)

        host.show_message = host.workbench.show_message = message
        mutations = []
        add = host.project_commands.add_source
        remove = host.project_commands.remove_source

        def add_source(*args, **kwargs):
            mutations.append("add")
            return add(*args, **kwargs)

        def remove_source(*args, **kwargs):
            mutations.append("remove")
            return remove(*args, **kwargs)

        monkeypatch.setattr(host.project_commands, "add_source", add_source)
        monkeypatch.setattr(host.project_commands, "remove_source", remove_source)
        notify = services.project_projection._notify
        monkeypatch.setattr(services.project_projection, "_notify", lambda _snapshot: ())
        install = context.install_working_sources

        def fail_install(*args, **kwargs):
            return False if kwargs.get("view_token") is not None else install(*args, **kwargs)

        monkeypatch.setattr(context, "install_working_sources", fail_install)
        source_add = ProjectSourceAddOperation(host)
        assert source_add.start(str(new_source), identity)
        workbench._wait_until(qapp, lambda: bool(retries))
        assert context.project_view_failed
        assert not context.project_opening
        retries.pop()()
        assert not context.project_view_failed and context.active_version_identity == identity
        assert context.active_key != original
        added_key = context.active_key
        assert next(iter(context.collection)).original == "Added original"
        assert context.slots[original].collection is original_collection
        assert original_collection.get(key).translation == "Unsaved draft"
        assert context.dirty and database.read_bytes() == saved_bytes
        assert len(services.project_lifecycle.active.source_additions) == 1
        assert mutations == ["add"]

        monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
        host.workbench._on_remove_slot()
        (retry,) = retries
        assert context.project_view_failed
        retry()
        assert not context.project_view_failed and context.active_version_identity == identity
        assert added_key not in context.slots
        assert len(context.slots) == 2 and context.slots[original].collection is original_collection
        assert original_collection.get(key).translation == "Unsaved draft"
        assert context.dirty and database.read_bytes() == saved_bytes
        assert mutations == ["add", "remove"]

        monkeypatch.setattr(services.project_projection, "_notify", notify)
        assert services.gui_project_commands.save(host.runtime_context).is_success
        assert store.read_entries(variant, (key,))[0].state.translation == "Unsaved draft"
        assert len(store.sources()) == 2
