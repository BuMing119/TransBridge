from __future__ import annotations

import threading
from types import SimpleNamespace

from transbridge.ai_translator.project_terminology_adapter import (
    ProjectTerminologyAdapter,
    PublishedEffectiveTerminologyGate,
    plugin_id_from_entry,
)
from transbridge.ai_translator.term_database import TermDatabaseManager
from transbridge.ai_translator.term_formats import TermEntry
from transbridge.ai_translator.term_vector_index import TermVectorIndex
from transbridge.application.io.identity import EntryKey, EntryRevision, SourceNamespace
from transbridge.application.terminology.effective import (
    EffectiveSnapshotStatus,
    EffectiveTerminologySnapshot,
    SnapshotEffectiveTerminologyPort,
    TerminologyLookupContext,
)
from transbridge.application.terminology.identity import normalize_original, term_id
from transbridge.application.terminology.models import DecisionStatus, TermDecision, TermScope
from transbridge.application.translation import TranslationInput


def _decision(original, translation, *, scope=None, status=DecisionStatus.ADOPTED, suppressed=False):
    resolved_scope = scope or TermScope.project()
    return TermDecision(
        term_id(
            project_id="project-1",
            variant_id="variant-1",
            scope=resolved_scope,
            original=original,
        ),
        "project-1",
        "variant-1",
        original,
        normalize_original(original),
        translation,
        scope=resolved_scope,
        status=status,
        suppressed=suppressed,
    )


class _Snapshots:
    def __init__(self, snapshot):
        self.value = snapshot
        self.calls = 0

    def snapshot(self, project, variant, version_id=None):
        self.calls += 1
        assert (project, variant, version_id) == ("project-1", "variant-1", None)
        return self.value


def _adapter(snapshot, *, enabled=True):
    source = _Snapshots(snapshot)
    gate = PublishedEffectiveTerminologyGate(lambda _project, _variant: enabled)
    return ProjectTerminologyAdapter(SnapshotEffectiveTerminologyPort(source), gate), source


def _ready(*decisions):
    return EffectiveTerminologySnapshot(
        "project-1",
        "variant-1",
        EffectiveSnapshotStatus.READY,
        version_id="version-1",
        content_digest="content-1",
        decisions=decisions,
    )


def test_default_off_and_no_version_preserve_legacy_entries_exactly():
    no_version = EffectiveTerminologySnapshot("project-1", "variant-1", EffectiveSnapshotStatus.NO_PROJECT_VERSION)
    legacy = (TermEntry("Sword", "旧剑", "dynamic"),)
    adapter = ProjectTerminologyAdapter(SnapshotEffectiveTerminologyPort(_Snapshots(no_version)))

    disabled = adapter.load(TerminologyLookupContext("project-1", "variant-1"), legacy)
    assert disabled.entries == legacy
    assert disabled.status is None
    assert disabled.snapshot_identity == "legacy-global"

    enabled, _ = _adapter(no_version)
    fallback = enabled.load(TerminologyLookupContext("project-1", "variant-1"), legacy)
    assert fallback.entries == legacy
    assert fallback.status is EffectiveSnapshotStatus.NO_PROJECT_VERSION
    assert fallback.snapshot_identity == "legacy-global"


def test_published_version_probe_failure_fails_gate_off_to_legacy():
    def unavailable(_project, _variant):
        raise OSError("repository unavailable")

    no_version = EffectiveTerminologySnapshot("project-1", "variant-1", EffectiveSnapshotStatus.NO_PROJECT_VERSION)
    adapter = ProjectTerminologyAdapter(
        SnapshotEffectiveTerminologyPort(_Snapshots(no_version)),
        PublishedEffectiveTerminologyGate(unavailable),
    )
    legacy = (TermEntry("Sword", "旧剑", "dynamic"),)

    loaded = adapter.load(TerminologyLookupContext("project-1", "variant-1"), legacy)

    assert loaded.entries == legacy
    assert loaded.status is None


def test_corrupt_version_preserves_legacy_translation_with_diagnostics():
    corrupt = EffectiveTerminologySnapshot(
        "project-1",
        "variant-1",
        EffectiveSnapshotStatus.CORRUPT,
        diagnostics=("VERSION_DIGEST_MISMATCH",),
    )
    adapter, _ = _adapter(corrupt)
    legacy = (TermEntry("Sword", "旧剑", "dynamic"),)

    loaded = adapter.load(TerminologyLookupContext("project-1", "variant-1"), legacy)

    assert loaded.entries == legacy
    assert loaded.status is EffectiveSnapshotStatus.CORRUPT
    assert loaded.snapshot_identity == "legacy-global"
    assert loaded.diagnostics == ("VERSION_DIGEST_MISMATCH",)


def test_ready_review_and_unresolved_decisions_cannot_reintroduce_legacy_terms():
    review = _decision("Sword", "待定剑", status=DecisionStatus.REVIEW_REQUIRED)
    unresolved = _decision("Shield", "待定盾", status=DecisionStatus.UNRESOLVED)
    adapter, _ = _adapter(_ready(review, unresolved))
    legacy = (TermEntry("Sword", "旧剑", "dynamic"), TermEntry("Shield", "旧盾", "json"))

    loaded = adapter.load(TerminologyLookupContext("project-1", "variant-1"), legacy)

    assert loaded.entries == ()
    assert adapter.resolve("Sword", TerminologyLookupContext("project-1", "variant-1"), lambda _term: legacy[0]) is None
    assert (
        adapter.resolve("Shield", TerminologyLookupContext("project-1", "variant-1"), lambda _term: legacy[1]) is None
    )


def test_global_compatibility_never_leaks_plugin_special_and_context_resolve_overrides():
    global_entry = _decision("Sword", "项目剑")
    plugin_entry = _decision("Sword", "插件剑", scope=TermScope.plugin("plugin-a.esp"))
    adapter, _ = _adapter(_ready(global_entry, plugin_entry))
    legacy = (TermEntry("Sword", "旧剑", "dynamic"), TermEntry("Shield", "旧盾", "json"))
    base = TerminologyLookupContext("project-1", "variant-1")

    loaded = adapter.load(base, legacy)
    assert {entry.term: entry.translation for entry in loaded.entries} == {
        "Sword": "项目剑",
        "Shield": "旧盾",
    }
    assert adapter.resolve("Sword", base, lambda _term: legacy[0]).translation == "项目剑"
    assert adapter.resolve("Sword", base.for_plugin("plugin-a.esp"), lambda _term: legacy[0]).translation == "插件剑"


def test_term_database_contextual_facade_scopes_plugin_entries_and_blocks_suppressed_legacy():
    global_entry = _decision("Sword", "项目剑")
    plugin_entry = _decision("Sword", "插件剑", scope=TermScope.plugin("plugin-a.esp"))
    suppressed = _decision("Shield", "盾", suppressed=True)
    adapter, _ = _adapter(_ready(global_entry, plugin_entry, suppressed))
    base = TerminologyLookupContext("project-1", "variant-1")
    legacy = [TermEntry("Sword", "旧剑", "dynamic"), TermEntry("Shield", "旧盾", "json")]
    manager = object.__new__(TermDatabaseManager)
    manager._effective_loader = adapter
    manager._terminology_context = base
    manager._legacy_terms = legacy
    manager._merged_terms = list(adapter.load(base, legacy).entries)
    manager._project_terminology_status = EffectiveSnapshotStatus.READY
    manager._retrieval_enabled = True
    manager._vector_index = None
    manager._dynamic_db = SimpleNamespace(as_list=lambda: [])

    plugin_entry_context = SimpleNamespace(
        key="plugin-entry",
        original="Sword and Shield",
        form_id_with_plugin="000001|plugin-a.esp",
    )
    global_entry_context = SimpleNamespace(key="global-entry", original="Sword", form_id_with_plugin=None)

    assert manager.resolve_term("Sword").translation == "项目剑"
    assert manager.resolve_term("Shield") is None
    assert manager.match_terms_for_entry(plugin_entry_context) == {"Sword": "插件剑"}
    assert manager.match_terms_for_entry(global_entry_context) == {"Sword": "项目剑"}


def test_vector_hash_binds_project_variant_version_digest_and_scope_identity():
    index = object.__new__(TermVectorIndex)
    terms = [TermEntry("Sword", "剑", "project-terminology")]

    global_hash = index._compute_term_hash(terms, "project-1:variant-1:version-1:content-1:project")
    plugin_hash = index._compute_term_hash(terms, "project-1:variant-1:version-1:content-1:plugin:a.esp")

    assert global_hash != plugin_hash


def test_plugin_semantic_lookup_rebuilds_and_searches_the_scope_bound_index():
    global_entry = _decision("Sword", "项目剑")
    plugin_entry = _decision("Sword", "插件剑", scope=TermScope.plugin("plugin-a.esp"))
    adapter, _ = _adapter(_ready(global_entry, plugin_entry))
    base = TerminologyLookupContext("project-1", "variant-1")
    manager = object.__new__(TermDatabaseManager)
    manager._effective_loader = adapter
    manager._terminology_context = base
    manager._legacy_terms = []
    manager._merged_terms = list(adapter.load(base, ()).entries)
    manager._load_completed = True
    manager._dynamic_db = SimpleNamespace(as_list=lambda: [])
    manager._vector_snapshot_identity = adapter.snapshot_identity(base)
    manager._vector_lock = threading.RLock()

    class _Index:
        available = True

        def __init__(self):
            self.translation = "项目剑"

        def build_index(self, entries, *, force, snapshot_identity):
            assert force and snapshot_identity.endswith(":plugin:plugin-a.esp")
            self.translation = next(item.translation for item in entries if item.term == "Sword")
            return True

        def search_batch(self, texts, *, top_k):
            assert texts == ["weapon"] and top_k == 3
            return {"weapon": (SimpleNamespace(term="Sword", translation=self.translation),)}

    manager._vector_index = _Index()

    assert manager.semantic_match(["weapon"], top_k=3, context=base.for_plugin("plugin-a.esp")) == {"Sword": "插件剑"}


def test_postprocess_report_details_preserve_plugin_lookup_context() -> None:
    candidate = SimpleNamespace(
        form_id_with_plugin=None,
        report_details=(("terminology_plugin_id", "plugin-a.esp"),),
    )

    assert plugin_id_from_entry(candidate) == "plugin-a.esp"


def test_translation_input_only_extends_legacy_fingerprint_payload_for_plugin_context() -> None:
    key = EntryKey(SourceNamespace.legacy(), "entry-1")
    legacy = TranslationInput(key, EntryRevision(), "Sword", "", 0)
    plugin = TranslationInput(key, EntryRevision(), "Sword", "", 0, terminology_plugin_id="plugin-a.esp")

    assert "terminology_plugin_id" not in legacy.to_dict()
    assert plugin.to_dict()["terminology_plugin_id"] == "plugin-a.esp"
