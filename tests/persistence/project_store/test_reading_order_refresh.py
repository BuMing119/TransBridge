from contextlib import contextmanager
import json
import stat
import struct
from types import SimpleNamespace

import pytest

from tests.dialogue_catalog_support import field, plugin_bytes, record, reference
from tests.persistence.project_store.test_source_extraction import _create_project
from transbridge.application.io import FormatId
from transbridge.application.projects.models import TransitionTarget
from transbridge.application.projects.working_copy import ProjectWorkingCopy
from transbridge.persistence.project_store import reading_order_refresh as refresh
from transbridge.persistence.project_store.connection import project_connection
from transbridge.persistence.project_store.paged_reads import read_workbench_candidate
from transbridge.persistence.project_store.records import WorkspaceProjects
from transbridge.persistence.project_store.repository import ProjectStore
from transbridge.persistence.v2.ids import ProjectId, ProjectRef, VariantRef

OLD = [
    "Quest",
    "Default question",
    "Override question",
    "Override reply",
    "Default reply",
    "Second topic",
    "Second reply",
]
QUESTIONS = ["Default question", "Default reply", "Override question", "Override reply"]
CURRENT = ["Quest", "Second topic", "Second reply", *QUESTIONS]


def _response(text):
    return field("TRDT", bytes(12) + bytes([1]) + bytes(11)) + field("NAM1", text.encode() + b"\0")


def _children(topic, content):
    return struct.pack("<4sIIiHHHH", b"GRUP", len(content) + 24, topic, 7, 0, 0, 0, 0) + content


def _legacy_project(tmp_path, *, with_catalog=True):
    source = tmp_path / "Legacy.esp"
    source.write_bytes(
        plugin_bytes({
            "QUST": record("QUST", 1, field("FULL", b"Quest\0")),
            "DIAL": record("DIAL", 0x10, reference("QNAM", 1) + field("FULL", b"Default question\0"))
            + _children(
                0x10,
                record("INFO", 0x11, _response("Override reply") + field("RNAM", b"Override question\0"))
                + record("INFO", 0x12, _response("Default reply")),
            )
            + record("DIAL", 0x20, reference("QNAM", 1) + field("FULL", b"Second topic\0"))
            + _children(0x20, record("INFO", 0x21, _response("Second reply"))),
            "SCEN": record(
                "SCEN",
                0x30,
                reference("PNAM", 1)
                + b"".join(
                    field("ANAM", b"\0\0") + reference("DATA", topic) + field("ANAM", b"") for topic in (0x20, 0x10)
                ),
            ),
        })
    )
    database, variant = _create_project(tmp_path / "data", source, FormatId.PLUGIN_SSE, role="primary")
    source.unlink()
    with project_connection(database, write=True) as connection:
        (raw,) = connection.execute("SELECT format_fields FROM source").fetchone()
        fields = json.loads(raw)
        _version, fingerprint, payload = fields[refresh.METADATA_KEY]
        fields[refresh.METADATA_KEY] = [1, fingerprint, payload[:7]]
        if not with_catalog:
            fields.pop("plugin_dialogue_catalog")
        connection.execute("UPDATE source SET format_fields = ?", (json.dumps(fields),))
        connection.execute("UPDATE source_entry SET reading_ordinal = NULL")
        connection.executemany("UPDATE source_entry SET reading_ordinal = ? WHERE original = ?", enumerate(OLD))
    return database, variant


def _texts(rows):
    return [row.snapshot.original for row in rows]


def _saved(database):
    with project_connection(database) as connection:
        (fields,) = connection.execute("SELECT format_fields FROM source").fetchone()
        ranks = tuple(connection.execute("SELECT id, reading_ordinal FROM source_entry ORDER BY id"))
    return json.loads(fields), tuple(tuple(row) for row in ranks)


@pytest.mark.parametrize("with_catalog", [False, True])
def test_legacy_ranks_refresh_once_from_saved_facts_and_keep_translation_state(tmp_path, monkeypatch, with_catalog):
    database, variant = _legacy_project(tmp_path, with_catalog=with_catalog)
    store = ProjectStore(database)
    working = ProjectWorkingCopy(store, variant)
    reply = next(row for row in store.read_page(variant) if row.snapshot.original == "Default reply")
    working.edit(reply.snapshot.entry_key, translation="Saved translation")
    working.save()
    token = store.read_token(variant)
    expected = CURRENT if with_catalog else ["Quest", *QUESTIONS, "Second topic", "Second reply"]
    assert _texts(store.read_page(variant, reading_order=True, limit=2)) == expected[:2]
    assert store.read_token(variant) == token
    assert store.read_entries(variant, (reply.snapshot.entry_key,))[0].state.translation == "Saved translation"
    assert _saved(database)[0][refresh.METADATA_KEY][0] == 2

    statements = []
    original_connection = refresh.project_connection

    @contextmanager
    def traced(*args, **kwargs):
        with original_connection(*args, **kwargs) as connection:
            connection.set_trace_callback(statements.append)
            yield connection

    monkeypatch.setattr(refresh, "project_connection", traced)
    monkeypatch.setattr(refresh, "_prepare", lambda *_: pytest.fail("Repeated source refresh"))
    assert _texts(store.read_page(variant, reading_order=True, offset=2, limit=2)) == expected[2:4]
    assert not any("format_fields" in sql or "source_entry" in sql for sql in statements)
    monkeypatch.undo()

    monkeypatch.setattr(refresh, "build_dialogue_reading_view", lambda *_: pytest.fail("Current ranks were rebuilt"))
    monkeypatch.setattr(refresh, "read_sources", lambda *_: pytest.fail("Current native metadata was decoded"))
    reopened = ProjectStore(database)
    assert _texts(tuple(reopened.iter_entries(variant, reading_order=True))) == expected
    assert _texts(reopened.read_page(variant)) != expected


def test_paged_open_preview_refreshes_before_its_direct_sql_read(tmp_path):
    database, variant = _legacy_project(tmp_path)
    projects = WorkspaceProjects(tmp_path / "data")
    project = ProjectStore(database).summary().identity
    target = TransitionTarget(ProjectRef(project), VariantRef(variant, project))
    records = read_workbench_candidate(projects, target)
    assert _texts(records.paged_read.sources[0].preview) == CURRENT
    assert _saved(database)[0][refresh.METADATA_KEY][0] == 2


def test_v2_saved_ranks_require_explicit_source_refresh_for_phase_order(tmp_path, monkeypatch):
    database, variant = _legacy_project(tmp_path)
    with project_connection(database, write=True) as connection:
        (raw,) = connection.execute("SELECT format_fields FROM source").fetchone()
        fields = json.loads(raw)
        _, fingerprint, payload = fields[refresh.METADATA_KEY]
        fields[refresh.METADATA_KEY] = [2, fingerprint, [*payload, []]]
        fields["plugin_dialogue_catalog"][0] = 1
        connection.execute("UPDATE source SET format_fields = ?", (json.dumps(fields),))
    before = _saved(database)
    monkeypatch.setattr(refresh, "read_sources", lambda *_: pytest.fail("Legacy v2 metadata must retain saved ranks"))
    store = ProjectStore(database)
    assert _texts(store.read_page(variant, reading_order=True)) == OLD
    assert _saved(database) == before


def test_failed_rank_publication_rolls_back_ranks_and_version_marker(tmp_path, monkeypatch):
    database, variant = _legacy_project(tmp_path)
    before = _saved(database)
    original_apply = refresh._apply

    def failed(connection, plan):
        original_apply(connection, plan)
        raise RuntimeError("Injected failure after publication")

    monkeypatch.setattr(refresh, "_apply", failed)
    store = ProjectStore(database)
    with pytest.raises(RuntimeError, match="Injected failure"):
        store.read_page(variant, reading_order=True)
    assert _saved(database) == before
    assert not store._reading_order_sources


def test_read_only_project_retains_saved_ranks_with_actionable_warning(tmp_path, caplog):
    database, variant = _legacy_project(tmp_path)
    before = _saved(database)
    database.chmod(stat.S_IREAD)
    try:
        assert _texts(ProjectStore(database).read_page(variant, reading_order=True)) == OLD
    finally:
        database.chmod(stat.S_IREAD | stat.S_IWRITE)
    assert _saved(database) == before
    assert "PROJECT_READING_ORDER_NOT_WRITABLE" in caplog.text
    assert "writable project copy" in caplog.text


def test_invalid_open_identity_cannot_publish_derived_updates(tmp_path):
    database, variant = _legacy_project(tmp_path)
    before = _saved(database)
    project = ProjectId("wrong-project")
    target = TransitionTarget(ProjectRef(project), VariantRef(variant, project))
    projects = SimpleNamespace(store_for=lambda _: ProjectStore(database))
    with pytest.raises(refresh.ProjectStoreError, match="identity differs"):
        read_workbench_candidate(projects, target)
    assert _saved(database) == before


def test_sqlite_source_id_reuse_after_restore_does_not_reuse_checked_ranks(tmp_path):
    database, variant = _legacy_project(tmp_path)
    with project_connection(database) as connection:
        source = dict(connection.execute("SELECT * FROM source").fetchone())
        entries = tuple(dict(row) for row in connection.execute("SELECT * FROM source_entry"))
        installed = dict(connection.execute("SELECT * FROM project_source").fetchone())
    store = ProjectStore(database)
    assert _texts(store.read_page(variant, reading_order=True)) == CURRENT
    with project_connection(database, write=True) as connection:
        connection.execute("DELETE FROM project_source")
        connection.execute("DELETE FROM source")
        columns = tuple(key for key in source if key != "id")
        new_id = connection.execute(
            f"INSERT INTO source ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            tuple(source[key] for key in columns),
        ).lastrowid
        assert new_id == source["id"]
        connection.execute(
            "INSERT INTO project_source VALUES (?, ?, ?)", (new_id, installed["namespace"], installed["ordinal"])
        )
        for entry in entries:
            columns = tuple(entry)
            connection.execute(
                f"INSERT INTO source_entry ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                tuple(entry[key] for key in columns),
            )
        connection.execute("UPDATE project SET revision = revision + 1")
    assert _saved(database)[0][refresh.METADATA_KEY][0] == 1
    assert _texts(store.read_page(variant, reading_order=True)) == CURRENT
    assert _saved(database)[0][refresh.METADATA_KEY][0] == 2
