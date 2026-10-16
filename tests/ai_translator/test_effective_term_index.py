from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from tests.ai_translator.test_project_terminology_adapter import _adapter, _decision, _ready
from transbridge.ai_translator.term_formats import TermEntry
from transbridge.application.terminology.effective import (
    EffectiveTerminologyIndex,
    TerminologyLookupContext,
    resolve_snapshot,
)
from transbridge.application.terminology.models import DecisionStatus, TermScope


@pytest.mark.parametrize("plugin", [None, "a.esp", "b.esp"])
def test_index_preserves_scope_shadowing_aliases_and_statuses(plugin):
    global_term = replace(_decision("Sword", "剑"), variants=("Blade",))
    plugin_term = _decision("Sword", "刀", scope=TermScope.plugin("a.esp"))
    suppressed = _decision("Shield", "盾", scope=TermScope.plugin("a.esp"), suppressed=True)
    review = _decision("Bow", "弓", status=DecisionStatus.REVIEW_REQUIRED)
    snapshot = _ready(global_term, plugin_term, suppressed, review)
    context = TerminologyLookupContext("project-1", "variant-1", plugin)
    index = EffectiveTerminologyIndex(snapshot)

    for term in (" Sword ", "SHIELD", "Bow", "Blade", "Missing", ""):
        assert index.resolve(term, context) == resolve_snapshot(snapshot, term, context)
    adapter, _ = _adapter(snapshot)
    merged = adapter.load(context, (TermEntry("Shield", "旧盾", "legacy"),)).entries
    assert next(item for item in merged if item.term == "Sword").translation == ("刀" if plugin == "a.esp" else "剑")
    assert ("Shield" in {item.term for item in merged}) is (plugin != "a.esp")


def test_index_rejects_a_different_project_context():
    index = EffectiveTerminologyIndex(_ready(_decision("Sword", "剑")))
    with pytest.raises(ValueError, match="context"):
        index.resolve("Sword", TerminologyLookupContext("another", "variant-1"))


def test_adapter_refreshes_versions_and_does_not_cache_mutable_legacy_terms():
    adapter, source = _adapter(_ready(_decision("Sword", "剑")))
    context = TerminologyLookupContext("project-1", "variant-1")
    legacy = TermEntry("Shield", "旧盾", "dynamic")
    first = adapter.load(context, (legacy,))
    source.value = replace(_ready(_decision("Sword", "刀")), version_id="version-2", content_digest="content-2")
    legacy.translation = "新盾"
    second = adapter.load(context, (legacy, TermEntry("Bow", "弓", "dynamic")))

    assert first.snapshot_identity != second.snapshot_identity
    assert {entry.term: entry.translation for entry in second.entries} == {"Sword": "刀", "Shield": "新盾", "Bow": "弓"}


def test_concurrent_plugin_lookups_cannot_share_another_scopes_resolution():
    adapter, _ = _adapter(
        _ready(
            _decision("Sword", "剑"),
            _decision("Sword", "刀", scope=TermScope.plugin("a.esp")),
        )
    )
    base = TerminologyLookupContext("project-1", "variant-1")

    def read(plugin):
        return adapter.load(base.for_plugin(plugin), ()).entries[0].translation

    plugins = [None, "a.esp", "b.esp"] * 10
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(read, plugins)) == ["刀" if plugin == "a.esp" else "剑" for plugin in plugins]


def test_project_merge_does_not_compare_every_term_against_the_entire_snapshot():
    class CountedOriginal(str):
        comparisons = 0
        __hash__ = str.__hash__

        def __eq__(self, other):
            type(self).comparisons += 1
            return str.__eq__(self, other)

    size = 1500
    decisions = tuple(
        replace(_decision(f"Term{i:05d}", f"译词{i}"), normalized_original=CountedOriginal(f"term{i:05d}"))
        for i in range(size)
    )
    adapter, _ = _adapter(_ready(*decisions))
    context = TerminologyLookupContext("project-1", "variant-1")
    CountedOriginal.comparisons = 0

    assert len(adapter.load(context, ()).entries) == size
    assert len(adapter.load(context.for_plugin("a.esp"), ()).entries) == size
    # A full scan per candidate performs millions of equality comparisons here.
    assert CountedOriginal.comparisons < size * 10
