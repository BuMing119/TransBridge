from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from transbridge.application.contracts import DomainError, RequestContext
from transbridge.application.io import (
    FormatId,
    ParseResult,
    SourceSnapshot,
    Stage,
)
from transbridge.application.io.identity import EntryKey, EntryRevision, SourceNamespace
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.application.projects.source_content import authoritative_baseline_sources
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.persistence.current_project import CurrentProjectOpener
from transbridge.persistence.project_provisioning import TranslationIoProjectSourcePreparer
from transbridge.persistence.v2 import ProjectId, ProjectRef, VariantId, VariantRef

from .fakes import MemoryFilesystem


class _Ids:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"id-{self.value}"


def _context() -> RequestContext:
    return RequestContext("gui", run_id="provision")


def test_bootstrap_provisions_empty_project_variant_catalog_pointer_and_projection(tmp_path: Path) -> None:
    ids = _Ids()
    services = build_persistence_v2_services(
        tmp_path,
        id_factory=ids,
        timestamp_factory=lambda: "2026-08-24T00:00:00+08:00",
    )

    prepared = services.gui_project_commands.prepare_create(
        ProjectProvisioningRequest("空工程"),
        _context(),
    )
    assert prepared.is_success and prepared.value is not None
    project_ref = ProjectRef(ProjectId(str(prepared.value["project_id"])))
    variant_ref = VariantRef(VariantId(str(prepared.value["variant_id"])), project_ref.identity)
    assert not Path(services.projects.path_for(project_ref)).exists()
    assert not Path(services.variants.path_for(variant_ref)).exists()

    committed = services.gui_project_commands.commit_create(
        str(prepared.value["token"]),
        _context(),
        request_fingerprint=str(prepared.value["request_fingerprint"]),
    )

    assert committed.is_success
    assert Path(services.projects.path_for(project_ref)).exists()
    assert Path(services.variants.path_for(variant_ref)).exists()
    pointer = json.loads((tmp_path / "active-project.json").read_text(encoding="utf-8"))
    catalog = json.loads((tmp_path / "project-catalog.json").read_text(encoding="utf-8"))
    assert pointer["project_id"] == project_ref.identity.value
    assert pointer["variant_id"] == variant_ref.identity.value
    assert catalog["projects"][project_ref.identity.value]["name"] == "空工程"
    assert (
        services.baselines.provide(
            services.project_lifecycle.active.project,
            variant_ref,
            _context(),
        )
        == ()
    )
    projection = services.project_projection.snapshot()
    assert projection is not None
    assert projection.to_dict()["values"]["project_name"] == "空工程"
    assert services.gui_project_commands.create_variant(
        "空白分支",
        RequestContext("gui", run_id="variant"),
    ).is_success

    restarted = build_persistence_v2_services(
        tmp_path,
        id_factory=_Ids(),
        timestamp_factory=lambda: "2026-08-24T00:01:00+08:00",
    )
    restored = restarted.current_project_opener.prepare_active(
        RequestContext("gui", run_id="restore"),
    )
    assert restored.is_success
    assert restored.value is not None and restored.value.baselines == ()
    assert restarted.current_project_opener.activate(
        restored.value,
        RequestContext("gui", run_id="restore"),
    ).is_success
    duplicate = restarted.gui_project_commands.prepare_create(
        ProjectProvisioningRequest("空工程"),
        _context(),
    )
    assert duplicate.diagnostics[0].code == "PROJECT_NAME_EXISTS"


def test_variant_publication_fault_removes_staged_project_and_keeps_pointer_absent() -> None:
    root = os.path.abspath(os.path.join(os.sep, "transbridge-fr26-provisioning"))
    filesystem = MemoryFilesystem()
    services = build_persistence_v2_services(
        root,
        id_factory=_Ids(),
        timestamp_factory=lambda: "2026-08-24T00:00:00+08:00",
        filesystem=filesystem,
    )
    prepared = services.gui_project_commands.prepare_create(
        ProjectProvisioningRequest("故障工程"),
        _context(),
    )
    assert prepared.value is not None
    project_ref = ProjectRef(ProjectId(str(prepared.value["project_id"])))
    variant_ref = VariantRef(VariantId(str(prepared.value["variant_id"])), project_ref.identity)
    project_path = services.projects.path_for(project_ref)
    variant_path = services.variants.path_for(variant_ref)
    filesystem.fail_replace_destinations.add(variant_path)

    committed = services.gui_project_commands.commit_create(
        str(prepared.value["token"]),
        _context(),
    )

    assert committed.diagnostics[0].code == "PROJECT_PROVISIONING_COMMIT_FAILED"
    assert services.project_lifecycle.active is None
    assert project_path not in filesystem.files
    assert variant_path not in filesystem.files
    assert os.path.join(root, "project-catalog.json") not in filesystem.files
    assert os.path.join(root, "active-project.json") not in filesystem.files


@pytest.mark.parametrize("fault_document", ["project-catalog.json", "active-project.json"])
def test_document_publication_fault_compensates_created_records(fault_document: str) -> None:
    root = os.path.abspath(os.path.join(os.sep, f"transbridge-fr26-{fault_document}"))
    filesystem = MemoryFilesystem()
    services = build_persistence_v2_services(
        root,
        id_factory=_Ids(),
        timestamp_factory=lambda: "2026-08-24T00:00:00+08:00",
        filesystem=filesystem,
    )
    prepared = services.gui_project_commands.prepare_create(
        ProjectProvisioningRequest("补偿工程"),
        _context(),
    )
    assert prepared.value is not None
    project_ref = ProjectRef(ProjectId(str(prepared.value["project_id"])))
    variant_ref = VariantRef(VariantId(str(prepared.value["variant_id"])), project_ref.identity)
    filesystem.fail_replace_destinations.add(os.path.join(root, fault_document))

    committed = services.gui_project_commands.commit_create(
        str(prepared.value["token"]),
        _context(),
    )

    assert committed.diagnostics[0].code == "PROJECT_PROVISIONING_COMMIT_FAILED"
    assert services.project_lifecycle.active is None
    assert services.projects.path_for(project_ref) not in filesystem.files
    assert services.variants.path_for(variant_ref) not in filesystem.files
    assert os.path.join(root, "project-catalog.json") not in filesystem.files
    assert os.path.join(root, "active-project.json") not in filesystem.files


class _Io:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.requests = []

    def parse(self, request):
        self.requests.append(request)
        namespace = SourceNamespace("source:plugin:test")
        source_snapshot = SourceSnapshot.from_bytes(
            request.source,
            FormatId.PLUGIN_SSE,
            self.content,
        )
        entry = TranslationEntry(
            id="legacy-entry",
            key="entry",
            original="Original text",
            translation="",
            stage=Stage.UNTRANSLATED,
            context="FULL",
            entry_key=EntryKey(namespace, "entry"),
            provenance=(),
            revision=EntryRevision(),
        )
        return ParseResult.completed(
            FormatId.PLUGIN_SSE,
            request.source,
            source_snapshot,
            (entry,),
        )


class _PairedPluginIo:
    def __init__(self, translations: dict[str, str]) -> None:
        self.translations = translations
        self.requests = []

    def parse(self, request):
        self.requests.append(request)
        path = Path(request.source.uri)
        namespace = SourceNamespace("source:plugin:paired")
        source_snapshot = SourceSnapshot.from_bytes(
            request.source,
            FormatId.PLUGIN_SSE,
            path.read_bytes(),
        )
        entry = TranslationEntry(
            id="legacy-entry",
            key="entry",
            original=self.translations[path.name],
            translation="",
            stage=Stage.UNTRANSLATED,
            context="FULL",
            entry_key=EntryKey(namespace, "entry"),
            provenance=(),
            revision=EntryRevision(),
        )
        return ParseResult.completed(
            FormatId.PLUGIN_SSE,
            request.source,
            source_snapshot,
            (entry,),
        )


def _plugin_source_record(source_id: str, *, role: str, identity: str) -> dict:
    return {
        "source_id": source_id,
        "enabled": True,
        "format_id": FormatId.PLUGIN_SSE.value,
        "location": f"C:/mods/{source_id}.esp",
        "kind": "plugin",
        "bilingual_capability": "self_contained",
        "legacy": {"role": role, "source_id": identity},
    }


def test_restore_keeps_multiple_matching_plugin_migrations_fail_closed() -> None:
    sources = (
        _plugin_source_record("primary", role="primary", identity="source:plugin:same"),
        _plugin_source_record("translation-a", role="migration", identity="source:plugin:same"),
        _plugin_source_record("translation-b", role="migration", identity="source:plugin:same"),
    )

    assert authoritative_baseline_sources(sources) is sources


def test_restore_keeps_distinct_plugin_migration_as_an_independent_baseline() -> None:
    sources = (
        _plugin_source_record("primary", role="primary", identity="source:plugin:primary"),
        _plugin_source_record("migration", role="migration", identity="source:plugin:other"),
    )

    assert authoritative_baseline_sources(sources) is sources


def test_restore_keeps_legacy_sources_without_roles_or_content_identity() -> None:
    sources = (
        {
            "path": "C:/mods/legacy.esp",
            "format_id": FormatId.PLUGIN_SSE.value,
        },
    )

    assert authoritative_baseline_sources(sources) is sources


def test_restore_folds_each_pair_when_other_primary_plugins_are_present() -> None:
    first = _plugin_source_record("first", role="primary", identity="source:plugin:first")
    first_import = _plugin_source_record("first-import", role="migration", identity="source:plugin:first")
    second = _plugin_source_record("second", role="primary", identity="source:plugin:second")
    second_import = _plugin_source_record("second-import", role="migration", identity="source:plugin:second")
    sources = (first, first_import, second, second_import)

    assert authoritative_baseline_sources(sources) == (first, second)


def test_restore_does_not_fold_a_translation_with_a_conflicting_recorded_relation() -> None:
    sources = (
        _plugin_source_record("primary", role="primary", identity="source:plugin:same"),
        _plugin_source_record("translation", role="migration", identity="source:plugin:same"),
        _plugin_source_record("other", role="primary", identity="source:plugin:other"),
    )
    relations = ({"kind": "translation_for", "from_source_id": "translation", "to_source_id": "other"},)

    assert authoritative_baseline_sources(sources, relations) is sources


def test_source_preparer_infers_plugin_format_and_freezes_verified_baseline(tmp_path: Path) -> None:
    source = tmp_path / "插件.esp"
    source.write_bytes(b"plugin-source")
    io = _Io(source.read_bytes())
    preparer = TranslationIoProjectSourcePreparer(io)

    prepared = preparer.prepare_source(
        ProjectSourceRequest(str(source)),
        _context(),
        role="primary",
        common_options=(),
    )

    assert io.requests[0].format_hint is FormatId.PLUGIN_SSE
    assert prepared.to_dict()["location"] == str(source.resolve())
    assert prepared.to_dict()["fingerprint"] == prepared.baseline.fingerprint.sha256
    assert prepared.baseline.entries[0].entry_key.local_key == "entry"
    assert prepared.hydration is not None
    assert prepared.hydration.entries[0].original == "Original text"


def test_source_preparer_treats_migration_plugin_text_as_translation(tmp_path: Path) -> None:
    source = tmp_path / "汉化插件.esp"
    source.write_bytes(b"translated-plugin")
    preparer = TranslationIoProjectSourcePreparer(_Io(source.read_bytes()))

    prepared = preparer.prepare_source(
        ProjectSourceRequest(str(source)),
        _context(),
        role="migration",
        common_options=(),
    )

    assert prepared.baseline.entries[0].translation == "Original text"
    assert prepared.baseline.entries[0].stage is Stage.TRANSLATED
    assert prepared.hydration is None


def test_plugin_commit_reuses_prepare_hydration_without_second_parse(tmp_path: Path) -> None:
    source = tmp_path / "MyMod.esp"
    source.write_bytes(b"plugin-source")
    io = _Io(source.read_bytes())
    services = build_persistence_v2_services(
        tmp_path / "data",
        id_factory=_Ids(),
        timestamp_factory=lambda: "2026-08-24T00:00:00+08:00",
        source_preparer=TranslationIoProjectSourcePreparer(io),
    )
    prepared = services.gui_project_commands.prepare_create(
        ProjectProvisioningRequest("MyMod", source=ProjectSourceRequest(str(source))),
        _context(),
    )
    assert prepared.is_success and prepared.value is not None

    committed = services.gui_project_commands.commit_create(
        str(prepared.value["token"]),
        _context(),
    )
    hydration = services.gui_project_commands.consume_create_hydration(
        str(prepared.value["project_id"]),
        _context(),
    )

    assert committed.is_success
    assert hydration.is_success and hydration.value is not None
    assert hydration.value.source.entries[0].original == "Original text"
    assert len(io.requests) == 1


@pytest.mark.parametrize("remove_target", ["primary", "migration"])
def test_plugin_pair_roundtrip_and_removal_keeps_source_files(tmp_path: Path, remove_target: str) -> None:
    source = tmp_path / "source.esp"
    translated = tmp_path / "translated.esp"
    source.write_bytes(b"plugin-source")
    translated.write_bytes(b"plugin-translated")
    io = _PairedPluginIo({source.name: "Original text", translated.name: "已有译文"})
    services = build_persistence_v2_services(
        tmp_path / "data",
        id_factory=_Ids(),
        timestamp_factory=lambda: "2026-08-24T00:00:00+08:00",
        source_preparer=TranslationIoProjectSourcePreparer(io),
    )
    prepared = services.gui_project_commands.prepare_create(
        ProjectProvisioningRequest(
            "MyMod",
            source=ProjectSourceRequest(str(source)),
            migration_sources=(ProjectSourceRequest(str(translated)),),
        ),
        _context(),
    )

    assert prepared.is_success and prepared.value is not None
    assert prepared.value["source_count"] == 2
    assert prepared.value["entry_count"] == 1
    committed = services.gui_project_commands.commit_create(
        str(prepared.value["token"]),
        _context(),
    )
    hydration = services.gui_project_commands.consume_create_hydration(
        str(prepared.value["project_id"]),
        _context(),
    )

    assert committed.is_success
    assert hydration.is_success and hydration.value is not None
    assert hydration.value.source.entries[0].original == "Original text"
    assert hydration.value.source.entries[0].translation == "已有译文"
    assert hydration.value.source.entries[0].stage == Stage.TRANSLATED.value
    assert len(io.requests) == 2
    assert services.project_lifecycle.active is not None
    project_data = services.project_lifecycle.active.project.envelope.data
    assert len(project_data["sources"]) == 2
    assert len(project_data["source_relations"]) == 1
    assert services.project_lifecycle.active.variant is not None
    snapshot = services.project_lifecycle.active.variant.snapshot()
    assert len(snapshot.entries) == 1
    assert snapshot.entries[0].translation == "已有译文"

    variant_ref = VariantRef(
        VariantId(str(prepared.value["variant_id"])),
        ProjectId(str(prepared.value["project_id"])),
    )
    baseline = services.baselines.provide(
        services.project_lifecycle.active.project,
        variant_ref,
        _context(),
    )[0]
    restarted = build_persistence_v2_services(
        tmp_path / "data",
        id_factory=_Ids(),
        timestamp_factory=lambda: "2026-08-24T00:01:00+08:00",
    )
    loaded_sources: list[dict] = []

    def load_baseline(source: dict, _context: RequestContext):
        loaded_sources.append(source)
        return baseline

    opener = CurrentProjectOpener(
        str(tmp_path / "data"),
        restarted.projects,
        restarted.variants,
        restarted.baselines,
        restarted.gui_project_commands,
        baseline_loader=load_baseline,
    )

    restored = opener.prepare_active(RequestContext("gui", run_id="restore"))

    assert restored.is_success and restored.value is not None
    assert len(restored.value.sources) == 2
    assert [source["legacy"]["role"] for source in loaded_sources] == ["primary"]
    assert opener.activate(restored.value, RequestContext("gui", run_id="restore")).is_success

    target = source if remove_target == "primary" else translated
    removed = restarted.gui_project_commands.remove_source(str(target), _context())
    assert removed.is_success, removed.diagnostics
    assert restarted.project_lifecycle.active.dirty
    assert restarted.gui_project_commands.save(_context()).is_success
    assert not restarted.project_lifecycle.active.dirty
    restarted.close()
    services.close()

    reopened = build_persistence_v2_services(
        tmp_path / "data",
        id_factory=_Ids(),
        timestamp_factory=lambda: "2026-08-24T00:02:00+08:00",
        source_preparer=TranslationIoProjectSourcePreparer(io),
    )
    result = reopened.current_project_opener.prepare_active(_context())
    assert result.is_success, result.diagnostics
    assert reopened.current_project_opener.activate(result.value, _context()).is_success
    final = reopened.project_lifecycle.active
    assert final.project.envelope.data["source_relations"] == []
    if remove_target == "primary":
        assert final.project.envelope.data["sources"] == []
        assert final.variant.snapshot().entries == ()
        assert final.variant.snapshot().source_fingerprints == ()
    else:
        assert [item["location"] for item in final.project.envelope.data["sources"]] == [str(source.resolve())]
        assert final.variant.snapshot().entries[0].translation == "已有译文"
        assert len(final.variant.snapshot().source_fingerprints) == 1
    assert source.read_bytes() == b"plugin-source"
    assert translated.read_bytes() == b"plugin-translated"
    reopened.close()


def test_source_preparer_rejects_changed_expected_fingerprint(tmp_path: Path) -> None:
    source = tmp_path / "source.esp"
    source.write_bytes(b"plugin-source")
    preparer = TranslationIoProjectSourcePreparer(_Io(source.read_bytes()))

    with pytest.raises(DomainError) as error:
        preparer.prepare_source(
            ProjectSourceRequest(str(source), expected_fingerprint="0" * 64),
            _context(),
            role="primary",
            common_options=(),
        )

    assert error.value.code == "PROJECT_SOURCE_FINGERPRINT_MISMATCH"
