"""Existing translations are durable before the first model request starts."""

from threading import Event
from types import SimpleNamespace

import pytest

from tests.ai_translator import test_prepared_names as support
from transbridge.ai_translator.batch_planner import Batch
from transbridge.ai_translator.prepared_names import PreparedName
from transbridge.ai_translator.translation_reuse import admit_translation_reuse, select_translation_reuse
from transbridge.application.translation import InMemoryTranslationCheckpointPort
from transbridge.converter.translation_entry_collection import TranslationEntryCollection

make_translator = support.make_translator


@pytest.mark.parametrize("prepared", [False, True], ids=["exact-term", "prepared-name"])
def test_later_round_reuse_is_persisted_before_first_model_call(make_translator, monkeypatch, prepared):
    pending = support._entry("pending", original="New name")
    reusable = support._entry("known", original="Known text", context="BOOK:DESC")
    port = InMemoryTranslationCheckpointPort()
    names = {reusable.identity: PreparedName(reusable.original, reusable.context, "已有译文")} if prepared else {}
    translator, provider, terms = make_translator(prepared=names, checkpoint=port)
    translator._run_id_factory = lambda: "reuse-first"
    if not prepared:
        terms.exact[reusable.original] = "已有译文"
    collection = TranslationEntryCollection([pending, reusable])
    snapshots, phases, batches = [], [], []
    original_chat = provider.chat
    original_batch = translator._run_batch

    def chat(*args, **kwargs):
        checkpoint = port.load("reuse-first")
        assert checkpoint is not None
        snapshots.append({item.entry_key: item.text for item in checkpoint.candidates})
        # Admission does not bypass the final guarded commit.
        assert collection.get(reusable.identity).translation == ""
        return original_chat(*args, **kwargs)

    def batch(value, *args, **kwargs):
        batches.append(tuple(entry.identity for entry in value.entries))
        return original_batch(value, *args, **kwargs)

    monkeypatch.setattr(provider, "chat", chat)
    monkeypatch.setattr(translator, "_run_batch", batch)
    result = translator.translate(
        collection, None, lambda *_: None, Event(), stage_progress_callback=lambda *args: phases.append(args)
    )

    assert snapshots == [{reusable.identity: "已有译文"}]
    assert batches == [(pending.identity,)]
    assert provider.translation_calls == [("pending",)]
    assert collection.get(reusable.identity).translation == "已有译文"
    assert result.success_count == 2 and result.failed_count == 0
    assert all(item.status == "succeeded" for item in result.entry_outcomes.values())
    reuse_progress = [event for event in phases if event[0] == "reuse"]
    assert reuse_progress[0][1:3] == (0, 2)
    assert reuse_progress[-1][1:3] == (2, 2)
    assert "已复用 1 条" in reuse_progress[-1][3]


def test_mixed_batch_sends_only_unresolved_entries_without_duplicate_candidates(make_translator, monkeypatch):
    known = support._entry("known")
    pending = support._entry("pending", original="New name")
    port = InMemoryTranslationCheckpointPort()
    translator, provider, terms = make_translator(checkpoint=port)
    translator._run_id_factory = lambda: "mixed-reuse"
    terms.exact[known.original] = "已有译文"
    batches = []
    original_batch = translator._run_batch

    def batch(value, *args, **kwargs):
        batches.append(tuple(entry.identity for entry in value.entries))
        return original_batch(value, *args, **kwargs)

    monkeypatch.setattr(translator, "_run_batch", batch)
    result, collection = support._translate(translator, [known, pending])

    assert batches == [(pending.identity,)]
    assert provider.translation_calls == [("pending",)]
    assert result.success_count == 2 and result.failed_count == 0
    assert len(port.load("mixed-reuse").candidates) == 2
    assert collection.get(known.identity).translation == "已有译文"


def test_all_reused_entries_skip_batch_workers(make_translator):
    entry = support._entry()
    translator, provider, terms = make_translator()
    terms.exact[entry.original] = "已有译文"

    class Workers:
        def run(self, *_args, **_kwargs):
            pytest.fail("local reuse must not enter batch workers")

    translator._batch_executor = Workers()
    result, collection = support._translate(translator, [entry])
    assert provider.translation_calls == []
    assert result.entry_outcomes[entry.identity].status == "succeeded"
    assert collection.get(entry.identity).translation == "已有译文"


@pytest.mark.parametrize("context", ["NPC_:FULL", "BOOK:DESC"], ids=["mixed-batch", "separate-batch"])
def test_cancel_after_reuse_resumes_without_duplicate_candidates_or_count(make_translator, monkeypatch, context):
    known = support._entry("known", context=context)
    pending = support._entry("pending", original="New name")
    port = InMemoryTranslationCheckpointPort()
    first, provider, terms = make_translator(checkpoint=port)
    first._run_id_factory = lambda: "resumed-reuse"
    terms.exact[known.original] = "已有译文"
    collection = TranslationEntryCollection([pending, known])
    stop = Event()
    checkpoints = []
    monkeypatch.setattr(support.ProgressCheckpoint, "save", lambda checkpoint, _: checkpoints.append(checkpoint))

    def progress(phase, current, total, _message):
        if phase == "reuse" and current == total:
            stop.set()

    cancelled = first.translate(collection, None, lambda *_: None, stop, stage_progress_callback=progress)
    assert provider.translation_calls == []
    assert collection.get(known.identity).translation == ""
    assert cancelled.entry_outcomes[known.identity].status == "cancelled"
    assert len(port.load("resumed-reuse").candidates) == 1

    resumed, provider, _ = make_translator(checkpoint=port)
    result = resumed.translate(collection, None, lambda *_: None, Event(), checkpoint=checkpoints[-1])
    assert provider.translation_calls == [("pending",)]
    assert len(port.load("resumed-reuse").candidates) == 2
    assert result.success_count == 2 and result.failed_count == 0
    assert all(item.status == "succeeded" for item in result.entry_outcomes.values())
    assert collection.get(known.identity).translation == "已有译文"


def test_exact_reuse_is_scoped_and_failed_prepared_names_cannot_fall_back():
    first = support._entry("first", source="A")
    second = support._entry("second", source="B")
    failed = support._entry("failed", source="A")
    terms = SimpleNamespace(
        lookup_context_for_entry=lambda entry: entry.identity.namespace.value,
        exact_match=lambda originals, context: {original: f"译文{context}" for original in originals},
    )
    names = {failed.identity: PreparedName(failed.original, failed.context, reason="不满足术语约束")}
    selection = select_translation_reuse([first, second, failed], names, terms)
    assert selection.accepted == {"first": "译文A", "second": "译文B"}
    assert selection.remaining == ()
    assert len(selection.failures) == 1 and "不满足术语约束" in selection.failures[0]


def test_cancellation_during_reuse_does_not_admit_later_batches():
    first, second = support._entry("first"), support._entry("second")
    stop = Event()
    accepted, completed = [], set()

    def progress(_phase, current, _total, _message):
        if current == 1:
            stop.set()

    result = admit_translation_reuse(
        [Batch([first], "名称"), Batch([second], "名称")],
        prepared_names={},
        manager=SimpleNamespace(exact_match=lambda originals: {original: "已有译文" for original in originals}),
        completed_fingerprints=completed,
        accept=lambda values: accepted.append(values) or len(values),
        stop_event=stop,
        pause_event=None,
        progress=progress,
        log=lambda _: None,
    )
    assert accepted == [{"first": "已有译文"}]
    assert result.settled == {first.identity}
    assert completed == {frozenset({"first"})}


def test_completed_batches_are_not_reused_again():
    entry = support._entry()
    result = admit_translation_reuse(
        [Batch([entry], "名称")],
        prepared_names={},
        manager=SimpleNamespace(exact_match=lambda _: pytest.fail("already completed")),
        completed_fingerprints={frozenset({"name"})},
        accept=lambda _: pytest.fail("already completed"),
        stop_event=Event(),
        pause_event=None,
        progress=None,
        log=lambda _: None,
    )
    assert not result.settled and not result.failures


@pytest.mark.parametrize("postprocess", [False, True])
def test_committed_draft_is_not_rewritten_but_still_receives_configured_postprocess(make_translator, postprocess):
    from transbridge.application.io.publish import ImmediateCommitGuard
    from transbridge.application.translation import TranslationInput
    from transbridge.application.translation.draft_reuse import commit_draft_reuse

    entry = support._entry()
    collection = TranslationEntryCollection([entry])
    receipt = commit_draft_reuse(
        (TranslationInput(entry.identity, entry.revision, entry.original, "", 0, entry.context),),
        {"name": "已有译文"},
        collection=collection,
        run_id="early-draft",
        checkpoint=InMemoryTranslationCheckpointPort(),
        guard=ImmediateCommitGuard("early-draft"),
    )
    after = collection.get(entry.identity).snapshot()
    translator, provider, _ = make_translator(committed=receipt, postprocess=postprocess)
    result = translator.translate(collection, None, lambda *_: None, Event())
    assert provider.translation_calls == []
    assert result.success_count == 1 and result.failed_count == 0
    assert result.entry_outcomes[entry.identity].status == "succeeded"
    if postprocess:
        assert len(provider.proofread_calls) == 1
        assert collection.get(entry.identity).translation == "已有译文（校对）"
    else:
        assert collection.get(entry.identity).snapshot() == after


def test_committed_draft_can_resume_after_cancel_without_recounting_or_reapplying(make_translator, monkeypatch):
    from transbridge.application.io.publish import ImmediateCommitGuard
    from transbridge.application.translation import TranslationInput
    from transbridge.application.translation.draft_reuse import commit_draft_reuse

    known = support._entry()
    pending = support._entry("pending", original="Other")
    collection = TranslationEntryCollection([known, pending])
    receipt = commit_draft_reuse(
        (TranslationInput(known.identity, known.revision, known.original, "", 0, known.context),),
        {"name": "已有译文"},
        collection=collection,
        run_id="early-draft",
        checkpoint=InMemoryTranslationCheckpointPort(),
        guard=ImmediateCommitGuard("early-draft"),
    )
    before = collection.get(known.identity).snapshot()
    port = InMemoryTranslationCheckpointPort()
    translator, provider, _ = make_translator(committed=receipt, checkpoint=port)
    checkpoints, stop = [], Event()
    monkeypatch.setattr(support.ProgressCheckpoint, "save", lambda checkpoint, _: checkpoints.append(checkpoint))

    def progress(phase, current, total, _message):
        if phase == "reuse" and current == total:
            stop.set()

    translator.translate(collection, None, lambda *_: None, stop, stage_progress_callback=progress)
    assert provider.translation_calls == []
    resumed, provider, _ = make_translator(committed=receipt, checkpoint=port)
    result = resumed.translate(collection, None, lambda *_: None, Event(), checkpoint=checkpoints[-1])
    assert provider.translation_calls == [("pending",)]
    assert result.success_count == 2 and result.failed_count == 0
    assert collection.get(known.identity).snapshot() == before
    assert all(item.status == "succeeded" for item in result.entry_outcomes.values())
