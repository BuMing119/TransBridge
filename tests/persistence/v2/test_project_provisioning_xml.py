"""Existing XML translations follow the same authoritative creation path as plugins."""

from pathlib import Path
from uuid import uuid4

import pytest

from tests.plugin_fixtures import write_plugin
from transbridge.application.contracts import DomainError, RequestContext
from transbridge.application.io import FormatId, Stage
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.persistence.project_provisioning import TranslationIoProjectSourcePreparer


def _services(root):
    return build_persistence_v2_services(
        root, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "2026-11-07T12:00:00+08:00"
    )


def _xml(path: Path, kind: str, encoding: str) -> Path:
    content = (
        "<DocumentElement><ESP><GRUP>NPC_</GRUP><ID>00000800</ID><EDID>Npc</EDID>"
        "<CHAMP>FULL</CHAMP><ORIGINAL>Hello</ORIGINAL><TRADUIT>你好</TRADUIT>"
        "<INDEX>1</INDEX><STATUS>99</STATUS></ESP></DocumentElement>"
        if kind == "eet"
        else "<SSTXMLRessources><Content><String><EDID>Npc</EDID><REC>NPC_:FULL</REC>"
        "<Source>Hello</Source><Dest>你好</Dest></String></Content></SSTXMLRessources>"
    )
    path.write_text(content, encoding=encoding)
    return path


@pytest.mark.parametrize("kind,suffix", [("eet", ".xml"), ("eet", ".eet"), ("xt", ".XML")])
@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16"])
def test_xml_migration_creates_and_reopens_with_preserved_translation(tmp_path, kind, suffix, encoding):
    plugin = write_plugin(tmp_path / "Original.esp", [(0x801, "OtherNpc", "Other original")])
    source = _xml(tmp_path / f"translated{suffix}", kind, encoding)
    source_bytes = source.read_bytes()
    context = RequestContext("gui", run_id="xml-provisioning")
    root = tmp_path / "data"
    services = _services(root)
    try:
        prepared = services.gui_project_commands.prepare_create(
            ProjectProvisioningRequest(
                "XML 译文工程",
                source=ProjectSourceRequest(str(plugin)),
                migration_sources=(ProjectSourceRequest(str(source)),),
            ),
            context,
        )
        assert prepared.is_success and prepared.value is not None, prepared.diagnostics
        assert prepared.value["source_count"] == prepared.value["entry_count"] == 2
        assert services.gui_project_commands.commit_create(str(prepared.value["token"]), context).is_success
        hydration = services.gui_project_commands.consume_create_hydration(str(prepared.value["project_id"]), context)
        assert hydration.is_success and hydration.value is not None
        xml_source = next(item for item in hydration.value.sources if item.location == str(source.resolve()))
        assert xml_source.format_id is FormatId(f"xml.{kind}")
        assert xml_source.source_snapshot.content == source_bytes
        assert [(item.original, item.translation) for item in xml_source.entries] == [("Hello", "你好")]
        snapshot = services.project_lifecycle.active.variant.snapshot()
        translated = next(item for item in snapshot.entries if item.translation == "你好")
        assert translated.stage is Stage.TRANSLATED
        assert services.gui_project_commands.save(context).is_success
    finally:
        services.close()

    # Saved source artifacts, including their XML templates, must survive losing the input path.
    source.unlink()
    restarted = _services(root)
    try:
        restored = restarted.current_project_opener.prepare_active(context)
        assert restored.is_success and restored.value is not None, restored.diagnostics
        assert restarted.current_project_opener.activate(restored.value, context).is_success
        assert restarted.project_lifecycle.active.variant.snapshot().entries == snapshot.entries
        assert restarted.project_lifecycle.active.variant.snapshot().source_fingerprints == snapshot.source_fingerprints
    finally:
        restarted.close()


@pytest.mark.parametrize("kind", ["eet", "xt"])
def test_xml_can_also_be_used_as_the_only_project_source_without_a_hint(tmp_path, kind):
    source = _xml(tmp_path / "source.xml", kind, "utf-8")
    prepared = TranslationIoProjectSourcePreparer().prepare_source(
        ProjectSourceRequest(str(source)), RequestContext("gui"), role="primary", common_options=()
    )
    assert prepared.to_dict()["format_id"] == f"xml.{kind}"
    assert prepared.baseline.entries[0].translation == "你好"


@pytest.mark.parametrize("content", [b"<Other/>", b"<DocumentElement><ESP>", b"not XML"])
def test_invalid_xml_migration_does_not_publish_a_project(tmp_path, content):
    source = tmp_path / "invalid.xml"
    source.write_bytes(content)
    root = tmp_path / "data"
    services = _services(root)
    try:
        result = services.gui_project_commands.prepare_create(
            ProjectProvisioningRequest("失败工程", migration_sources=(ProjectSourceRequest(str(source)),)),
            RequestContext("gui"),
        )
        assert not result.is_success
        assert str(source) in result.diagnostics[0].message
        assert services.project_lifecycle.active is None
        assert not (root / "project-catalog.json").exists()
        assert not (root / "active-project.json").exists()
        assert source.read_bytes() == content
    finally:
        services.close()


def test_explicit_xml_format_does_not_silently_switch_dialects(tmp_path):
    source = _xml(tmp_path / "eet.xml", "eet", "utf-8")
    with pytest.raises(DomainError, match="工程来源无法解析"):
        TranslationIoProjectSourcePreparer().prepare_source(
            ProjectSourceRequest(str(source), format_hint=FormatId.XML_XT),
            RequestContext("gui"),
            role="migration",
            common_options=(),
        )


def test_ambiguous_content_requires_a_format_instead_of_guessing(tmp_path):
    source = tmp_path / "ambiguous.json"
    source.write_text("[]", encoding="utf-8")
    with pytest.raises(DomainError, match="多个格式") as failure:
        TranslationIoProjectSourcePreparer().prepare_source(
            ProjectSourceRequest(str(source)), RequestContext("gui"), role="migration", common_options=()
        )
    assert failure.value.code == "PROJECT_SOURCE_FORMAT_REQUIRED"
    assert len(failure.value.details["candidates"]) > 1


def test_probe_read_failure_preserves_the_original_cause(tmp_path, monkeypatch):
    source = _xml(tmp_path / "locked.xml", "eet", "utf-8")
    error = PermissionError("file locked")

    def fail_read(_path):
        raise error

    monkeypatch.setattr(Path, "read_bytes", fail_read)
    with pytest.raises(DomainError) as failure:
        TranslationIoProjectSourcePreparer().prepare_source(
            ProjectSourceRequest(str(source)), RequestContext("gui"), role="migration", common_options=()
        )
    assert failure.value.code == "PROJECT_SOURCE_READ_FAILED"
    assert failure.value.__cause__ is error
