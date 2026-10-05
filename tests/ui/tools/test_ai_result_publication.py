"""Bulk publication preserves entry contracts and rejects invalid decisions atomically."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from transbridge.application.io.identity import EntryKey, ExternalEntryRef, SourceNamespace
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.tools.ai_translator.task_entry_results import TaskEntryResults


def make_results(count=3):
    namespace = SourceNamespace("test:ai-result-publication")
    entries = tuple(
        TranslationEntry(
            str(index),
            str(index),
            f"original {index}",
            f"before {index}",
            1,
            "INFO:NAM1",
            entry_key=EntryKey(namespace, str(index)),
            external_refs=(ExternalEntryRef("paratranz", "test", index),),
            metadata=(("source", "preserved"),),
        )
        for index in range(count)
    )
    collection = TranslationEntryCollection(entries)
    task = SimpleNamespace(key="source", collection=collection, translate_entries=(), polish_entries=entries)
    results = TaskEntryResults((task,), "test-run")
    results.entries = {
        key: replace(row, status="succeeded", text=f"after {row.before.id}", stage=3, decision="accepted")
        for key, row in results.entries.items()
    }
    return task, results


def test_bulk_publish_preserves_identity_and_updates_selected_entries_once(monkeypatch):
    task, results = make_results(500)
    originals = tuple(task.collection)
    rejected, failed = originals[-2:]
    results.entries[rejected.identity] = replace(results.entries[rejected.identity], decision="rejected")
    results.entries[failed.identity] = replace(results.entries[failed.identity], status="failed")
    revision = task.collection.collection_revision
    original_builder = task.collection._build_external_index
    scans = []

    def build_index(entries):
        scans.append(len(entries))
        return original_builder(entries)

    monkeypatch.setattr(task.collection, "_build_external_index", build_index)
    selected = results.publish_to_drafts((task,))

    assert len(selected) == 498
    assert scans == [500]
    assert task.collection.collection_revision == revision.next()
    for original in originals:
        current = task.collection.get(original.identity)
        assert current.identity == original.identity
        assert current.external_refs == original.external_refs
        assert current.metadata == original.metadata
        assert current.original == original.original
        assert task.collection.get_by_external_ref(original.external_refs[0]) == (current,)
        if original.identity in selected:
            assert current.translation == f"after {original.id}"
            assert current.stage == 3
            assert current.revision == original.revision.next()
        else:
            assert current is original


def test_decisions_reject_unknown_key_without_changing_any_rows():
    task, results = make_results()
    key = next(iter(results.entries))
    original = results.entries.copy()
    unknown = EntryKey(SourceNamespace("test:other"), "unknown")
    with pytest.raises(ValueError, match="只能确认"):
        results.decide({key: False, unknown: True})
    assert results.entries == original
    assert len(task.collection) == 3


def test_publish_rejects_invalid_stage_before_mutating_source():
    task, results = make_results()
    key = next(iter(results.entries))
    results.entries[key] = replace(results.entries[key], stage=17)
    before = tuple(task.collection)
    revision = task.collection.collection_revision
    with pytest.raises(ValueError, match="stage must"):
        results.publish_to_drafts((task,))
    assert tuple(task.collection) == before
    assert task.collection.collection_revision == revision


def test_publish_surfaces_revision_conflict_without_overwriting(monkeypatch):
    task, results = make_results()
    original_apply = task.collection.apply
    before = tuple(task.collection)

    def conflict(changes, context):
        changes = replace(
            changes,
            expected_revisions=tuple((key, revision.next()) for key, revision in changes.expected_revisions),
        )
        return original_apply(changes, context)

    monkeypatch.setattr(task.collection, "apply", conflict)
    with pytest.raises(RuntimeError, match="revision changed"):
        results.publish_to_drafts((task,))
    assert tuple(task.collection) == before


def test_bulk_decisions_preserve_accepted_and_rejected_choices():
    task, results = make_results(500)
    decisions = {key: index % 2 == 0 for index, key in enumerate(results.entries)}
    results.decide(decisions)
    selected = results.publish_to_drafts((task,))
    assert selected == {key for key, accepted in decisions.items() if accepted}
    assert all(
        results.entries[key].decision == ("accepted" if accepted else "rejected") for key, accepted in decisions.items()
    )
