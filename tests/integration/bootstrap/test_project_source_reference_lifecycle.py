from pathlib import Path

from tests.integration.bootstrap.test_project_storage_composition import _services
from tests.plugin_fixtures import write_plugin
from transbridge.application.contracts import RequestContext
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.persistence.project_store.repository import ProjectStore


def test_relocated_reference_survives_live_save_and_reopen_without_source_access(tmp_path, monkeypatch):
    root = tmp_path / "data"
    original = write_plugin(tmp_path / "Original.esp", [(0x800, "Greeter", "Welcome")])
    relocated = tmp_path / "moved" / original.name
    context = RequestContext("gui", run_id="source-reference-lifecycle")
    services = _services(root)
    try:
        preview = services.gui_project_commands.prepare_create(
            ProjectProvisioningRequest("Reference test", source=ProjectSourceRequest(str(original))), context
        )
        assert preview.is_success, preview.diagnostics
        created = services.gui_project_commands.commit_create(preview.value["token"], context)
        assert created.is_success, created.diagnostics
        active = services.project_lifecycle.active
        store = services.projects.store_for(active.project_ref.identity)
        (info,) = store.sources(native_fields=False)
        token = store.read_token(active.formal_variant_ref.identity)
        source_id = active.project.envelope.data["sources"][0]["source_id"]
        assert services.projects.update_source_reference(
            token.project_id,
            source_id,
            info.namespace,
            expected_fingerprint=info.fingerprint,
            expected_path=str(original),
            reference_path=str(relocated),
            token=token,
        )
        original.unlink()
        original_stat, original_open = Path.stat, Path.open

        def deny_source_stat(path, *args, **kwargs):
            assert path not in (original, relocated), "ordinary project use inspected a plugin reference"
            return original_stat(path, *args, **kwargs)

        def deny_source_open(path, *args, **kwargs):
            assert path not in (original, relocated), "ordinary project use read a plugin reference"
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", deny_source_stat)
        monkeypatch.setattr(Path, "open", deny_source_open)
        (entry,) = active.variant.snapshot().entries
        edited = services.gui_project_commands.update_entry(entry.entry_key, context, translation="欢迎")
        assert edited.is_success, edited.diagnostics
        saved = services.gui_project_commands.save(context)
        assert saved.is_success, saved.diagnostics
        assert store.sources(native_fields=False)[0].reference_path == str(relocated)
        path = store.path
    finally:
        services.close()

    reopened = _services(root)
    try:
        prepared = reopened.current_project_opener.prepare_active(context)
        assert prepared.is_success, prepared.diagnostics
        assert reopened.current_project_opener.activate(prepared.value, context).is_success
        store = ProjectStore(path)
        assert store.sources(native_fields=False)[0].reference_path == str(relocated)
        assert store.read_page(token.variant_id)[0].state.translation == "欢迎"
    finally:
        reopened.close()
