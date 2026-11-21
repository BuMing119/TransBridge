from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from tests.dialogue_catalog_support import field, plugin_bytes, record, reference
from tests.persistence.project_store.test_source_extraction import _create_project
from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId
from transbridge.application.io.plugin_dialogue_catalog import METADATA_KEY, dialogue_catalog_from_snapshot
from transbridge.application.io.plugin_dialogue_order import (
    METADATA_KEY as ORDER_METADATA_KEY,
    dialogue_order_from_snapshot,
)
from transbridge.application.projects.source_extraction import reconstructed_source_snapshot
from transbridge.parser.plugin.dialogue_catalog import CATALOG_VERSION, DialogueCatalog, DialogueRecord
from transbridge.parser.plugin.dialogue_order_binary import DialogueOrderCancelled
from transbridge.persistence.project_store.archive import ProjectPackage
from transbridge.persistence.project_store.catalog import ActiveProjectPreference, ProjectCatalog
from transbridge.persistence.project_store.connection import project_connection
from transbridge.persistence.project_store.format import (
    PROJECT_FILENAME,
    PROJECT_FORMAT,
    PROJECT_FORMAT_VERSION,
    UnsupportedProjectFormat,
)
from transbridge.persistence.project_store.records import WorkspaceProjects
from transbridge.persistence.project_store.repository import ProjectStore
from transbridge.persistence.v2 import OsPersistenceFilesystem


def _project(tmp_path, *, topic_editor="StringlessTopic", duplicate_topic=False):
    source = tmp_path / "Dialogue.esp"
    topic = record(
        "DIAL",
        0x10,
        field("EDID", topic_editor.encode("utf-8") + b"\0") + reference("QNAM", 1) + field("DATA", b"\0\2\0\0"),
    )
    source.write_bytes(
        plugin_bytes({
            "QUST": record("QUST", 1, field("EDID", b"Quest\0") + field("FULL", b"Journal\0")),
            "DIAL": topic + topic if duplicate_topic else topic,
            "SCEN": record(
                "SCEN",
                0x12,
                field("EDID", b"TestScene\0")
                + reference("PNAM", 1)
                + field("ANAM", b"\0\0")
                + reference("DATA", 0x10)
                + field("ANAM", b""),
            ),
        })
    )
    database, variant = _create_project(tmp_path / "data", source, FormatId.PLUGIN_SSE, role="primary")
    source.unlink()
    return database, variant


def _snapshot(database):
    (info,) = ProjectStore(database).sources()
    return reconstructed_source_snapshot(info, ())


def _package(tmp_path, database):
    destination = tmp_path / "catalog.transbridge"
    with ZipFile(destination, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps({
                "format": PROJECT_FORMAT,
                "version": PROJECT_FORMAT_VERSION,
                "project_id": ProjectStore(database).summary().identity.value,
                "members": [PROJECT_FILENAME],
            }),
        )
        archive.write(database, PROJECT_FILENAME)
    return destination


def _packages(tmp_path):
    workspace = WorkspaceProjects(tmp_path / "imported")
    catalog = ProjectCatalog(workspace, ActiveProjectPreference(workspace.root, OsPersistenceFilesystem()))
    return ProjectPackage(workspace, catalog, lifecycle=None)


def test_project_and_portable_import_keep_scenes_and_stringless_topics_without_plugin_input(tmp_path, monkeypatch):
    database, variant = _project(tmp_path)
    source = _snapshot(database)
    assert source.content is None
    expected = DialogueCatalog((
        DialogueRecord("QUST", "00000001", "Quest"),
        DialogueRecord("DIAL", "00000010", "StringlessTopic", "00000001", "Scene"),
        DialogueRecord("SCEN", "00000012", "TestScene", "00000001", topic_ids=("00000010",)),
    ))
    assert dialogue_catalog_from_snapshot(source) == expected
    scenes = dialogue_order_from_snapshot(source).scenes
    assert [(item.form_id, item.quest_id, item.topic_ids) for item in scenes] == [
        ("00000012", "00000001", ("00000010",))
    ]
    rows = ProjectStore(database).read_page(variant)
    assert [row.snapshot.original for row in rows] == ["Journal"]
    package = _package(tmp_path, database)

    def forbidden(*_args, **_kwargs):
        pytest.fail("Saved navigation must not open a historical plugin input")

    # All navigation and archive operations below use saved business facts.
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    packages = _packages(tmp_path)
    assert packages.inspect_import(str(package), RequestContext("catalog-test")).project_id
    imported = packages.import_project(str(package), RequestContext("catalog-test"))
    assert dialogue_catalog_from_snapshot(_snapshot(imported)) == expected
    assert dialogue_order_from_snapshot(_snapshot(imported)).scenes == scenes
    assert dialogue_catalog_from_snapshot(source) == expected


@pytest.mark.parametrize("with_catalog", [False, True])
@pytest.mark.parametrize("version", [1, 2])
def test_legacy_scene_evidence_metadata_remains_portable_without_source_bytes(
    tmp_path, monkeypatch, with_catalog, version
):
    database, _variant = _project(tmp_path)
    with project_connection(database, write=True) as connection:
        (raw,) = connection.execute("SELECT format_fields FROM source").fetchone()
        fields = json.loads(raw)
        _version, fingerprint, payload = fields[ORDER_METADATA_KEY]
        fields[ORDER_METADATA_KEY] = [version, fingerprint, payload[:7] if version == 1 else payload]
        if not with_catalog:
            fields.pop(METADATA_KEY)
        else:
            fields[METADATA_KEY][0] = 1
        connection.execute("UPDATE source SET format_fields = ?", (json.dumps(fields),))
    package = _package(tmp_path, database)
    monkeypatch.setattr(Path, "read_bytes", lambda *_: pytest.fail("Historical plugin input must not be opened"))
    imported = _packages(tmp_path).import_project(str(package), RequestContext("catalog-test"))
    evidence = dialogue_order_from_snapshot(_snapshot(imported))
    assert evidence.complete
    assert evidence.extraction_version == version
    assert any(item.kind == "DIAL" and item.form_id == "00000010" for item in evidence.records)
    assert [item.topic_ids for item in evidence.scenes] == ([("00000010",)] if with_catalog or version == 2 else [])


def test_old_project_without_catalog_metadata_stays_readable_without_source_input(tmp_path):
    database, _variant = _project(tmp_path)
    with project_connection(database, write=True) as connection:
        connection.execute("UPDATE source SET format_fields = json_remove(format_fields, '$.plugin_dialogue_catalog')")
    snapshot = _snapshot(database)
    assert snapshot.content is None
    assert dialogue_catalog_from_snapshot(snapshot) is None
    package = _package(tmp_path, database)
    assert _packages(tmp_path).inspect_import(str(package), RequestContext("catalog-test")).project_id


@pytest.mark.parametrize(
    "payload",
    [
        None,
        (CATALOG_VERSION + 1, "digest", ()),
        (CATALOG_VERSION, "wrong-revision", ()),
        (CATALOG_VERSION, "digest", (("SCEN", "00000012", "Scene", "00000001", "", (0x10,)),)),
        (CATALOG_VERSION, "digest", (("SCEN", "00000012", "Scene", "00000001", "", (), "plugin-bytes"),)),
    ],
    ids=["null", "version", "revision", "typed-reference", "unapproved-field"],
)
def test_invalid_saved_catalog_is_rejected_by_runtime_and_portable_import(tmp_path, payload):
    database, _variant = _project(tmp_path)
    snapshot = _snapshot(database)
    if isinstance(payload, tuple):
        version, digest, records = payload
        payload = version, snapshot.sha256 if digest == "digest" else digest, records
    with project_connection(database, write=True) as connection:
        (raw,) = connection.execute("SELECT format_fields FROM source").fetchone()
        fields = json.loads(raw)
        fields[METADATA_KEY] = payload
        connection.execute("UPDATE source SET format_fields = ?", (json.dumps(fields),))
    with pytest.raises(ValueError):
        dialogue_catalog_from_snapshot(_snapshot(database))
    package = _package(tmp_path, database)
    with pytest.raises(UnsupportedProjectFormat):
        _packages(tmp_path).inspect_import(str(package), RequestContext("catalog-test"))


def test_catalog_metadata_read_observes_cancellation(tmp_path):
    database, _variant = _project(tmp_path)
    with pytest.raises(DialogueOrderCancelled):
        dialogue_catalog_from_snapshot(_snapshot(database), cancellation=SimpleNamespace(is_cancelled=True))


def test_metadata_only_catalog_does_not_depend_on_text_read_defaults(tmp_path):
    database, _variant = _project(tmp_path)
    snapshot = _snapshot(database)
    assert dialogue_catalog_from_snapshot(replace(snapshot, encoding="not-a-codec")) == dialogue_catalog_from_snapshot(
        snapshot
    )


@pytest.mark.parametrize("topic_editor", ["C:/Editor/Topic", "\\EditorTopic", "/EditorTopic", "file:Topic"])
def test_duplicate_navigation_ids_and_path_shaped_edids_remain_valid_catalog_facts(tmp_path, topic_editor):
    database, _variant = _project(tmp_path, topic_editor=topic_editor, duplicate_topic=True)
    catalog = dialogue_catalog_from_snapshot(_snapshot(database))
    assert [item.editor_id for item in catalog.records if item.kind == "DIAL"] == [topic_editor, topic_editor]
    package = _package(tmp_path, database)
    packages = _packages(tmp_path)
    imported = packages.import_project(str(package), RequestContext("catalog-test"))
    assert dialogue_catalog_from_snapshot(_snapshot(imported)) == catalog
