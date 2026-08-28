from __future__ import annotations

import threading
from types import SimpleNamespace

from tests.application.terminology.story08_support import Permit, State, build, expected
from transbridge.ai_translator.batch_planner import Batch
from transbridge.ai_translator.project_terminology_adapter import (
    ProjectTerminologyAdapter,
    PublishedEffectiveTerminologyGate,
)
from transbridge.ai_translator.project_terminology_runtime import resolve_project_terminology
from transbridge.ai_translator.term_database import TermDatabaseManager
from transbridge.ai_translator.term_formats import TermEntry
from transbridge.ai_translator.translator import AutoTranslator, TranslationResult
from transbridge.application.terminology.effective import (
    EffectiveSnapshotStatus,
    SnapshotEffectiveTerminologyPort,
    TerminologyLookupContext,
)
from transbridge.application.terminology.identity import normalize_original, term_id
from transbridge.application.terminology.models import (
    DecisionStatus,
    DraftRef,
    TermDecision,
    TerminologyDraft,
    TermScope,
)
from transbridge.application.terminology.publish import PublishTerminologyRequest, VersionPublisher
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.persistence.terminology import (
    SqliteEffectiveTerminologySnapshotPort,
    SqliteTerminologyRepository,
)


def _decision(original: str, translation: str, scope: TermScope) -> TermDecision:
    return TermDecision(
        term_id(
            project_id="project-1",
            variant_id="variant-1",
            scope=scope,
            original=original,
        ),
        "project-1",
        "variant-1",
        original,
        normalize_original(original),
        translation,
        scope=scope,
        status=DecisionStatus.MANUAL_CONFIRMED,
    )


def _publish_scoped_version(repository: SqliteTerminologyRepository) -> None:
    source = build()
    decisions = (
        _decision("Dragon", "项目龙", TermScope.project()),
        _decision("Dragon", "插件龙", TermScope.plugin("plugin-a.esp")),
    )
    reviewed = TerminologyDraft(
        DraftRef("draft-scoped", "project-1", "variant-1", None, "no-base", 0, "scoped-decisions"),
        decisions,
    )
    state = expected(draft_id="draft-scoped")
    repository.put_build(source)
    repository.create_draft(reviewed)
    request = PublishTerminologyRequest(
        project_id="project-1",
        variant_id="variant-1",
        expected=state,
        build_ref=source.ref,
        draft_ref=reviewed.ref,
        version_id="version-scoped",
        published_at="2026-08-28T04:00:00+00:00",
    )
    VersionPublisher(repository.publisher, State(state), Permit()).publish(request)


def _manager(adapter: ProjectTerminologyAdapter) -> TermDatabaseManager:
    context = TerminologyLookupContext("project-1", "variant-1")
    manager = object.__new__(TermDatabaseManager)
    manager._effective_loader = adapter
    manager._terminology_context = context
    manager._legacy_terms = [TermEntry("Dragon", "旧龙", "dynamic")]
    projected = adapter.load(context, manager._legacy_terms)
    manager._merged_terms = list(projected.entries)
    manager._project_terminology_status = projected.status
    manager._retrieval_enabled = True
    manager._vector_index = None
    manager._dynamic_db = SimpleNamespace(as_list=lambda: [])
    manager._load_completed = True
    return manager


def _entry(key: str, plugin_id: str | None) -> TranslationEntry:
    return TranslationEntry(
        key,
        key,
        "Dragon",
        "",
        0,
        "NPC_:FULL",
        form_id_with_plugin=None if plugin_id is None else f"000001|{plugin_id}",
    )


def test_formal_publish_is_consumed_by_the_next_real_translator_batch_with_scope_isolation(tmp_path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    _publish_scoped_version(repository)
    repository.close()
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    snapshots = SqliteEffectiveTerminologySnapshotPort(repository)
    effective = SnapshotEffectiveTerminologyPort(snapshots)
    gate = PublishedEffectiveTerminologyGate(
        lambda project_id, variant_id: repository.effective_version(project_id, variant_id) is not None
    )
    manager = _manager(ProjectTerminologyAdapter(effective, gate))
    assert manager._project_terminology_status is EffectiveSnapshotStatus.READY

    global_entry = _entry("global", None)
    plugin_entry = _entry("plugin", "plugin-a.esp")
    collection = TranslationEntryCollection((global_entry, plugin_entry))
    accepted: list[dict[str, str]] = []

    class _CandidateSession:
        def accept(self, values, _collection):
            accepted.append(dict(values))
            return SimpleNamespace(accepted=len(values))

    translator = object.__new__(AutoTranslator)
    translator._term_mgr = manager
    translator._candidate_session = _CandidateSession()
    result = TranslationResult()

    translated = translator._run_batch(
        Batch([global_entry, plugin_entry], "其他"),
        collection,
        result,
        threading.Lock(),
    )

    assert translated == 2
    assert accepted == [{"global": "项目龙", "plugin": "插件龙"}]
    assert result.success_count == 2


def test_published_sqlite_version_flows_through_the_production_consumer_factory(tmp_path, monkeypatch) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    _publish_scoped_version(repository)
    effective = SnapshotEffectiveTerminologyPort(SqliteEffectiveTerminologySnapshotPort(repository))
    adapter = ProjectTerminologyAdapter(effective, PublishedEffectiveTerminologyGate(lambda *_identity: True))
    factory = SimpleNamespace(effective_adapter=lambda _project_id, _variant_id: adapter)
    binding = resolve_project_terminology(
        SimpleNamespace(
            active_version_identity=("project-1", "variant-1"),
            effective_terminology_factory=factory,
        )
    )
    monkeypatch.setattr(
        "transbridge.paratranz.config_manager.LLMConfig.get_ai_translator_dir",
        lambda _stem: str(tmp_path / "translator"),
    )
    config = SimpleNamespace(
        term_priority=(),
        retrieval_enabled=True,
        enable_semantic_match=False,
        embedding=SimpleNamespace(mode="disabled"),
    )
    manager = TermDatabaseManager(
        config=config,
        esp_path=str(tmp_path / "plugin-a.esp"),
        **binding.term_database_kwargs(),
    )

    manager.load_all()
    global_entry = _entry("global", None)
    plugin_entry = _entry("plugin", "plugin-a.esp")

    assert manager.match_terms_for_entry(global_entry) == {"Dragon": "项目龙"}
    assert manager.match_terms_for_entry(plugin_entry) == {"Dragon": "插件龙"}
    repository.close()
