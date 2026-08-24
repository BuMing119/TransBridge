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
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.converter.translation_entry import TranslationEntry
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
