from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId
from transbridge.application.projects import (
    ProjectCatalogEntry,
    ProjectCatalogSnapshot,
    ProjectProvisioningRequest,
    ProjectSourceRequest,
)
from transbridge.application.projects.source_registry import (
    BilingualCapability,
    SourceKind,
    SourceRegistration,
    SourceRegistrySnapshot,
)
from transbridge.application.terminology import DecisionStatus, ScopeKind, TermDecision, TermScope
from transbridge.bootstrap.history_search import (
    DictionaryHistoryProvider,
    ProjectVariantHistoryProvider,
    TerminologyHistoryProvider,
)
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.persistence.terminology import TerminologyPaths
from transbridge.persistence.v2.ids import EntityKind
from transbridge.persistence.v2.models import ProjectDto, SchemaEnvelope


def test_dictionary_provider_reads_valid_files_and_does_not_mutate_corrupt_files(tmp_path) -> None:
    valid = tmp_path / "Skyrim.tbdict"
    valid.write_text(
        json.dumps({
            "schema_version": 2,
            "mod_file_id": "Skyrim",
            "scope": "global",
            "entries": {
                "entry": {
                    "original": "Dragon",
                    "translation": "龙",
                    "source_locale": "en",
                    "target_locale": "zh-CN",
                    "enabled": True,
                },
                "legacy": {
                    "original": "Legacy",
                    "translation": "旧译文",
                    "enabled": False,
                },
            },
        }),
        encoding="utf-8",
    )
    corrupt = tmp_path / "Broken.tbdict"
    original_corrupt = "{broken"
    corrupt.write_text(original_corrupt, encoding="utf-8")

    result = DictionaryHistoryProvider(tmp_path).collect(None)

    assert [(item.original, item.translation) for item in result.records] == [
        ("Dragon", "龙"),
        ("Legacy", "旧译文"),
    ]
    assert result.records[1].status.endswith("disabled")
    assert result.diagnostics[0].code == "HISTORY_DICTIONARY_INVALID"
    assert corrupt.read_text(encoding="utf-8") == original_corrupt
    assert sorted(path.name for path in tmp_path.iterdir()) == ["Broken.tbdict", "Skyrim.tbdict"]


class _Catalog:
    def list_projects(self):
        return ProjectCatalogSnapshot((ProjectCatalogEntry("project-1", "天际工程", "project.json", False, True),))


class _Repository:
    def __init__(self, value):
        self.value = value

    def read_snapshot(self, _ref):
        return self.value


def _project(source: SourceRegistration, variant_id: str = "variant-1") -> ProjectDto:
    return ProjectDto(
        SchemaEnvelope(
            3,
            EntityKind.PROJECT,
            "project-1",
            1,
            {
                "name": "天际工程",
                **SourceRegistrySnapshot((source,)).to_project_data(),
                "variant_ids": [variant_id],
                "variant_names": {variant_id: "主线"},
                "active_variant_id": variant_id,
            },
        )
    )


@pytest.fixture
def history_project(tmp_path):
    source = tmp_path / "source.json"
    shared = {"form_id": "00000800|Skyrim.esm", "type": "QUST CNAM"}
    source.write_text(
        json.dumps([
            {**shared, "original": "Skyrim", "string": "天际"},
            {**shared, "original": "Tamriel", "string": "泰姆瑞尔"},
            {"form_id": "00000900|Skyrim.esm", "type": "INFO NAM1", "string": "原文未知的译文"},
        ]),
        encoding="utf-8",
    )
    root = tmp_path / "state"

    context = RequestContext("history-test")
    writer = _history_services(root)
    try:
        created = writer.gui_project_commands.create_project(
            ProjectProvisioningRequest("天际工程", "主线", ProjectSourceRequest(str(source), FormatId.JSON_DSD)),
            context,
        )
        assert created.is_success, created.diagnostics
        active = writer.project_lifecycle.active
        path = Path(writer.projects.path_for(active.project_ref))
        store = writer.projects.store_for(active.project_ref.identity)
        rows = store.read_page(active.formal_variant_ref.identity)
        assert len(rows) == 3
        assert rows[0].state.entry_key.local_key == rows[1].state.entry_key.local_key
        assert [row.snapshot.original for row in rows] == ["Skyrim", "Tamriel", ""]
        keys = [row.state.entry_key.serialize() for row in rows]
    finally:
        writer.close()

    reader = _history_services(root)
    try:
        yield reader, source, path, keys
    finally:
        reader.close()


def _history_services(root):
    return build_persistence_v2_services(root, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now")


@pytest.mark.parametrize("remove_input", [False, True])
def test_project_provider_reads_exact_saved_entry_keys_without_reading_input(history_project, remove_input) -> None:
    services, source, path, keys = history_project
    replacement = b"invalid replacement input"
    if remove_input:
        source.unlink()
    else:
        source.write_bytes(replacement)
    before = path.read_bytes()

    result = ProjectVariantHistoryProvider(services.project_catalog, services.projects).collect(None)

    assert not result.diagnostics
    assert [(item.original, item.translation) for item in result.records] == [
        ("Skyrim", "天际"),
        ("Tamriel", "泰姆瑞尔"),
    ]
    assert [dict(item.source.details)["entry_key"] for item in result.records] == keys[:2]
    assert len({item.source.source_id for item in result.records}) == 2
    assert {item.source.project_name for item in result.records} == {"天际工程"}
    assert {item.source.variant_name for item in result.records} == {"主线"}
    assert services.project_lifecycle.active is None
    assert path.read_bytes() == before
    if remove_input:
        assert not source.exists()
    else:
        assert source.read_bytes() == replacement


def test_project_provider_does_not_use_translation_as_missing_original(history_project) -> None:
    services, _source, _path, keys = history_project
    result = ProjectVariantHistoryProvider(services.project_catalog, services.projects).collect(None)

    assert not result.diagnostics
    assert len(result.records) == 2
    assert all("原文未知的译文" not in (item.original, item.translation) for item in result.records)
    assert keys[2] not in {dict(item.source.details)["entry_key"] for item in result.records}


def test_project_provider_indexes_unfolded_migration_source_translations(tmp_path) -> None:
    primary = tmp_path / "main.json"
    migration = tmp_path / "migration.json"
    expected = [("Main quest", "主线任务"), ("Imported quest", "导入任务")]
    for path, (original, translation) in zip((primary, migration), expected, strict=True):
        path.write_text(
            json.dumps([
                {
                    "form_id": "00000800|Skyrim.esm",
                    "type": "QUST CNAM",
                    "original": original,
                    "string": translation,
                }
            ]),
            encoding="utf-8",
        )
    services = _history_services(tmp_path / "state")
    try:
        created = services.gui_project_commands.create_project(
            ProjectProvisioningRequest(
                "工程",
                source=ProjectSourceRequest(str(primary), FormatId.JSON_DSD),
                migration_sources=(ProjectSourceRequest(str(migration), FormatId.JSON_DSD),),
            ),
            RequestContext("history-test"),
        )
        assert created.is_success, created.diagnostics
        active = services.project_lifecycle.active
        store = services.projects.store_for(active.project_ref.identity)
        rows = store.read_page(active.formal_variant_ref.identity)
        assert [(row.snapshot.original, row.state.translation) for row in rows] == expected

        result = ProjectVariantHistoryProvider(services.project_catalog, services.projects).collect(None)

        assert not result.diagnostics
        assert [(row.original, row.translation) for row in result.records] == expected
    finally:
        services.close()


def test_terminology_provider_reads_only_effective_decisions_and_preserves_scope(tmp_path, monkeypatch) -> None:
    source = SourceRegistration(
        "source-1",
        False,
        FormatId.JSON_TRANSBRIDGE,
        str(tmp_path / "unused.json"),
        SourceKind.BILINGUAL,
        BilingualCapability.SELF_CONTAINED,
    )
    project = _project(source)
    database = TerminologyPaths(tmp_path).database("project-1")
    database.parent.mkdir(parents=True)
    database.write_bytes(b"placeholder")
    decisions = (
        TermDecision(
            "term-1",
            "project-1",
            "variant-1",
            "Dragon",
            "dragon",
            "龙",
            status=DecisionStatus.ADOPTED,
        ),
        TermDecision(
            "term-2",
            "project-1",
            "variant-1",
            "Dragonborn",
            "dragonborn",
            "龙裔",
            scope=TermScope(ScopeKind.PLUGIN, "Skyrim.esm"),
            status=DecisionStatus.MANUAL_CONFIRMED,
        ),
        TermDecision(
            "term-3",
            "project-1",
            "variant-1",
            "Draft",
            "draft",
            "草稿",
            status=DecisionStatus.REVIEW_REQUIRED,
        ),
    )

    class TerminologyRepository:
        closed = False

        def effective_version(self, _project_id, _variant_id):
            return SimpleNamespace(ref=SimpleNamespace(version_id="version-1"), decisions=decisions)

        def close(self):
            self.closed = True

    repository = TerminologyRepository()
    monkeypatch.setattr(
        "transbridge.bootstrap.history_search.SqliteTerminologyRepository.open",
        lambda *_args, **_kwargs: repository,
    )

    result = TerminologyHistoryProvider(tmp_path, _Catalog(), _Repository(project)).collect(None)

    assert [item.original for item in result.records] == ["Dragon", "Dragonborn"]
    assert {item.scope_key for item in result.records} == {
        "project:project-1:project",
        "project:project-1:plugin:Skyrim.esm",
    }
    assert repository.closed
