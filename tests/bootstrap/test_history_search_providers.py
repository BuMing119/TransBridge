from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from transbridge.application.io import (
    EntryKey,
    FormatId,
    SourceDescriptor,
    SourceNamespace,
    SourceSnapshot,
    Stage,
)
from transbridge.application.projects import ProjectCatalogEntry, ProjectCatalogSnapshot
from transbridge.application.projects.prepared_source_results import prepare_parsed_source
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
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.persistence.source_artifact_store import ProjectSourceArtifactStore
from transbridge.persistence.terminology import TerminologyPaths
from transbridge.persistence.v2.ids import EntityKind, ProjectId, VariantId, VariantRef
from transbridge.persistence.v2.models import ProjectDto, SchemaEnvelope
from transbridge.persistence.v2.variant import SourceFingerprint, VariantEntryState, VariantSnapshot


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


@pytest.mark.parametrize("clear_reference", [False, True])
def test_project_provider_restores_original_by_exact_entry_key(tmp_path, clear_reference) -> None:
    payload = b"source"
    digest = sha256(payload).hexdigest()
    namespace = SourceNamespace.from_fingerprint(FormatId.JSON_TRANSBRIDGE.value, digest)
    key = EntryKey(namespace, "record-1")
    path = tmp_path / "source.json"
    path.write_bytes(payload)
    parsed_entry = TranslationEntry("record-1", "record-1", "Skyrim", "", 0, None, entry_key=key)
    snapshot = SourceSnapshot.from_bytes(SourceDescriptor(str(path)), FormatId.JSON_TRANSBRIDGE, payload)
    saved = prepare_parsed_source(str(path), (parsed_entry,), snapshot, FormatId.JSON_TRANSBRIDGE)
    artifacts = ProjectSourceArtifactStore(str(tmp_path / "state"))
    artifact_id = artifacts.put(saved)
    source = SourceRegistration(
        "source-1",
        True,
        FormatId.JSON_TRANSBRIDGE,
        str(path),
        SourceKind.BILINGUAL,
        BilingualCapability.SELF_CONTAINED,
        fingerprint=digest,
        display_name="source.json",
        source_artifact_id=artifact_id,
    )
    project = _project(source)
    variant_ref = VariantRef(VariantId("variant-1"), ProjectId("project-1"))
    variant = VariantSnapshot(
        variant_ref,
        (SourceFingerprint(namespace, digest),),
        (VariantEntryState(key, "天际", Stage.TRANSLATED),),
    ).to_dto()

    class Io:
        def parse(self, request):
            raise AssertionError("history must use saved source entries")

    path.unlink()
    if clear_reference:
        source = replace(source, location="")
        project = _project(source)

    result = ProjectVariantHistoryProvider(
        _Catalog(),
        _Repository(project),
        _Repository(variant),
        Io(),
        artifacts=artifacts,
    ).collect(None)

    assert [(item.original, item.translation) for item in result.records] == [("Skyrim", "天际")]
    assert result.records[0].source.project_name == "天际工程"
    assert result.records[0].source.variant_name == "主线"

    missing_key = EntryKey(namespace, "missing-record")
    unmatched_variant = VariantSnapshot(
        variant_ref,
        (SourceFingerprint(namespace, digest),),
        (VariantEntryState(missing_key, "未对齐译文", Stage.TRANSLATED),),
    ).to_dto()
    unmatched = ProjectVariantHistoryProvider(
        _Catalog(),
        _Repository(project),
        _Repository(unmatched_variant),
        Io(),
        artifacts=artifacts,
    ).collect(None)
    assert not unmatched.records
    assert any(item.code == "HISTORY_ENTRY_UNMATCHED" for item in unmatched.diagnostics)

    missing_saved = ProjectVariantHistoryProvider(
        _Catalog(),
        _Repository(_project(replace(source, source_artifact_id=None))),
        _Repository(variant),
        Io(),
        artifacts=artifacts,
    ).collect(None)
    assert not missing_saved.records
    assert any(item.code == "HISTORY_SOURCE_PARSE_FAILED" for item in missing_saved.diagnostics)


def test_saved_original_in_identity_remains_searchable_without_a_source_artifact(tmp_path) -> None:
    source = SourceRegistration(
        "source-1", True, FormatId.XML_EET, "", SourceKind.BILINGUAL, BilingualCapability.SELF_CONTAINED
    )
    namespace = SourceNamespace.legacy()
    variant = VariantSnapshot(
        VariantRef(VariantId("variant-1"), ProjectId("project-1")),
        (SourceFingerprint(namespace, None),),
        (
            VariantEntryState(EntryKey(namespace, "known", "Saved original"), "已存译文", Stage.TRANSLATED),
            VariantEntryState(EntryKey(namespace, "unknown"), "原文未知的译文", Stage.TRANSLATED),
        ),
    )
    result = ProjectVariantHistoryProvider(
        _Catalog(),
        _Repository(_project(source)),
        _Repository(variant.to_dto()),
        artifacts=ProjectSourceArtifactStore(str(tmp_path)),
    ).collect(None)
    assert [(row.original, row.translation) for row in result.records] == [("Saved original", "已存译文")]
    assert any(row.code == "HISTORY_ENTRY_UNMATCHED" for row in result.diagnostics)


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
