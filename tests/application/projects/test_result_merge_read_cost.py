"""A small result must not reconstruct an index over the entire Variant."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from transbridge.application.contracts import OperationResult, RequestContext
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.projects.result_merge import ProjectResultMergeService
from transbridge.application.translation.result_merge import ResultConflictPolicy, ResultEntryChange, ResultEntryState
from transbridge.persistence.v2.ids import ProjectId, VariantId, VariantRef
from transbridge.persistence.v2.variant import SourceFingerprint, VariantEntryState, VariantSnapshot


class CountedEntries:
    def __init__(self, values):
        self.values = values
        self.visited = 0

    def __len__(self):
        return len(self.values)

    def __getitem__(self, index):
        self.visited += 1
        return self.values[index]

    def __iter__(self):
        for entry in self.values:
            self.visited += 1
            yield entry


def merge_case(service_type=ProjectResultMergeService, count=10_000):
    namespace = SourceNamespace("source:read-cost")
    ref = VariantRef(VariantId("variant"), ProjectId("project"))
    snapshot = VariantSnapshot(
        ref,
        (SourceFingerprint(namespace, "a" * 64),),
        tuple(VariantEntryState(EntryKey(namespace, f"{index:06}")) for index in range(count)),
    )
    entries = CountedEntries(snapshot.entries)
    observed = SimpleNamespace(ref=ref, entries=entries, revision=snapshot.revision)
    active = SimpleNamespace(
        formal_variant_ref=ref,
        variant=SimpleNamespace(snapshot=lambda: observed),
        project=SimpleNamespace(envelope=SimpleNamespace(revision=0)),
        project_ref=SimpleNamespace(identity=ref.project_id),
    )
    key = snapshot.entries[count // 2].entry_key
    base = ResultEntryState(key, "Source text", "", 0, "context")
    candidates = (ResultEntryChange(base, replace(base, translation="Completed result", stage=1)),)
    writes = []

    def commit(states, context, **options):
        options["commit_mutation"](lambda: writes.append(states))
        return OperationResult.completed(None, run_id=context.run_id)

    service = service_type(
        SimpleNamespace(active=active),
        lambda _project, _snapshot, keys, _context: {item: ("Source text", "context") for item in keys},
        commit,
        commit,
    )
    context = RequestContext("test", "result-read-cost", "project", "variant")
    return service, candidates, context, ref, entries, writes


@pytest.mark.parametrize("clear_scope", [None, ("paratranz", "42")])
def test_one_result_reads_only_its_target_from_large_sorted_snapshot(clear_scope):
    service, candidates, context, ref, entries, writes = merge_case()
    result = service.merge(
        candidates,
        context,
        expected_variant_ref=ref,
        policy=ResultConflictPolicy.KEEP_CURRENT,
        clear_external_scope=clear_scope,
    )
    assert result.is_success, result.diagnostics
    assert result.value.applied == (candidates[0].incoming,)
    assert len(writes) == 1 and set(writes[0]) == {candidates[0].base.entry_key}
    assert entries.visited < 100, "A single target scanned the entire 10,000-entry snapshot"


def test_missing_result_target_remains_pending_without_scanning_every_entry():
    service, candidates, context, ref, entries, writes = merge_case()
    missing = EntryKey(candidates[0].base.entry_key.namespace, "missing")
    candidates = tuple(
        ResultEntryChange(replace(row.base, entry_key=missing), replace(row.incoming, entry_key=missing))
        for row in candidates
    )
    result = service.merge(candidates, context, expected_variant_ref=ref, policy=ResultConflictPolicy.PREFER_INCOMING)
    assert result.is_success and not result.value.applied
    assert result.value.pending[0].reason == "source_changed_or_missing"
    assert writes == [{}]
    assert entries.visited < 100


def test_dense_result_batch_uses_one_pass_and_reuses_selected_records():
    service, candidates, context, ref, entries, writes = merge_case()
    candidates = tuple(
        ResultEntryChange(
            replace(candidates[0].base, entry_key=entry.entry_key),
            replace(candidates[0].incoming, entry_key=entry.entry_key),
        )
        for entry in entries.values[:2000]
    )
    result = service.merge(
        candidates,
        context,
        expected_variant_ref=ref,
        policy=ResultConflictPolicy.KEEP_CURRENT,
        clear_external_scope=("paratranz", "42"),
    )
    assert result.is_success and len(result.value.applied) == 2000
    assert len(writes) == 1 and len(writes[0]) == 2000
    assert entries.visited <= len(entries)
