from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import threading
from types import SimpleNamespace

from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io.identity import EntryKey, EntryRevision, Provenance, SourceNamespace
from transbridge.application.io.mutation import ChangeSet, EntryPatch, MutationStatus
from transbridge.application.io.publish import ImmediateCommitGuard
from transbridge.application.translation import (
    ActionAssignment,
    ActionPlan,
    CandidateSet,
    CommitTranslations,
    CommitTranslationsRequest,
    ContextBatch,
    ContextPlan,
    FilesystemTranslationCheckpointPort,
    InMemoryTranslationCheckpointPort,
    LegacyTranslationCandidateSession,
    OpenAiTranslationHttpPort,
    TranslationAction,
    TranslationBatchResponse,
    TranslationInput,
    TranslationServiceError,
    TranslationWorkload,
    TranslationWorkloadRequest,
    build_run_spec,
    translation_input_fingerprint,
)
from transbridge.application.translation.workload_models import canonical_hash
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection

NAMESPACE = SourceNamespace("test:translation-workload")


class Token:
    def __init__(self) -> None:
        self.cancelled = False

    @property
    def is_cancelled(self) -> bool:
        return self.cancelled

    def wait(self, timeout=None) -> bool:
        return self.cancelled

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise RuntimeError("cancelled")


class ControlledLlm:
    def __init__(self) -> None:
        self.calls = []
        self.retry_once = False
        self.cancel_after_first = False
        self.token: Token | None = None
        self.malformed = False

    def translate(self, request, *, cancellation=None):
        self.calls.append(request)
        if self.retry_once and len(self.calls) == 1:
            raise TranslationServiceError(
                "TRANSLATION_HTTP_429",
                "The translation service rejected the batch.",
                retryable=True,
                retry_after=0,
            )
        entries = request.entries[:-1] if self.malformed else request.entries
        values = []
        for entry in entries:
            if request.action is TranslationAction.TRANSLATE:
                text = f"translated:{entry.entry_key.local_key}"
            else:
                text = f"polished:{entry.translation}"
            values.append((entry.entry_key, text))
        response = TranslationBatchResponse(tuple(values), canonical_hash({"call": len(self.calls)}))
        if self.cancel_after_first and len(self.calls) == 1 and self.token is not None:
            self.token.cancelled = True
        return response


class AuditedCollection(TranslationEntryCollection):
    def __init__(self, entries):
        super().__init__(entries)
        self.apply_count = 0

    def apply(self, change_set, context):
        self.apply_count += 1
        return super().apply(change_set, context)


class OneBatchFails(ControlledLlm):
    def translate(self, request, *, cancellation=None):
        if request.entries[0].entry_key.local_key == "a":
            raise TranslationServiceError(
                "TRANSLATION_FIXTURE_FAILED",
                "The controlled batch failed.",
            )
        return super().translate(request, cancellation=cancellation)


class FailOnCommitCheckpoint(InMemoryTranslationCheckpointPort):
    def __init__(self) -> None:
        super().__init__()
        self.saves = 0

    def save(self, checkpoint) -> None:
        self.saves += 1
        if self.saves > 1:
            raise OSError("controlled checkpoint failure")
        super().save(checkpoint)


class FailFirstSaveCheckpoint(InMemoryTranslationCheckpointPort):
    def __init__(self) -> None:
        super().__init__()
        self.fail_next = True

    def save(self, checkpoint) -> None:
        if self.fail_next:
            self.fail_next = False
            raise OSError("controlled first checkpoint failure")
        super().save(checkpoint)


def _key(name: str) -> EntryKey:
    return EntryKey(NAMESPACE, name)


def _input(name: str, *, translation: str = "", stage: int = 0) -> TranslationInput:
    return TranslationInput(_key(name), EntryRevision(), f"original:{name}", translation, stage, "NPC_:FULL")


def _spec(entries, *, run_id="translation-run"):
    return build_run_spec(
        run_id=run_id,
        config_revision=4,
        input_revision=2,
        source_locale="en",
        target_locale="zh-CN",
        prompt_profile="default",
        provider="controlled",
        base_url="http://127.0.0.1:1/v1",
        model="controlled-model",
        parameters={"temperature": 0},
        retrieval_enabled=False,
        retrieval_loader=None,
        scope=tuple(entry.entry_key for entry in entries),
    )


def _plans(entries, actions, *, split=False):
    action_plan = ActionPlan(
        tuple(entry.entry_key for entry in entries),
        tuple(
            ActionAssignment(entry.entry_key, action, "test") for entry, action in zip(entries, actions, strict=True)
        ),
    )
    actionable = [
        entry.entry_key for entry, action in zip(entries, actions, strict=True) if action is not TranslationAction.SKIP
    ]
    batches = (
        tuple(ContextBatch(1, "fixture", (key,)) for key in actionable)
        if split
        else (ContextBatch(1, "fixture", tuple(actionable)),)
    )
    return action_plan, ContextPlan(batches)


def _request(entries, actions, checkpoint, **changes):
    action_plan, context_plan = _plans(entries, actions, split=changes.pop("split", False))
    values = {
        "run_spec": _spec(entries),
        "action_plan": action_plan,
        "context_plan": context_plan,
        "entries": tuple(entries),
        "owner_id": "owner-1",
        "checkpoint": checkpoint,
        "retry_backoff_seconds": 0,
    }
    values.update(changes)
    return TranslationWorkloadRequest(**values)


def _collection(entries) -> AuditedCollection:
    return AuditedCollection(
        TranslationEntry(
            id=entry.entry_key.local_key,
            key=entry.entry_key.local_key,
            original=entry.original,
            translation=entry.translation,
            stage=entry.stage,
            context=entry.context,
            entry_key=entry.entry_key,
            revision=entry.revision,
        )
        for entry in entries
    )


def _context(run_id="translation-run") -> RequestContext:
    return RequestContext(
        "owner-1",
        run_id=run_id,
        permissions=frozenset({"entry.translation.write", "entry.stage.write"}),
    )


def test_real_mixed_candidate_chain_and_single_collection_commit() -> None:
    entries = (
        _input("translate"),
        _input("polish", translation="draft", stage=1),
        _input("both"),
    )
    actions = (TranslationAction.TRANSLATE, TranslationAction.POLISH, TranslationAction.BOTH)
    checkpoint = InMemoryTranslationCheckpointPort()
    llm = ControlledLlm()

    workload_result = TranslationWorkload(llm).run(_request(entries, actions, checkpoint))

    assert workload_result.outcome is OperationOutcome.COMPLETED
    candidate_set = CandidateSet.from_dict(workload_result.value["candidate_set"])
    candidates = {candidate.entry_key.local_key: candidate.text for candidate in candidate_set.candidates}
    assert candidates == {
        "translate": "translated:translate",
        "polish": "polished:draft",
        "both": "polished:translated:both",
    }
    assert all(
        candidate.provenance.source == "translation-llm-v2" and candidate.provenance.actor == "owner-1"
        for candidate in candidate_set.candidates
    )
    assert len(llm.calls) == 2

    collection = _collection(entries)
    commit = CommitTranslations().commit(
        CommitTranslationsRequest(
            candidate_set,
            collection,
            _context(),
            ImmediateCommitGuard("translation-run"),
            checkpoint,
        )
    )

    assert commit.outcome is OperationOutcome.COMPLETED
    assert collection.apply_count == 1
    assert {entry.key: entry.translation for entry in collection} == candidates
    assert all(entry.stage == 2 for entry in collection)

    replay = CommitTranslations().commit(
        CommitTranslationsRequest(
            candidate_set,
            collection,
            _context(),
            ImmediateCommitGuard("translation-run"),
            checkpoint,
        )
    )
    assert replay.outcome is OperationOutcome.COMPLETED
    assert replay.counts.skipped == 3
    assert collection.apply_count == 1


def test_retry_is_bounded_and_records_accepted_attempt() -> None:
    entries = (_input("a"),)
    checkpoint = InMemoryTranslationCheckpointPort()
    llm = ControlledLlm()
    llm.retry_once = True

    result = TranslationWorkload(llm).run(_request(entries, (TranslationAction.TRANSLATE,), checkpoint, max_retries=1))

    candidate = CandidateSet.from_dict(result.value["candidate_set"]).candidates[0]
    assert result.outcome is OperationOutcome.COMPLETED
    assert candidate.attempt == 2
    assert len(llm.calls) == 2


def test_reserved_http_parameters_cannot_override_model_or_messages() -> None:
    entries = (_input("a"),)
    request = _request(
        entries,
        (TranslationAction.TRANSLATE,),
        InMemoryTranslationCheckpointPort(),
    )
    request = replace(
        request,
        run_spec=replace(request.run_spec, parameters=(("messages", "[]"),)),
    )

    result = TranslationWorkload(OpenAiTranslationHttpPort(timeout_seconds=0.01)).run(request)

    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].code == "TRANSLATION_PARAMETERS_INVALID"


def test_malformed_batch_is_failed_with_response_summary_and_no_candidate() -> None:
    entries = (_input("a"), _input("b"))
    checkpoint = InMemoryTranslationCheckpointPort()
    llm = ControlledLlm()
    llm.malformed = True

    result = TranslationWorkload(llm).run(
        _request(entries, (TranslationAction.TRANSLATE, TranslationAction.TRANSLATE), checkpoint)
    )

    assert result.outcome is OperationOutcome.FAILED
    assert result.counts.failed == 2
    assert result.diagnostics[0].code == "TRANSLATION_RESPONSE_KEYS_MISMATCH"
    assert dict(result.diagnostics[0].details)["response_sha256"] is not None


def test_one_failed_batch_keeps_accepted_candidate_and_reports_partial() -> None:
    entries = (_input("a"), _input("b"))

    result = TranslationWorkload(OneBatchFails()).run(
        _request(
            entries,
            (TranslationAction.TRANSLATE, TranslationAction.TRANSLATE),
            InMemoryTranslationCheckpointPort(),
            split=True,
        )
    )

    candidate_set = CandidateSet.from_dict(result.value["candidate_set"])
    assert result.outcome is OperationOutcome.PARTIAL
    assert result.counts.succeeded == 1
    assert result.counts.failed == 1
    assert tuple(candidate.entry_key.local_key for candidate in candidate_set.candidates) == ("b",)
    assert {diagnostic.code for diagnostic in result.diagnostics} == {"TRANSLATION_FIXTURE_FAILED"}


def test_cancel_after_accepted_batch_resumes_without_repeating_llm_side_effect() -> None:
    entries = (_input("a"), _input("b"))
    actions = (TranslationAction.TRANSLATE, TranslationAction.TRANSLATE)
    checkpoint = InMemoryTranslationCheckpointPort()
    token = Token()
    llm = ControlledLlm()
    llm.cancel_after_first = True
    llm.token = token

    first = TranslationWorkload(llm).run(_request(entries, actions, checkpoint, cancellation=token, split=True))
    assert first.outcome is OperationOutcome.PARTIAL
    assert first.counts.succeeded == 1
    assert first.counts.cancelled == 1

    token.cancelled = False
    llm.cancel_after_first = False
    second = TranslationWorkload(llm).run(_request(entries, actions, checkpoint, cancellation=token, split=True))

    assert second.outcome is OperationOutcome.COMPLETED
    assert len(llm.calls) == 2
    assert len(CandidateSet.from_dict(second.value["candidate_set"]).candidates) == 2


def test_checkpoint_owner_identity_mismatch_fails_before_llm_call() -> None:
    entries = (_input("a"),)
    checkpoint = InMemoryTranslationCheckpointPort()
    first_llm = ControlledLlm()
    first = TranslationWorkload(first_llm).run(_request(entries, (TranslationAction.TRANSLATE,), checkpoint))
    second_llm = ControlledLlm()

    second = TranslationWorkload(second_llm).run(
        _request(
            entries,
            (TranslationAction.TRANSLATE,),
            checkpoint,
            owner_id="different-owner",
        )
    )

    assert first.outcome is OperationOutcome.COMPLETED
    assert second.outcome is OperationOutcome.FAILED
    assert second.diagnostics[0].code == "TRANSLATION_CHECKPOINT_INVALID"
    assert second_llm.calls == []


def test_cancelled_commit_guard_never_mutates_formal_collection() -> None:
    entries = (_input("a"),)
    checkpoint = InMemoryTranslationCheckpointPort()
    result = TranslationWorkload(ControlledLlm()).run(_request(entries, (TranslationAction.TRANSLATE,), checkpoint))
    candidate_set = CandidateSet.from_dict(result.value["candidate_set"])
    collection = _collection(entries)

    commit = CommitTranslations().commit(
        CommitTranslationsRequest(
            candidate_set,
            collection,
            _context(),
            ImmediateCommitGuard("translation-run", active=lambda: False),
            checkpoint,
        )
    )

    assert commit.outcome is OperationOutcome.CANCELLED
    assert collection.apply_count == 0
    assert collection.get(_key("a")).translation == ""


def test_legacy_bridge_accepts_candidates_without_formal_write_then_commits_once() -> None:
    entries = (_input("a"),)
    collection = _collection(entries)
    checkpoint = InMemoryTranslationCheckpointPort()
    spec = _spec(entries, run_id="legacy-bridge-run")
    session = LegacyTranslationCandidateSession(
        run_id=spec.run_id,
        owner_id="legacy-owner",
        spec_fingerprint=spec.fingerprint,
        input_fingerprint=translation_input_fingerprint(entries),
        checkpoint=checkpoint,
        provider=spec.provider,
        model=spec.model,
    )

    accepted = session.accept({"a": "legacy-candidate"}, collection)

    assert accepted.accepted == 1
    assert collection.get(_key("a")).translation == ""
    assert collection.apply_count == 0

    commit = session.commit(
        collection,
        RequestContext(
            "legacy-owner",
            run_id=spec.run_id,
            permissions=frozenset({"entry.translation.write", "entry.stage.write"}),
        ),
        ImmediateCommitGuard(spec.run_id),
    )

    assert commit.outcome is OperationOutcome.COMPLETED
    assert collection.apply_count == 1
    assert collection.get(_key("a")).translation == "legacy-candidate"


def test_hidden_and_locked_entries_are_rejected_again_at_commit() -> None:
    entries = (_input("hidden", stage=-1), _input("locked", stage=9))
    checkpoint = InMemoryTranslationCheckpointPort()
    workload = TranslationWorkload(ControlledLlm()).run(
        _request(
            entries,
            (TranslationAction.TRANSLATE, TranslationAction.TRANSLATE),
            checkpoint,
        )
    )
    collection = _collection(entries)

    commit = CommitTranslations().commit(
        CommitTranslationsRequest(
            CandidateSet.from_dict(workload.value["candidate_set"]),
            collection,
            _context(),
            ImmediateCommitGuard("translation-run"),
            checkpoint,
        )
    )

    assert commit.outcome is OperationOutcome.FAILED
    assert commit.counts.failed == 2
    assert collection.apply_count == 0
    assert collection.get(_key("hidden")).translation == ""
    assert collection.get(_key("locked")).translation == ""


def test_user_edit_conflict_is_partial_and_does_not_blindly_overwrite() -> None:
    entries = (_input("a"), _input("b"))
    checkpoint = InMemoryTranslationCheckpointPort()
    result = TranslationWorkload(ControlledLlm()).run(
        _request(entries, (TranslationAction.TRANSLATE, TranslationAction.TRANSLATE), checkpoint)
    )
    candidate_set = CandidateSet.from_dict(result.value["candidate_set"])
    collection = _collection(entries)
    edited = collection.get(_key("a"))
    user_edit = collection.apply(
        ChangeSet(
            "user-run",
            (EntryPatch.create(_key("a"), translation="user-edit"),),
            ((_key("a"), edited.revision),),
            Provenance("user-run", "user", "manual-edit"),
        ),
        RequestContext(
            "user",
            run_id="user-run",
            permissions=frozenset({"entry.translation.write"}),
        ),
    )
    assert user_edit.status is MutationStatus.APPLIED

    commit = CommitTranslations().commit(
        CommitTranslationsRequest(
            candidate_set,
            collection,
            _context(),
            ImmediateCommitGuard("translation-run"),
            checkpoint,
        )
    )

    assert commit.outcome is OperationOutcome.PARTIAL
    assert commit.counts.succeeded == 1
    assert commit.counts.failed == 1
    assert collection.get(_key("a")).translation == "user-edit"
    assert collection.get(_key("b")).translation == "translated:b"


def test_commit_reports_partial_after_data_applied_but_checkpoint_evidence_fails() -> None:
    entries = (_input("a"),)
    checkpoint = FailOnCommitCheckpoint()
    workload = TranslationWorkload(ControlledLlm()).run(_request(entries, (TranslationAction.TRANSLATE,), checkpoint))
    collection = _collection(entries)

    commit = CommitTranslations().commit(
        CommitTranslationsRequest(
            CandidateSet.from_dict(workload.value["candidate_set"]),
            collection,
            _context(),
            ImmediateCommitGuard("translation-run"),
            checkpoint,
        )
    )

    assert commit.outcome is OperationOutcome.PARTIAL
    assert commit.counts.succeeded == 1
    assert commit.diagnostics[0].code == "TRANSLATION_COMMIT_EVIDENCE_FAILED"
    assert collection.apply_count == 1
    assert collection.get(_key("a")).translation == "translated:a"


def test_filesystem_candidate_checkpoint_round_trips_atomically(tmp_path: Path) -> None:
    entries = (_input("a"),)
    checkpoint = FilesystemTranslationCheckpointPort(tmp_path)
    result = TranslationWorkload(ControlledLlm()).run(_request(entries, (TranslationAction.TRANSLATE,), checkpoint))

    assert result.outcome is OperationOutcome.COMPLETED
    restored = checkpoint.load("translation-run")
    assert restored is not None
    assert len(restored.candidates) == 1
    assert list(tmp_path.glob("*.tmp")) == []


def test_real_auto_translator_constructor_buffers_then_commits_once(monkeypatch) -> None:
    from transbridge.ai_translator import noun_extractor, prompt_builder, term_database
    from transbridge.ai_translator.translator import (
        AutoTranslator,
        ProgressCheckpoint,
        TranslatorConfig,
    )
    from transbridge.infra import llm_client

    class FakeTermManager:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def load_all(self) -> None:
            pass

        def get_load_log(self):
            return ()

        def match_terms_scoped(self, **kwargs):
            return SimpleNamespace(flat_terms={}, terms_by_entry={})

        def exact_match(self, originals):
            return {original: f"exact:{original}" for original in originals}

    class ConstructorOnly:
        def __init__(self, *args, **kwargs) -> None:
            pass

    monkeypatch.setattr(llm_client, "create_llm_client", lambda config: object())
    monkeypatch.setattr(term_database, "TermDatabaseManager", FakeTermManager)
    monkeypatch.setattr(prompt_builder, "PromptBuilder", ConstructorOnly)
    monkeypatch.setattr(noun_extractor, "NounExtractor", ConstructorOnly)
    monkeypatch.setattr(ProgressCheckpoint, "save", lambda self, esp_path: None)
    monkeypatch.setattr(ProgressCheckpoint, "delete", lambda self, esp_path: None)

    config = SimpleNamespace(
        game_profile="fixture",
        target_lang="zh-CN",
        max_tokens_per_batch=100,
        max_concurrent=1,
        max_output_tokens=100,
        config_revision=11,
        provider="fixture",
        base_url="http://127.0.0.1:1/v1",
        model="fixture-model",
        enable_post_process=False,
    )
    translator = AutoTranslator(
        TranslatorConfig(config, "fixture.esp"),
        candidate_checkpoint=InMemoryTranslationCheckpointPort(),
        run_id_factory=lambda: "legacy-constructor-run",
    )
    entry = _input("legacy")
    collection = _collection((entry,))

    result = translator.translate(
        collection,
        ["legacy"],
        lambda *args: None,
        threading.Event(),
    )

    assert result.success_count == 1
    assert result.failed_count == 0
    assert collection.apply_count == 1
    assert collection.get(_key("legacy")).translation == "exact:original:legacy"
    assert collection.get(_key("legacy")).stage == 2


def test_stream_candidate_is_only_accepted_after_durable_checkpoint_and_retries(
    monkeypatch,
) -> None:
    from transbridge.ai_translator import noun_extractor, prompt_builder, term_database
    from transbridge.ai_translator.translator import (
        AutoTranslator,
        ProgressCheckpoint,
        TranslatorConfig,
    )
    from transbridge.infra import llm_client

    class StreamingLlm:
        def chat_stream(self, messages, max_tokens, callback):
            callback('{"stream":"streamed"}')
            return '{"stream":"streamed"}'

    captured_prompt_calls: list[dict] = []

    class StreamingPrompt:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def build_translation_prompt(
            self,
            entries,
            terms,
            batch_type,
            *,
            terms_by_entry=None,
        ):
            captured_prompt_calls.append({
                "entries": {entry.key: entry.original for entry in entries},
                "terms": dict(terms),
                "terms_by_entry": {key: dict(value) for key, value in (terms_by_entry or {}).items()},
            })
            return [{"role": "system", "content": "fixture"}, {"role": "user", "content": "fixture"}]

        def extract_partial_pairs(self, value):
            return {"stream": "streamed"}

        def parse_translation_response(self, response, expected_keys):
            return {}

    class EmptyTermManager:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def load_all(self) -> None:
            pass

        def get_load_log(self):
            return ()

        def match_terms_scoped(self, **kwargs):
            return SimpleNamespace(
                flat_terms={"stream-source": "流式源文"},
                terms_by_entry={"stream": {"stream-source": "流式源文"}},
            )

        def exact_match(self, originals):
            return {}

    class ConstructorOnly:
        def __init__(self, *args, **kwargs) -> None:
            pass

    monkeypatch.setattr(llm_client, "create_llm_client", lambda config: StreamingLlm())
    monkeypatch.setattr(term_database, "TermDatabaseManager", EmptyTermManager)
    monkeypatch.setattr(prompt_builder, "PromptBuilder", StreamingPrompt)
    monkeypatch.setattr(noun_extractor, "NounExtractor", ConstructorOnly)
    monkeypatch.setattr(ProgressCheckpoint, "save", lambda self, esp_path: None)
    monkeypatch.setattr(ProgressCheckpoint, "delete", lambda self, esp_path: None)

    config = SimpleNamespace(
        game_profile="fixture",
        target_lang="zh-CN",
        max_tokens_per_batch=100,
        max_concurrent=1,
        max_output_tokens=100,
        config_revision=12,
        provider="fixture",
        base_url="http://127.0.0.1:1/v1",
        model="fixture-model",
        enable_post_process=False,
    )
    checkpoint = FailFirstSaveCheckpoint()
    translator = AutoTranslator(
        TranslatorConfig(config, "fixture.esp"),
        candidate_checkpoint=checkpoint,
        run_id_factory=lambda: "stream-retry-run",
    )
    stream_entry = TranslationInput(
        _key("stream"),
        EntryRevision(),
        "stream-source",
        "",
        0,
    )
    collection = _collection((stream_entry,))

    first = translator.translate(
        collection,
        ["stream"],
        lambda *args: None,
        threading.Event(),
    )
    second = translator.translate(
        collection,
        ["stream"],
        lambda *args: None,
        threading.Event(),
    )

    assert first.success_count == 0
    assert first.failed_count == 1
    assert second.success_count == 1
    assert second.failed_count == 0
    assert collection.apply_count == 1
    assert collection.get(_key("stream")).translation == "streamed"
    assert captured_prompt_calls
    assert captured_prompt_calls[-1] == {
        "entries": {"stream": "stream-source"},
        "terms": {"stream-source": "流式源文"},
        "terms_by_entry": {"stream": {"stream-source": "流式源文"}},
    }


def test_mixed_worker_passes_active_collection_and_source_path_to_auto_translator(
    monkeypatch,
) -> None:
    from transbridge.ai_translator import translator as translator_module
    from transbridge.ui.tools.ai_translator._mixed_worker import _MixedWorker

    captured = {}

    class FakeAutoTranslator:
        def __init__(self, config, *, run_id_factory=None) -> None:
            captured["esp_path"] = config.esp_path
            captured["run_id"] = run_id_factory()

        def translate(self, *, collection, target_entry_ids, **kwargs):
            captured["collection"] = collection
            captured["target_entry_ids"] = target_entry_ids
            return SimpleNamespace(success_count=1, failed_count=0)

    monkeypatch.setattr(translator_module, "AutoTranslator", FakeAutoTranslator)
    collection = _collection((_input("mixed"),))
    context = SimpleNamespace(collection=collection, esp_path="mixed.esp")
    worker = _MixedWorker(
        cfg=SimpleNamespace(),
        translate_entries=list(collection),
        polish_entries=[],
        ctx=context,
        run_id="mixed-parent-run",
    )

    result = worker._do_translate()

    assert result.success_count == 1
    assert captured == {
        "esp_path": "mixed.esp",
        "run_id": "mixed-parent-run",
        "collection": collection,
        "target_entry_ids": ["mixed"],
    }
