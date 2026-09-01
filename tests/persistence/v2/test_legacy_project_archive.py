from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

import pytest

from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId
from transbridge.application.io.identity import EntryRevision, Provenance
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.persistence.legacy_project_archive import decode_legacy_archive
from transbridge.persistence.project_provisioning import TranslationIoProjectSourcePreparer
from transbridge.persistence.v2 import ProjectId, VariantSnapshot
from transbridge.persistence.v2.models import SchemaValidationError


def _context() -> RequestContext:
    return RequestContext("gui", run_id="legacy-archive")


def _project(*sources: dict, names: tuple[str, ...] = ("主版本",)) -> dict:
    return {
        "name": "旧版工程",
        "sources": list(sources),
        "variants": [{"name": name} for name in names],
        "active_variant": names[0],
    }


def _variant(name: str = "主版本", *, key: str = "old-entry") -> dict:
    return {
        "variant": name,
        "translations": {key: "译文", "explicit-empty": ""},
        "labels": {key: ["reviewed"], "explicit-empty": []},
        "label_library": {"reviewed": {"color": "green", "rules": [["keep", 1]]}},
        "entry_states": {
            key: {"stage": 5, "revision": 7, "provenance": [Provenance("old-run", "editor", "manual").to_dict()]},
            "explicit-empty": {"stage": 9, "revision": 3, "provenance": []},
            "state-only": {"stage": -1, "revision": 2, "provenance": []},
        },
    }


def _write_archive(path: Path, project: dict, documents: dict[str, dict | str]) -> Path:
    with ZipFile(path, "w") as archive:
        archive.writestr("project.json", json.dumps(project, ensure_ascii=False))
        for member, document in documents.items():
            archive.writestr(
                member, document if isinstance(document, str) else json.dumps(document, ensure_ascii=False)
            )
    return path


def _decode(path: Path, *, preparer=None):
    with ZipFile(path) as archive:
        return decode_legacy_archive(
            archive,
            project_id=ProjectId("imported"),
            source_preparer=preparer or TranslationIoProjectSourcePreparer(),
            context=_context(),
        )


def _xml_source(path: Path, *, original: str = "Hello", extra_entry: bool = False):
    extra = (
        '<String List="0"><EDID>Extra</EDID><REC>INFO:NAM1</REC><Source>Extra</Source><Dest /></String>'
        if extra_entry
        else ""
    )
    path.write_text(
        "<SSTXMLRessources><Params><Addon>Fixture</Addon></Params><Content>"
        '<String List="0"><EDID>Greeting</EDID><REC>INFO:NAM1</REC>'
        f"<Source>{original}</Source><Dest>来源译文</Dest></String>"
        f"{extra}</Content></SSTXMLRessources>",
        encoding="utf-8",
    )
    prepared = TranslationIoProjectSourcePreparer().prepare_source(
        ProjectSourceRequest(str(path), format_hint=FormatId.XML_XT),
        _context(),
        role="primary",
        common_options=(),
    )
    assert prepared.hydration is not None
    return {"path": str(path), "key": str(path), "type": "xt"}, prepared


def test_missing_source_keeps_all_variants_entry_states_and_duplicate_named_snapshots(tmp_path: Path) -> None:
    project = _project(
        {"key": "missing", "type": "esp", "path": str(tmp_path / "missing.esp")}, names=("主版本", "branch")
    )
    first = _variant()
    second = _variant("branch")
    second["translations"]["old-entry"] = "分支译文"
    before = deepcopy(first)
    before["snapshot_name"] = "检查点"
    after = deepcopy(before)
    after["translations"]["old-entry"] = "历史译文"
    path = _write_archive(
        tmp_path / "legacy.transbridge",
        project,
        {
            "主版本/current.json": first,
            "branch/current.json": second,
            "主版本/snapshots/20260830-000001-checkpoint.json": before,
            "主版本/snapshots/20260830-000002-checkpoint.json": after,
        },
    )
    original_bytes = path.read_bytes()

    decoded, variants, snapshots = _decode(path)

    assert path.read_bytes() == original_bytes
    assert len(variants) == 2 and len(snapshots) == 2
    assert decoded.envelope.data["legacy"]["archive_recovery"] == "source-baseline-required"
    states = {entry.entry_key.local_key: entry for entry in variants[0].entries}
    assert all(entry.entry_key.namespace.value == "legacy:v1" for entry in states.values())
    assert variants[0].source_fingerprints[0].sha256 is None
    assert states["old-entry"].translation == "译文"
    assert states["old-entry"].stage.value == 5
    assert states["old-entry"].revision == EntryRevision(7)
    assert states["old-entry"].provenance == (Provenance("old-run", "editor", "manual"),)
    assert states["old-entry"].labels == ("reviewed",)
    assert states["explicit-empty"].translation == "" and states["explicit-empty"].stage.value == 9
    assert states["state-only"].stage.value == -1
    assert variants[0].to_dto().envelope.data["label_library"] == first["label_library"]
    assert snapshots[0]["name"] == "检查点" and snapshots[1]["name"] != snapshots[0]["name"]
    assert {row["translation"] for doc in snapshots for row in doc["variant"]["data"]["entries"]} >= {
        "译文",
        "历史译文",
    }


def test_verified_source_remaps_exact_legacy_identity_and_materializes_saved_state(tmp_path: Path) -> None:
    source, prepared = _xml_source(tmp_path / "source.xml")
    parsed = prepared.hydration.entries[0]
    saved = {
        "variant": "主版本",
        "translations": {parsed.legacy_id: "人工译文"},
        "labels": {parsed.legacy_id: ["reviewed"]},
        "label_library": {"reviewed": {"color": "green"}},
        "entry_states": {parsed.legacy_id: {"stage": 5, "revision": 7, "provenance": []}},
    }
    historical = deepcopy(saved)
    historical["snapshot_name"] = "历史检查点"
    historical["translations"][parsed.legacy_id] = "历史译文"
    archive = _write_archive(
        tmp_path / "legacy.transbridge",
        _project(source),
        {
            "主版本/current.json": saved,
            "主版本/snapshots/20260830-000001-checkpoint.json": historical,
        },
    )

    project, variants, snapshots = _decode(archive)

    entry = variants[0].entries[0]
    assert entry.entry_key == parsed.entry_key
    assert entry.translation == "人工译文" and entry.stage.value == 5 and entry.revision.value == 7
    assert entry.labels == ("reviewed",)
    assert variants[0].source_fingerprints == (prepared.baseline.fingerprint,)
    assert project.envelope.data["sources"][0]["format_id"] == "xml.xt"
    assert snapshots[0]["variant"]["data"]["entries"][0]["entry_key"] == parsed.entry_key.to_dict()
    assert snapshots[0]["variant"]["data"]["entries"][0]["translation"] == "历史译文"
    services = build_persistence_v2_services(
        tmp_path / "v2", id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now"
    )
    imported = services.project_archive.import_project(str(archive), _context())
    opened = services.current_project_opener.open_path(imported, _context())
    assert opened.is_success and not opened.value.get("read_only", False)
    actual = services.project_lifecycle.active.variant.snapshot().entries[0]
    assert actual == entry


def test_returning_source_does_not_make_unmapped_legacy_states_editable(tmp_path: Path) -> None:
    path = tmp_path / "source.xml"
    source, _ = _xml_source(path)
    source_bytes = path.read_bytes()
    path.unlink()
    archive = _write_archive(tmp_path / "legacy.transbridge", _project(source), {"主版本/current.json": _variant()})
    services = build_persistence_v2_services(
        tmp_path / "v2", id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now"
    )
    imported = services.project_archive.import_project(str(archive), _context())
    missing = services.current_project_opener.open_path(imported, _context())
    assert missing.is_success and missing.value["read_only"]
    stored = missing.value["recovery"].variant
    path.write_bytes(source_bytes)

    returned = services.current_project_opener.open_path(imported, _context())

    assert returned.is_success and returned.value["read_only"]
    assert returned.value["recovery"].variant == stored
    assert services.project_lifecycle.active is None
    assert VariantSnapshot.from_dto(services.variants.read_snapshot(stored.ref)) == stored


@pytest.mark.parametrize("with_missing_source", [False, True])
def test_import_and_open_without_baseline_preserves_data_and_active_project(
    tmp_path: Path, with_missing_source: bool
) -> None:
    sources = ({"key": "missing", "type": "esp", "path": str(tmp_path / "missing.esp")},) if with_missing_source else ()
    snapshot = {**_variant(), "snapshot_name": "检查点"}
    archive = _write_archive(
        tmp_path / "legacy.transbridge",
        _project(*sources),
        {
            "主版本/current.json": _variant(),
            "主版本/snapshots/20260830-000001-checkpoint.json": snapshot,
            "主版本/snapshots/20260830-000002-checkpoint.json": snapshot,
        },
    )
    services = build_persistence_v2_services(
        tmp_path / "v2", id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now"
    )
    assert services.gui_project_commands.create_project(ProjectProvisioningRequest("已打开工程"), _context()).is_success
    active = services.project_lifecycle.active
    pointer = (tmp_path / "v2" / "active-project.json").read_bytes()

    imported = services.project_archive.import_project(str(archive), _context())
    opened = services.current_project_opener.open_path(imported, _context())

    assert opened.is_success and opened.value["read_only"]
    recovery = opened.value["recovery"]
    assert len(recovery.variant.entries) == 3
    assert {entry.translation for entry in recovery.variant.entries} == {"译文", ""}
    assert services.project_lifecycle.active is active
    assert (tmp_path / "v2" / "active-project.json").read_bytes() == pointer
    assert VariantSnapshot.from_dto(services.variants.read_snapshot(recovery.variant.ref)) == recovery.variant
    snapshot_files = list((tmp_path / "v2" / "snapshots").glob("*.json"))
    assert len(snapshot_files) == 2
    for path in snapshot_files:
        document = json.loads(path.read_bytes())
        assert len(document["variant"]["data"]["entries"]) == 3


@pytest.mark.parametrize("ambiguous", [False, True])
def test_existing_sources_refuse_unmapped_or_ambiguous_legacy_ids(tmp_path: Path, ambiguous: bool) -> None:
    first, prepared = _xml_source(tmp_path / "source.xml")
    sources = [first]
    key = "unknown-id"
    if ambiguous:
        second, _ = _xml_source(tmp_path / "second.xml", original="Different source", extra_entry=True)
        sources.append(second)
        key = prepared.hydration.entries[0].legacy_id
    archive = _write_archive(
        tmp_path / "legacy.transbridge",
        _project(*sources),
        {
            "主版本/current.json": {"variant": "主版本", "translations": {key: "不能丢失"}},
        },
    )

    with pytest.raises(ValueError, match="无法唯一映射"):
        _decode(archive)


@pytest.mark.parametrize(
    "state",
    [{"stage": True}, {"stage": 8}, {"revision": -1}, {"revision": True}, {"provenance": "bad"}, {"future_state": 1}],
)
def test_invalid_legacy_entry_state_fails_instead_of_inference(tmp_path: Path, state: dict) -> None:
    variant = _variant()
    variant["entry_states"]["old-entry"] = state
    archive = _write_archive(tmp_path / "legacy.transbridge", _project(), {"主版本/current.json": variant})
    with pytest.raises((ValueError, SchemaValidationError)):
        _decode(archive)


@pytest.mark.parametrize(
    "documents",
    [
        {},
        {"主版本/current.json": '{"translations":{},"translations":{}}'},
        {"主版本/current.json": {"variant": "wrong", "translations": {}}},
        {
            "主版本/current.json": {"translations": {}},
            "undeclared/current.json": {"translations": {"entry": "不能丢失"}},
        },
    ],
)
def test_incomplete_or_ambiguous_bundle_is_rejected_without_silent_loss(tmp_path: Path, documents: dict) -> None:
    archive = _write_archive(tmp_path / "legacy.transbridge", _project(), documents)
    with pytest.raises((ValueError, SchemaValidationError)):
        _decode(archive)


def test_empty_legacy_project_has_no_fake_unverified_fingerprint(tmp_path: Path) -> None:
    archive = _write_archive(tmp_path / "legacy.transbridge", _project(), {"主版本/current.json": {"translations": {}}})
    _, variants, _ = _decode(archive)
    assert variants[0].entries == () and variants[0].source_fingerprints == ()


@pytest.mark.parametrize(
    ("kind", "suffix", "expected"),
    [("xt", ".xml", "xml.xt"), ("eet", ".eet", "binary.eet"), ("sst", ".sst", "sst.ssu8")],
)
def test_missing_legacy_source_preserves_parser_selection(
    tmp_path: Path, kind: str, suffix: str, expected: str
) -> None:
    source = {"key": "missing", "type": kind, "path": str(tmp_path / f"missing{suffix}")}
    archive = _write_archive(tmp_path / "legacy.transbridge", _project(source), {"主版本/current.json": _variant()})
    project, variants, _ = _decode(archive)
    assert project.envelope.data["sources"][0]["format_id"] == expected
    assert len(variants[0].entries) == 3
