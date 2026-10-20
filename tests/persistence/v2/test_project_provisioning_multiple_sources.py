from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.plugin_fixtures import write_plugin
from transbridge.application.contracts import RequestContext
from transbridge.application.io import Stage, TranslationIoUseCase
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.application.projects.source_content import folded_plugin_pairs
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.persistence.project_provisioning import TranslationIoProjectSourcePreparer
from transbridge.persistence.v2 import ProjectId, ProjectRef, VariantId, VariantRef

from .fakes import MemoryFilesystem


class _Ids:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"multiple-{self.value}"


class _CountingIo:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.io = TranslationIoUseCase()

    def parse(self, request):
        self.calls.append(request.source.uri)
        return self.io.parse(request)


def _context() -> RequestContext:
    return RequestContext("gui", run_id="multiple-plugins")


def _request(paths: tuple[Path, ...], *, name: str = "多个插件") -> ProjectProvisioningRequest:
    return ProjectProvisioningRequest(
        name,
        source=ProjectSourceRequest(str(paths[0])),
        additional_sources=tuple(ProjectSourceRequest(str(path)) for path in paths[1:]),
    )


def _services(root: Path, **kwargs):
    return build_persistence_v2_services(
        root,
        id_factory=_Ids(),
        timestamp_factory=lambda: "2026-10-20T00:00:00+08:00",
        **kwargs,
    )


def test_real_plugins_create_one_project_and_reopen_with_all_untranslated_entries(tmp_path: Path) -> None:
    plugins = tuple(
        write_plugin(tmp_path / f"Plugin{index}.{suffix}", [(0x800, f"NPC{index}", f"Original {index}")])
        for index, suffix in enumerate(("esp", "esm", "esl"))
    )
    originals = {path: path.read_bytes() for path in plugins}
    io = _CountingIo()
    root = tmp_path / "data"
    services = _services(root, source_preparer=TranslationIoProjectSourcePreparer(io))
    try:
        prepared = services.gui_project_commands.prepare_create(_request(plugins), _context())
        assert prepared.is_success and prepared.value is not None, prepared.diagnostics
        assert prepared.value["source_count"] == prepared.value["entry_count"] == 3
        assert services.project_lifecycle.active is None
        assert not (root / "project-catalog.json").exists()

        committed = services.gui_project_commands.commit_create(str(prepared.value["token"]), _context())
        assert committed.is_success, committed.diagnostics
        hydration = services.gui_project_commands.consume_create_hydration(
            str(prepared.value["project_id"]), _context()
        )
        assert hydration.is_success and hydration.value is not None
        assert [source.location for source in hydration.value.sources] == [str(path.resolve()) for path in plugins]
        assert [source.entries[0].original for source in hydration.value.sources] == [
            f"Original {index}" for index in range(3)
        ]
        assert io.calls == [str(path.resolve()) for path in plugins]
        snapshot = services.project_lifecycle.active.variant.snapshot()
        assert len(snapshot.entries) == len(snapshot.source_fingerprints) == 3
        assert all(entry.translation == "" and entry.stage is Stage.UNTRANSLATED for entry in snapshot.entries)
        assert services.gui_project_commands.save(_context()).is_success
        catalog = json.loads((root / "project-catalog.json").read_text(encoding="utf-8"))
        assert list(catalog["projects"]) == [prepared.value["project_id"]]
    finally:
        services.close()

    restarted = _services(root)
    try:
        restored = restarted.current_project_opener.prepare_active(_context())
        assert restored.is_success and restored.value is not None, restored.diagnostics
        assert restarted.current_project_opener.activate(restored.value, _context()).is_success
        active = restarted.project_lifecycle.active
        assert active.project_ref.identity.value == prepared.value["project_id"]
        assert {source["location"] for source in active.project.envelope.data["sources"]} == {
            str(path.resolve()) for path in plugins
        }
        assert active.project.envelope.data["source_relations"] == []
        assert active.variant.snapshot().entries == snapshot.entries
        assert active.variant.snapshot().source_fingerprints == snapshot.source_fingerprints
        assert all(path.read_bytes() == originals[path] for path in plugins)
    finally:
        restarted.close()


def test_second_invalid_plugin_leaves_existing_project_and_disk_unchanged(tmp_path: Path) -> None:
    first = write_plugin(tmp_path / "First.esp", [(0x800, "FirstNPC", "First original")])
    invalid = tmp_path / "Broken.esp"
    invalid.write_bytes(b"not a plugin")
    root = tmp_path / "data"
    services = _services(root)
    try:
        existing = services.gui_project_commands.prepare_create(ProjectProvisioningRequest("已有工程"), _context())
        assert existing.value is not None
        assert services.gui_project_commands.commit_create(str(existing.value["token"]), _context()).is_success
        previous = services.project_lifecycle.active
        persisted_before = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*.json")}

        failed = services.gui_project_commands.prepare_create(_request((first, invalid)), _context())

        assert not failed.is_success
        assert str(invalid) in failed.diagnostics[0].message
        assert dict(failed.diagnostics[0].details)["source_location"] == str(invalid)
        assert services.project_lifecycle.active is previous
        assert services.project_lifecycle.generation == 1
        assert {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*.json")} == persisted_before
        assert first.exists() and invalid.read_bytes() == b"not a plugin"
    finally:
        services.close()


@pytest.mark.parametrize("fault_document", ["variant", "project-catalog.json", "active-project.json"])
def test_multiple_plugin_transaction_failure_leaves_no_partial_project(tmp_path: Path, fault_document: str) -> None:
    plugins = tuple(
        write_plugin(tmp_path / f"Plugin{index}.esp", [(0x800, f"NPC{index}", f"Original {index}")])
        for index in range(2)
    )
    filesystem = MemoryFilesystem()
    root = tmp_path / "memory-data"
    services = _services(root, filesystem=filesystem)
    try:
        prepared = services.gui_project_commands.prepare_create(_request(plugins), _context())
        assert prepared.is_success and prepared.value is not None
        project_ref = ProjectRef(ProjectId(str(prepared.value["project_id"])))
        variant_ref = VariantRef(VariantId(str(prepared.value["variant_id"])), project_ref.identity)
        target = services.variants.path_for(variant_ref) if fault_document == "variant" else str(root / fault_document)
        filesystem.fail_replace_destinations.add(target)

        committed = services.gui_project_commands.commit_create(str(prepared.value["token"]), _context())

        assert committed.diagnostics[0].code == "PROJECT_PROVISIONING_COMMIT_FAILED"
        assert services.project_lifecycle.active is None
        assert services.project_lifecycle.generation == 0
        assert services.projects.path_for(project_ref) not in filesystem.files
        assert services.variants.path_for(variant_ref) not in filesystem.files
        assert str(root / "project-catalog.json") not in filesystem.files
        assert str(root / "active-project.json") not in filesystem.files
        assert not services.gui_project_commands.consume_create_hydration(
            str(prepared.value["project_id"]), _context()
        ).is_success
    finally:
        services.close()


def test_multiple_original_plugins_keep_their_own_translation_migration_pairs(tmp_path: Path) -> None:
    originals = tmp_path / "originals"
    translated = tmp_path / "translated"
    originals.mkdir()
    translated.mkdir()
    plugins = tuple(
        write_plugin(originals / f"Plugin{index}.esp", [(0x800, f"NPC{index}", f"Original {index}")])
        for index in range(2)
    )
    migrations = tuple(
        write_plugin(translated / plugin.name, [(0x800, f"NPC{index}", f"Translation {index}")])
        for index, plugin in enumerate(plugins)
    )
    request = ProjectProvisioningRequest(
        "两个迁移插件",
        source=ProjectSourceRequest(str(plugins[0])),
        additional_sources=(ProjectSourceRequest(str(plugins[1])),),
        migration_sources=tuple(ProjectSourceRequest(str(path)) for path in migrations),
    )
    services = _services(tmp_path / "data")
    try:
        prepared = services.gui_project_commands.prepare_create(request, _context())
        assert prepared.is_success and prepared.value is not None, prepared.diagnostics
        assert prepared.value["source_count"] == 4
        assert prepared.value["entry_count"] == 2
        assert services.gui_project_commands.commit_create(str(prepared.value["token"]), _context()).is_success
        hydration = services.gui_project_commands.consume_create_hydration(
            str(prepared.value["project_id"]), _context()
        )
        assert hydration.is_success and hydration.value is not None
        assert [source.entries[0].translation for source in hydration.value.sources] == [
            f"Translation {index}" for index in range(2)
        ]
        data = services.project_lifecycle.active.project.envelope.data
        assert data["source_registry_diagnostics"] == []
        pairs = folded_plugin_pairs(tuple(data["sources"]), tuple(data["source_relations"]))
        assert len(pairs) == 2
        assert all(Path(pair.primary["location"]).name == Path(pair.translation["location"]).name for pair in pairs)
    finally:
        services.close()
