from contextlib import closing
from dataclasses import replace
from pathlib import Path
import sys

from tests.plugin_fixtures import write_plugin
from transbridge.application.contracts import RequestContext
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.application.translation.postprocess_checkpoint import (
    FilesystemPostProcessCheckpointPort,
    PostProcessCheckpoint,
)
from transbridge.bootstrap import build_runtime
from transbridge.config.storage_location import default_data_path, default_progress_path, selected_storage_root
from transbridge.config.storage_migration import migrate_storage
from transbridge.config.ui_preferences import GuidanceMode
from transbridge.persistence.project_store.paths import project_path
from transbridge.persistence.project_store.repository import ProjectStore


def test_installed_defaults_migrate_project_preferences_and_resumable_task_together(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    mods = tmp_path / "mods"
    mods.mkdir()
    source = write_plugin(mods / "Example.esp", [(0x800, "GREETING", "Welcome")])
    original = source.read_bytes()
    context = RequestContext("gui", run_id="migration-test")
    task_id = "translation-one"
    checkpoint = PostProcessCheckpoint(task_id, "gui", "a" * 64, revision=1)
    with closing(build_runtime()) as runtime:
        resolve = runtime.use_cases.resolve
        assert resolve("ui_preferences").save_guidance_mode(GuidanceMode.GUIDED).saved
        commands = resolve("gui_project_commands")
        created = commands.create_project(
            ProjectProvisioningRequest("测试工程", source=ProjectSourceRequest(str(source))), context
        )
        assert created.is_success, created.diagnostics
        variant = resolve("project_lifecycle").active.formal_variant_ref
        (row,) = ProjectStore(project_path(default_data_path(), variant.project_id)).read_page(variant.identity)
        key = row.state.entry_key
        assert commands.update_entry(key, context, translation="欢迎", stage=3).is_success
        assert commands.save(context).is_success
        store = resolve("task_store")
        store.recovery().create({
            "task_id": task_id,
            "project_id": variant.project_id.value,
            "variant_id": variant.identity.value,
            "mode": "translate",
            "state": "running",
            "created_at": "2026-11-22T11:00:00+08:00",
            "config_digest": "test-settings",
            "sources": [],
        })
        directory = store.run_directory(task_id)
        assert directory.parent == default_progress_path()
        FilesystemPostProcessCheckpointPort(directory / "ai-translation-resume" / "postprocess").save(checkpoint)

    destination = selected_storage_root(tmp_path / "新位置")
    assert not migrate_storage(destination)

    with closing(build_runtime()) as runtime:
        resolve = runtime.use_cases.resolve
        assert Path(resolve("persistence_v2").root) == destination / "data"
        assert resolve("ui_preferences").load().guidance_mode == GuidanceMode.GUIDED
        opener = resolve("current_project_opener")
        prepared = opener.prepare_active(context)
        assert prepared.is_success, prepared.diagnostics
        assert opener.activate(prepared.value, context).is_success
        assert resolve("project_lifecycle").active.formal_variant_ref == variant
        project = ProjectStore(project_path(destination / "data", variant.project_id))
        assert project.read_page(variant.identity)[0].state.translation == "欢迎"
        commands = resolve("gui_project_commands")
        assert commands.update_entry(key, context, translation="欢迎回来", stage=3).is_success
        assert commands.save(context).is_success
        assert project.read_page(variant.identity)[0].state.translation == "欢迎回来"
        store = resolve("task_store")
        assert store.recovery().load(task_id)["state"] == "running"
        directory = store.run_directory(task_id)
        assert directory.parent == destination / "翻译进度"
        progress = FilesystemPostProcessCheckpointPort(directory / "ai-translation-resume" / "postprocess")
        assert progress.load(task_id) == checkpoint
        updated = replace(checkpoint, revision=2, completed_phases=("proofread",))
        progress.save(updated)
        assert progress.load(task_id) == updated
        store.recovery().update_metadata(task_id, state="completed")
        assert store.recovery().load(task_id)["state"] == "completed"
    assert not default_data_path().exists()
    assert not default_progress_path().exists()
    assert list(mods.iterdir()) == [source]
    assert source.read_bytes() == original
