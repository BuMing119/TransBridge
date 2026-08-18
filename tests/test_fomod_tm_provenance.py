from __future__ import annotations

import json
from pathlib import Path
import threading

import pytest

from transbridge.application.contracts import Diagnostic, OperationOutcome, RequestContext
from transbridge.application.fomod import (
    ArtifactRef,
    CandidateOrigin,
    CommitFomodCandidates,
    FomodCandidatePlanner,
    FomodCandidateSet,
    FomodTranslationCandidate,
    StageContext,
)
from transbridge.application.io import EntryKey, Provenance, SourceNamespace
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.fomod.stages import PluginTranslationSummary, TranslationStage
from transbridge.migrator import (
    KeyMigrationPlan,
    MigrationDisposition,
    MigrationEntry,
    plan_migration,
)
from transbridge.translation_memory import (
    TmConflictPolicy,
    TmMatchStatus,
    TranslationMemoryManager,
    TranslationMemoryQuery,
    TranslationMemoryQueryService,
    migrate_legacy_dictionary,
)


def _identity(local_key: str = "K1") -> EntryKey:
    return EntryKey(SourceNamespace("source:plugin:example"), local_key)


def _add_tm(
    manager: TranslationMemoryManager,
    *,
    dictionary: str,
    translation: str,
    target_locale: str = "zh_CN",
    fingerprint: str = "fp-new",
    scope: str = "project",
) -> None:
    key = _identity()
    manager.add(
        key.serialize(),
        "Hello",
        translation,
        mod_file_id=dictionary,
        scope=scope,
        source_locale="en_US",
        target_locale=target_locale,
        stage=1,
        provenance=(Provenance("seed", "tester", f"dictionary:{dictionary}"),),
        source_namespace=key.namespace.value,
        source_fingerprint=fingerprint,
    )


def _query(**changes) -> TranslationMemoryQuery:
    values = {
        "entry_key": _identity(),
        "original": "Hello",
        "source_locale": "en_US",
        "target_locale": "zh_CN",
        "stage": 0,
        "source_fingerprint": "fp-new",
    }
    values.update(changes)
    return TranslationMemoryQuery(**values)


def test_tm_query_is_locale_isolated_and_preserves_conflicts():
    manager = TranslationMemoryManager()
    _add_tm(manager, dictionary="zh-project", translation="你好")
    _add_tm(manager, dictionary="zh-global", translation="您好", scope="global")
    _add_tm(manager, dictionary="ja-project", translation="こんにちは", target_locale="ja_JP")
    service = TranslationMemoryQueryService(manager)

    result = service.query(_query())

    assert result.selected is None
    assert result.requires_confirmation
    assert {item.translation for item in result.candidates} == {"你好", "您好"}
    assert all(item.translation != "こんにちは" for item in result.candidates)
    assert any(item.code == "TM_CONFLICT_CONFIRMATION_REQUIRED" for item in result.diagnostics)


def test_tm_text_fallback_conflict_keeps_every_dictionary_candidate():
    manager = TranslationMemoryManager()
    key = _identity()
    for dictionary, translation in (("first", "你好"), ("second", "您好")):
        manager.add(
            "",
            "Hello",
            translation,
            mod_file_id=dictionary,
            source_locale="en_US",
            target_locale="zh_CN",
            stage=1,
            source_namespace=key.namespace.value,
            source_fingerprint="fp-new",
        )
    result = TranslationMemoryQueryService(manager).query(_query())
    assert result.selected is None
    assert {item.matched_via for item in result.candidates} == {"text"}
    assert {item.dictionary_id for item in result.candidates} == {"first", "second"}


def test_tm_cross_source_text_fallback_is_lower_priority_but_available():
    manager = TranslationMemoryManager()
    other = SourceNamespace("source:plugin:other")
    manager.add(
        "",
        "Hello",
        "跨来源译文",
        mod_file_id="shared",
        source_locale="en_US",
        target_locale="zh_CN",
        stage=1,
        source_namespace=other.value,
        source_fingerprint="other-fingerprint",
    )
    result = TranslationMemoryQueryService(manager).query(_query())
    assert result.selected is not None
    assert result.selected.match_status is TmMatchStatus.TEXT
    assert "cross_source_text_fallback" in result.selected.reasons


def test_tm_explicit_arbitration_and_stale_fingerprint():
    manager = TranslationMemoryManager()
    _add_tm(manager, dictionary="chosen", translation="你好", fingerprint="fp-old")
    service = TranslationMemoryQueryService(manager)

    stale = service.query(_query())
    assert stale.selected is None
    assert stale.candidates[0].match_status is TmMatchStatus.STALE
    assert "source_fingerprint_changed" in stale.candidates[0].reasons

    selected = service.query(
        _query(
            conflict_policy=TmConflictPolicy.EXPLICIT_DICTIONARY,
            selected_dictionary_id="chosen",
        )
    )
    assert selected.selected is not None
    assert selected.selected.dictionary_id == "chosen"


@pytest.mark.parametrize("stage", [-1, 9])
def test_tm_hidden_and_locked_never_auto_apply(stage):
    manager = TranslationMemoryManager()
    _add_tm(manager, dictionary="candidate", translation="你好")
    result = TranslationMemoryQueryService(manager).query(_query(stage=stage))
    assert result.selected is None
    assert not result.candidates
    assert result.blocks_publish is (stage == 9)


def test_legacy_locale_unknown_is_read_only_not_auto_enabled():
    manager = TranslationMemoryManager()
    manager.add("K1", "Hello", "你好", mod_file_id="legacy")
    result = TranslationMemoryQueryService(manager).query(_query())
    assert result.selected is None
    assert any(item.code == "TM_LEGACY_LOCALE_UNKNOWN" for item in result.diagnostics)
    assert manager.query("K1", "Hello").translation == "你好"


def test_key_migration_uses_namespace_mapping_and_marks_changed_fingerprint_stale():
    old_namespace = SourceNamespace("source:plugin:old")
    new_namespace = SourceNamespace("source:plugin:new")
    old = MigrationEntry(
        EntryKey(old_namespace, "K1"),
        "Hello",
        "你好",
        1,
        provenance=(Provenance("old", "tester", "old-archive"),),
    )
    new = MigrationEntry(EntryKey(new_namespace, "K1"), "Hello", "", 0)

    unmapped = plan_migration((old,), (new,), old_fingerprint="old", new_fingerprint="new")
    assert unmapped.unmatched == (new.key,)

    mapped = plan_migration(
        (old,),
        (new,),
        old_fingerprint="old",
        new_fingerprint="new",
        namespace_mappings=((old_namespace, new_namespace),),
    )
    assert mapped.candidates[0].disposition is MigrationDisposition.STALE
    assert "source_fingerprint_changed" in mapped.candidates[0].reasons


class FakeAi:
    def __init__(self) -> None:
        self.keys = []

    def translate(self, entry, *, target_locale, cancellation):
        del target_locale, cancellation
        self.keys.append(entry.entry_key)
        return "AI"


def _entry(key: EntryKey, *, stage: int = 0, translation: str = "") -> TranslationEntry:
    return TranslationEntry(
        id=key.local_key,
        key=key.local_key,
        original="Hello",
        translation=translation,
        stage=stage,
        context="NPC_:FULL",
        entry_key=key,
    )


def test_candidate_planner_ai_only_handles_unresolved_and_locked_empty_blocks():
    editable = _entry(_identity("editable"))
    hidden = _entry(_identity("hidden"), stage=-1)
    locked = _entry(_identity("locked"), stage=9)
    ai = FakeAi()
    planner = FomodCandidatePlanner(
        TranslationMemoryQueryService(TranslationMemoryManager()),
        ai=ai,
    )
    plan = planner.plan(
        run_id="run-1",
        entries=tuple(item.snapshot() for item in (editable, hidden, locked)),
        migration=KeyMigrationPlan((), (), ()),
        source_locale="en_US",
        target_locale="zh_CN",
        source_fingerprint="fp-new",
    )

    assert ai.keys == [editable.identity]
    assert plan.selected[0].origin is CandidateOrigin.AI
    assert plan.blockers[0].code == "STAGE_LOCKED_TRANSLATION_REQUIRED"


def test_translation_stage_turns_candidate_blocker_into_failed_stage(tmp_path: Path):
    class BlockedPort:
        def translate_plugins(self, new_root, old_root, *, run_id, target_locale, ai_enabled, cancellation):
            del new_root, old_root, run_id, target_locale, ai_enabled, cancellation
            return PluginTranslationSummary(
                publish_blockers=(
                    Diagnostic(
                        "FOMOD_TRANSLATION_CONFIRMATION_REQUIRED",
                        "confirmation required",
                    ),
                )
            )

    from transbridge.application.fomod import DirectCommitGuard, FomodRunSpec

    source = tmp_path / "source"
    source.mkdir()
    migration = tmp_path / "migration.json"
    migration.write_text("[]", encoding="utf-8")
    archive = tmp_path / "input.zip"
    archive.write_bytes(b"archive")
    spec = FomodRunSpec(
        "blocked",
        str(archive),
        "hash",
        str(tmp_path / "out.zip"),
        "zh_CN",
        "config",
        workspace_root=str(tmp_path),
    )
    context = StageContext(
        spec,
        tmp_path,
        {
            "new_root": ArtifactRef("new_root", "mod-root", str(source)),
            "migration_plan": ArtifactRef("migration_plan", "migration-plan", str(migration)),
        },
        None,
        DirectCommitGuard(),
    )

    result = TranslationStage(BlockedPort()).execute(context)
    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].code == "FOMOD_TRANSLATION_CONFIRMATION_REQUIRED"


class CountingMutationPort:
    def __init__(self, collection: TranslationEntryCollection) -> None:
        self.collection = collection
        self.calls = 0

    @property
    def collection_revision(self):
        return self.collection.collection_revision

    def snapshot(self, entry_key):
        return self.collection.snapshot(entry_key)

    def apply(self, change_set, context):
        self.calls += 1
        return self.collection.apply(change_set, context)

    def legacy_mapping_report(self):
        return self.collection.legacy_mapping_report()


def test_candidate_set_commits_once_with_provenance_and_cancel_is_zero_mutation():
    first = _entry(_identity("first"))
    second = _entry(_identity("second"))
    collection = TranslationEntryCollection((first, second))
    port = CountingMutationPort(collection)
    provenance = (Provenance("run-1", "planner", "dictionary:test"),)
    candidates = FomodCandidateSet(
        "run-1",
        selected=tuple(
            FomodTranslationCandidate(
                item.identity,
                item.revision,
                f"translated-{item.key}",
                1,
                CandidateOrigin.TRANSLATION_MEMORY,
                provenance,
            )
            for item in (first, second)
        ),
    )
    context = RequestContext(
        "owner",
        run_id="run-1",
        permissions=frozenset({"entry.translation.write", "entry.stage.write"}),
    )

    report = CommitFomodCandidates(port).execute(candidates, context)
    assert report.outcome is OperationOutcome.COMPLETED
    assert port.calls == 1
    assert collection.get(first.identity).provenance[-1].source == "fomod-candidate-set"

    cancelled = threading.Event()
    cancelled.set()
    report = CommitFomodCandidates(port).execute(candidates, context, cancelled)
    assert report.outcome is OperationOutcome.CANCELLED
    assert port.calls == 1

    class RejectGuard:
        def commit(self, run_id, mutation):
            del run_id, mutation
            return False

    report = CommitFomodCandidates(port, commit_guard=RejectGuard()).execute(candidates, context)
    assert report.outcome is OperationOutcome.CANCELLED
    assert port.calls == 1


def test_legacy_tm_migration_is_atomic_backed_up_and_unknown_locale_disabled(tmp_path: Path):
    source = tmp_path / "Legacy.tbdict"
    source.write_text(
        json.dumps({
            "schema_version": 1,
            "mod_file_id": "Legacy",
            "scope": "global",
            "entries": {"one": {"original": "Hello", "translation": "你好"}},
            "key_index": {"K1": {"entry_id": "one", "hits": 0}},
            "text_index": {"Hello": {"entry_id": "one", "hits": 0}},
        }),
        encoding="utf-8",
    )
    original = source.read_bytes()

    report = migrate_legacy_dictionary(source, run_id="migration-1")

    assert Path(report.backup).read_bytes() == original
    migrated = json.loads(source.read_text(encoding="utf-8"))
    assert migrated["schema_version"] == 2
    assert migrated["entries"]["one"]["enabled"] is False
    assert report.disabled == 1
    assert report.diagnostics[0].code == "TM_MIGRATION_LOCALE_UNKNOWN"


def test_legacy_tm_migration_failure_keeps_original_and_backup(tmp_path: Path, monkeypatch):
    source = tmp_path / "Legacy.tbdict"
    source.write_text(
        json.dumps({
            "schema_version": 1,
            "mod_file_id": "Legacy",
            "entries": {},
        }),
        encoding="utf-8",
    )
    original = source.read_bytes()
    import transbridge.translation_memory.migration as migration_module

    def fail_write(path, payload):
        del path, payload
        raise OSError("disk full")

    monkeypatch.setattr(migration_module, "_atomic_write_json", fail_write)
    with pytest.raises(OSError, match="disk full"):
        migrate_legacy_dictionary(source, run_id="migration-failed")
    assert source.read_bytes() == original
    assert (tmp_path / "Legacy.tbdict.v1.bak").read_bytes() == original


def test_cancelled_query_and_migration_produce_no_candidate():
    cancellation = threading.Event()
    cancellation.set()
    manager = TranslationMemoryManager()
    _add_tm(manager, dictionary="candidate", translation="你好")
    query = TranslationMemoryQueryService(manager).query(_query(), cancellation)
    migration = plan_migration(
        (),
        (),
        old_fingerprint="old",
        new_fingerprint="new",
        cancellation=cancellation,
    )
    assert query.cancelled and not query.candidates
    assert migration.cancelled and not migration.candidates
