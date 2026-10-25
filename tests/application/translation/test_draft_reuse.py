from dataclasses import replace

import pytest

from tests.conftest import make_entry
from transbridge.application.contracts import OperationResult
from transbridge.application.io.publish import ImmediateCommitGuard
from transbridge.application.translation import InMemoryTranslationCheckpointPort, TranslationInput
from transbridge.application.translation.draft_reuse import CommittedDraftTranslations, commit_draft_reuse
from transbridge.application.translation.entry_alias import ai_entry_key
from transbridge.converter.translation_entry_collection import TranslationEntryCollection


def _commit(collection, *, port=None, active=lambda: True):
    entries = tuple(collection)
    return commit_draft_reuse(
        tuple(
            TranslationInput(
                entry.identity, entry.revision, entry.original, entry.translation, entry.stage, entry.context
            )
            for entry in entries
        ),
        {ai_entry_key(entry): "已有译文" for entry in entries},
        collection=collection,
        run_id="early-reuse",
        checkpoint=port or InMemoryTranslationCheckpointPort(),
        guard=ImmediateCommitGuard("early-reuse", active=active),
    )


def test_local_reuse_persists_then_writes_draft_and_combines_evidence_without_second_write():
    entry = make_entry("name", original="Known", context="NPC_:FULL")
    collection = TranslationEntryCollection([entry])
    saves = []

    class Port(InMemoryTranslationCheckpointPort):
        def save(self, checkpoint):
            saves.append(collection.get(entry.identity).translation)
            super().save(checkpoint)

    port = Port()
    receipt = _commit(collection, port=port)
    after = collection.get(entry.identity).snapshot()
    assert saves == ["", "已有译文"]
    assert after.translation == "已有译文" and after.stage == 2
    assert after.revision == entry.revision.next()
    assert port.load("early-reuse").committed_candidate_ids
    assert receipt.inputs[0].translation == ""
    merged = receipt.include_in_commit(OperationResult.completed({"applied_keys": []}), collection)
    assert merged.counts.skipped == 1
    assert merged.value["already_committed_keys"] == [entry.identity.to_dict()]
    assert collection.get(entry.identity).snapshot() == after
    assert CommittedDraftTranslations().spec() == {}
    assert "committed_draft_reuse" in receipt.spec()


@pytest.mark.parametrize("cancelled", [False, True])
def test_failed_persistence_or_cancelled_guard_never_writes_draft(cancelled):
    entry = make_entry("name", original="Known", context="NPC_:FULL")
    collection = TranslationEntryCollection([entry])

    class Port(InMemoryTranslationCheckpointPort):
        def save(self, checkpoint):
            if not cancelled:
                raise OSError("disk unavailable")
            super().save(checkpoint)

    with pytest.raises((OSError, RuntimeError), match="disk unavailable|未能完整提交"):
        _commit(collection, port=Port(), active=lambda: not cancelled)
    assert collection.get(entry.identity).snapshot() == entry.snapshot()


@pytest.mark.parametrize(
    "changes", [{"original": "Other"}, {"context": "WEAP:FULL"}, {"translation": "其他译文"}, {"stage": 3}]
)
def test_changed_draft_cannot_supply_prior_commit_evidence(changes):
    entry = make_entry("name", original="Known", context="NPC_:FULL")
    collection = TranslationEntryCollection([entry])
    receipt = _commit(collection)
    # Even legacy direct edits that do not advance revisions invalidate reuse.
    changed = TranslationEntryCollection([replace(collection.get(entry.identity), **changes)])
    with pytest.raises(ValueError, match="草稿已变化"):
        receipt.require_current(changed)
    with pytest.raises(ValueError, match="翻译范围"):
        receipt.require_current(collection, set())


def test_same_text_without_commit_provenance_cannot_forge_a_receipt():
    entry = make_entry("name", original="Known", context="NPC_:FULL")
    before = TranslationInput(entry.identity, entry.revision, entry.original, "", 0, entry.context)
    after = replace(entry, translation="已有译文", stage=2, revision=entry.revision.next()).snapshot()
    with pytest.raises(ValueError, match="有效提交证据"):
        CommittedDraftTranslations((before,), (after,), "made-up-commit")
