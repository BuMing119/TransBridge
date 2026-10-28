"""A receipt reuses the exact durable pre-commit evidence without rebuilding it."""

from dataclasses import replace

import pytest

from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.translation import task_merge_evidence
from transbridge.application.translation.result_merge import (
    ResultConflictPolicy,
    ResultEntryChange,
    ResultEntryState,
    merge_result_entries,
)
from transbridge.application.translation.task_merge_evidence import TaskMergeEvidenceStore


def changes():
    base = ResultEntryState(EntryKey(SourceNamespace("source"), "one"), "original", "base", 1, "context")
    current, incoming = replace(base, translation="manual"), replace(base, translation="AI")
    candidates = (ResultEntryChange(base, incoming),)
    policy = ResultConflictPolicy.PREFER_INCOMING
    plan = merge_result_entries(
        {base.entry_key: base}, {base.entry_key: current}, {base.entry_key: incoming}, policy=policy
    )
    return candidates, plan, policy


def test_receipt_does_not_serialize_the_durable_candidates_again(tmp_path, monkeypatch):
    writer = TaskMergeEvidenceStore(tmp_path, "task")
    candidates, plan, policy = changes()
    writer.save(candidates, plan, policy)
    original = task_merge_evidence.asdict
    visits = []

    def counted(value):
        visits.append(value)
        return original(value)

    monkeypatch.setattr(task_merge_evidence, "asdict", counted)
    assert writer.acknowledge(candidates, plan, policy)
    assert not visits
    assert writer.load()[0]["applied_keys"] == [candidates[0].base.entry_key.to_dict()]


def test_a_new_commit_still_validates_existing_evidence_even_after_receipt(tmp_path):
    writer = TaskMergeEvidenceStore(tmp_path, "task")
    candidates, plan, policy = changes()
    path = writer.save(candidates, plan, policy)
    assert writer.acknowledge(candidates, plan, policy)
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="证据损坏"):
        writer.save(candidates, plan, policy)


def test_receipt_without_precommit_cache_still_creates_complete_evidence(tmp_path):
    writer = TaskMergeEvidenceStore(tmp_path, "task")
    candidates, plan, policy = changes()
    assert writer.acknowledge(candidates, plan, policy)
    evidence = writer.load()[0]
    assert evidence["candidates"][0]["incoming"]["translation"] == "AI"
    assert evidence["conflicts"][0]["current"]["translation"] == "manual"


def test_new_payload_cannot_reuse_another_plans_evidence(tmp_path):
    writer = TaskMergeEvidenceStore(tmp_path, "task")
    candidates, plan, policy = changes()
    writer.save(candidates, plan, policy)
    incoming = replace(candidates[0].incoming, translation="new AI")
    updated = (ResultEntryChange(candidates[0].base, incoming),)
    new_plan = replace(plan, applied=(incoming,), conflicts=())
    assert writer.acknowledge(updated, new_plan, policy)
    evidence = writer.load()
    assert len(evidence) == 2
    assert not evidence[0]["applied_keys"]
    assert evidence[1]["candidates"][0]["incoming"]["translation"] == "new AI"
    assert evidence[1]["applied_keys"] == [incoming.entry_key.to_dict()]


def test_receipt_disk_failure_retains_retry_without_rebuilding_evidence(tmp_path, monkeypatch):
    writer = TaskMergeEvidenceStore(tmp_path, "task")
    candidates, plan, policy = changes()
    writer.save(candidates, plan, policy)
    atomic_json = task_merge_evidence.atomic_json

    def fail_receipt(path, payload):
        if path.name.endswith(".applied.json"):
            raise PermissionError("receipt disk full")
        return atomic_json(path, payload)

    monkeypatch.setattr(task_merge_evidence, "atomic_json", fail_receipt)
    assert not writer.acknowledge(candidates, plan, policy)
    assert "receipt disk full" in writer.error
    assert not writer.load()[0]["applied_keys"]
    monkeypatch.setattr(task_merge_evidence, "atomic_json", atomic_json)
    monkeypatch.setattr(task_merge_evidence, "asdict", lambda _: pytest.fail("receipt retry rebuilt candidates"))
    writer.retry_receipts()
    assert not writer.error
    assert writer.load()[0]["applied_keys"] == [candidates[0].base.entry_key.to_dict()]
