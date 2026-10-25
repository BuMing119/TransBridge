from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import json
import re
import threading
from types import SimpleNamespace

import pytest

from transbridge.ai_translator.prepared_names import PreparedName, prepared_names_spec, select_prepared_names
from transbridge.ai_translator.translator import AutoTranslator, ProgressCheckpoint, TranslatorConfig
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.translation import InMemoryTranslationCheckpointPort
from transbridge.application.translation.entry_alias import ai_entry_key
from transbridge.application.translation.workload_models import canonical_hash
from transbridge.config.llm import LLMConfig
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection


def _entry(key="name", *, source="plugin", original="Whiterun", context="NPC_:FULL", translation="", stage=0):
    return TranslationEntry(
        key,
        key,
        original,
        translation,
        stage,
        context,
        entry_key=EntryKey(SourceNamespace(source), key),
    )


class _Terms:
    def __init__(self):
        self.exact = {}
        self.canonical = "雪漫城"

    def load_all(self):
        pass

    def get_load_log(self):
        return ()

    def exact_match(self, originals):
        return {value: self.exact[value] for value in originals if value in self.exact}

    def match_terms_scoped(self, *, entries, **_kwargs):
        return SimpleNamespace(flat_terms={}, terms_by_entry={ai_entry_key(entry): {} for entry in entries})

    def match_terms_for_entry(self, _entry):
        return {}

    def resolve_term(self, term):
        return SimpleNamespace(term=term, translation=self.canonical)


class _Provider:
    def __init__(self):
        self.translation_calls = []
        self.proofread_calls = []
        self.translation = "雪漫城"

    def chat(self, messages, max_tokens=0):
        content = messages[-1]["content"]
        payload = re.search(r"<translation_entries>\s*(.*?)\s*</translation_entries>", content, re.S)
        if payload:
            entries = json.loads(payload[1])
            self.translation_calls.append(tuple(entries))
            return json.dumps({"results": [{"entry_id": key, "translation": self.translation} for key in entries]})
        entries = json.loads(content)["entries"]
        self.proofread_calls.append(tuple(entries))
        return json.dumps({
            "results": [
                {"entry_key": entry["entry_key"], "final_translation": entry["current_translation"] + "（校对）"}
                for entry in entries
            ]
        })

    def chat_stream(self, messages, max_tokens, callback):
        response = self.chat(messages, max_tokens)
        callback(response)
        return response

    def cancel(self):
        pass


@pytest.fixture
def make_translator(tmp_path, monkeypatch):
    terms = _Terms()
    monkeypatch.setattr("transbridge.ai_translator.term_database.TermDatabaseManager", lambda **_kwargs: terms)
    monkeypatch.setattr("transbridge.infra.llm_reasoning.with_reasoning_intent", lambda client, *_: client)
    monkeypatch.setattr(ProgressCheckpoint, "save", lambda *_: None)
    monkeypatch.setattr(ProgressCheckpoint, "delete", lambda *_: None)

    def make(
        *,
        prepared=None,
        postprocess=False,
        defer=False,
        retrieval=False,
        checkpoint=None,
        overwrite=False,
        committed=None,
    ):
        provider = _Provider()
        config = LLMConfig(
            max_concurrent=1,
            retrieval_enabled=retrieval,
            enable_semantic_match=False,
            enable_post_process=postprocess,
            pp_strategy="proofread",
        )
        translator = AutoTranslator(
            TranslatorConfig(config, str(tmp_path / "plugin.esp"), overwrite=overwrite),
            llm_client=provider,
            candidate_checkpoint=checkpoint or InMemoryTranslationCheckpointPort(),
            prepared_names=prepared,
            defer_generated_terms=defer,
            committed_draft=committed,
        )
        return translator, provider, terms

    return make


def _translate(translator, entries, **kwargs):
    collection = TranslationEntryCollection(entries)
    target_entry_ids = kwargs.pop("target_entry_ids", None)
    result = translator.translate(collection, target_entry_ids, lambda *_: None, threading.Event(), **kwargs)
    return result, collection


def test_prepared_name_is_immutable_and_preserves_protected_syntax():
    entry = _entry(original="Whiterun {name}")
    prepared = PreparedName(entry.original, entry.context, "雪漫城 {name}")
    with pytest.raises(FrozenInstanceError):
        prepared.text = "changed"
    selected = select_prepared_names([entry], {entry.identity: prepared})
    assert selected.accepted == {"name": "雪漫城 {name}"}
    assert selected.failures == ()
    assert selected.remaining == ()
    assert entry.translation == ""


def test_preparation_spec_preserves_legacy_default_and_is_stable_across_mapping_order():
    first, second = _entry("first"), _entry("second", source="other")
    left = {
        first.identity: PreparedName(first.original, first.context, "雪漫城"),
        second.identity: PreparedName(second.original, second.context, reason="名称翻译失败"),
    }
    assert prepared_names_spec({}) == {}
    assert prepared_names_spec(left) == prepared_names_spec(dict(reversed(tuple(left.items()))))
    assert prepared_names_spec(left) != prepared_names_spec(left, defer_generated_terms=True)
    assert prepared_names_spec({}, defer_generated_terms=True)
    changed = dict(left)
    changed[first.identity] = replace(changed[first.identity], reason="重试仍失败")
    assert prepared_names_spec(changed) != prepared_names_spec(left)


def test_candidate_fingerprint_versions_advisory_policy_and_preserves_input_identity(make_translator):
    checkpoint = InMemoryTranslationCheckpointPort()
    translator, _, _ = make_translator(checkpoint=checkpoint)
    translator._run_id_factory = lambda: "default-spec"
    entry = _entry()
    _translate(translator, [entry])
    config = translator._cfg.llm_config
    assert checkpoint.load("default-spec").spec_fingerprint == canonical_hash({
        "run_id": "default-spec",
        "config_revision": config.config_revision,
        "provider": config.provider,
        "base_url": config.base_url,
        "model": config.model,
        "target_lang": config.target_lang,
        "scope": [entry.identity.to_dict()],
        "terminology_policy": "advisory-v1",
    })


@pytest.mark.parametrize("changed", ["failure", "defer"])
def test_same_run_cannot_restore_candidates_after_preparation_changes(make_translator, changed):
    checkpoint = InMemoryTranslationCheckpointPort()
    entry = _entry()
    prepared = {entry.identity: PreparedName(entry.original, entry.context, "雪漫城")}
    first, _, _ = make_translator(prepared=prepared, checkpoint=checkpoint)
    first._run_id_factory = lambda: "same-run"
    first_result, _ = _translate(first, [entry])
    assert first_result.success_count == 1

    if changed == "failure":
        prepared[entry.identity] = PreparedName(entry.original, entry.context, reason="本次准备失败")
    second, provider, _ = make_translator(prepared=prepared, checkpoint=checkpoint, defer=changed == "defer")
    second._run_id_factory = lambda: "same-run"
    collection = TranslationEntryCollection([_entry()])
    with pytest.raises(ValueError, match="checkpoint identity does not match"):
        second.translate(collection, None, lambda *_: None, threading.Event())
    assert collection.get(entry.identity).translation == ""
    assert provider.translation_calls == []


def test_prepared_names_are_bound_to_complete_source_identity(make_translator):
    earlier = _entry(source="earlier")
    current = _entry(source="current")
    translator, provider, _ = make_translator(
        prepared={earlier.identity: PreparedName(earlier.original, earlier.context, "其他插件译文")}
    )
    result, collection = _translate(translator, [current])
    assert provider.translation_calls == [("name",)]
    assert collection.get(current.identity).translation == "雪漫城"
    assert result.entry_outcomes[current.identity].status == "succeeded"


def test_successful_prepared_name_uses_real_candidate_commit_and_configured_postprocess(make_translator):
    entry = _entry()
    mapping = {entry.identity: PreparedName(entry.original, entry.context, "雪漫城")}
    translator, provider, _ = make_translator(prepared=mapping, postprocess=True)
    mapping.clear()  # The consumer keeps its captured bootstrap result.

    result, collection = _translate(translator, [entry])

    assert provider.translation_calls == []
    assert len(provider.proofread_calls) == 1
    assert collection.get(entry.identity).translation == "雪漫城（校对）"
    assert result.entry_outcomes[entry.identity].status == "succeeded"
    assert result.entry_outcomes[entry.identity].text == "雪漫城（校对）"
    assert result.success_count == 1
    assert any(stage.phase == "proofread" for stage in result.post_process_result.stage_outcomes)


def test_prepared_name_never_republishes_generated_terms_or_populates_name_cache(make_translator, monkeypatch):
    entry = _entry(original="Dawn")
    translator, provider, _ = make_translator(
        prepared={entry.identity: PreparedName(entry.original, entry.context, "黎明号")}, retrieval=True
    )
    written = []
    monkeypatch.setattr(translator, "_save_generated_terms", lambda terms: written.extend(terms) or len(terms))

    result, collection = _translate(translator, [entry])

    assert provider.translation_calls == []
    assert written == []
    assert translator._in_flight_terms == {}
    assert result.new_dynamic_terms == 0
    assert collection.get(entry.identity).translation == "黎明号"


def test_prepared_overwrite_excludes_old_name_from_existing_term_seeding(make_translator, monkeypatch):
    from transbridge.ai_translator.existing_term_extractor import ExistingTermSeedResult, should_seed_existing_terms

    entry = _entry(original="Dawn", translation="旧黎明", stage=1)
    pending = _entry("pending", original="Other name")
    existing = _entry("existing", original="Riverwood", translation="溪木镇", stage=1)
    translator, provider, _ = make_translator(
        prepared={entry.identity: PreparedName(entry.original, entry.context, "黎明号")},
        retrieval=True,
        overwrite=True,
    )
    inspected, seeded, written = [], [], []

    def should_seed(entries, candidates):
        inspected.append((tuple(e.identity for e in entries), tuple(e.identity for e in candidates)))
        return should_seed_existing_terms(entries, candidates)

    class Seeder:
        def __init__(self, *_args, **_kwargs):
            pass

        def seed(self, entries, **_kwargs):
            seeded.extend(e.identity for e in entries)
            return ExistingTermSeedResult()

    monkeypatch.setattr("transbridge.ai_translator.existing_term_extractor.should_seed_existing_terms", should_seed)
    monkeypatch.setattr("transbridge.ai_translator.existing_term_extractor.ExistingTermSeeder", Seeder)
    monkeypatch.setattr(translator, "_save_generated_terms", lambda terms: written.extend(terms) or len(terms))

    result, collection = _translate(
        translator, [entry, pending, existing], target_entry_ids=[ai_entry_key(entry), ai_entry_key(pending)]
    )

    assert inspected == [((pending.identity, existing.identity), (pending.identity,))]
    assert seeded == [pending.identity, existing.identity]
    assert all(term[0] != "Dawn" for term in written)
    assert provider.translation_calls == [("pending",)]
    assert collection.get(entry.identity).translation == "黎明号"
    assert collection.get(existing.identity).translation == "溪木镇"
    assert result.entry_outcomes[entry.identity].status == "succeeded"


def test_prepared_name_skips_restored_automatic_term_repair(make_translator):
    entry = _entry(translation="旧译文", stage=1)
    translator, provider, _ = make_translator(
        prepared={entry.identity: PreparedName(entry.original, entry.context, "雪漫城")}, overwrite=True
    )
    checkpoint = ProgressCheckpoint(
        esp_stem="plugin",
        target_entry_ids=None,
        overwrite=True,
        completed_fingerprints=[],
        result_so_far={},
        run_id="resumed-prepared",
        term_repairs=[{"entry_key": entry.identity.to_dict(), "required_terms": {"Whiterun": "旧术语"}}],
    )

    result, collection = _translate(translator, [entry], checkpoint=checkpoint)

    assert provider.translation_calls == []
    assert collection.get(entry.identity).translation == "雪漫城"
    assert result.entry_outcomes[entry.identity].status == "succeeded"


@pytest.mark.parametrize(
    "prepared",
    [
        PreparedName("changed original", "NPC_:FULL", "雪漫城 {name}"),
        PreparedName("Whiterun {name}", "BOOK:FULL", "雪漫城 {name}"),
        PreparedName("Whiterun {name}", "NPC_:FULL", reason="代表名称请求失败"),
        PreparedName("Whiterun {name}", "NPC_:FULL", " "),
        PreparedName("Whiterun {name}", "NPC_:FULL", "雪漫城"),
        PreparedName("Whiterun {name}", "NPC_:FULL", "雪漫城 {name}", reason="校验未通过"),
    ],
    ids=["original-changed", "context-changed", "failed", "empty", "syntax-changed", "text-with-error"],
)
def test_unusable_prepared_name_fails_closed_without_exact_match_or_model_fallback(make_translator, prepared):
    entry = _entry(original="Whiterun {name}")
    translator, provider, terms = make_translator(prepared={entry.identity: prepared})
    terms.exact[entry.original] = "术语捷径 {name}"

    result, collection = _translate(translator, [entry])

    assert provider.translation_calls == []
    assert provider.proofread_calls == []
    assert collection.get(entry.identity).translation == ""
    assert result.failed_count == 1
    assert result.success_count == 0
    assert result.entry_outcomes[entry.identity].status == "failed"
    assert "名称预翻译失败" in result.entry_outcomes[entry.identity].reason


def test_failed_prepared_name_does_not_block_unprepared_entries(make_translator):
    failed = _entry("failed")
    remaining = _entry("remaining", original="Other name")
    translator, provider, _ = make_translator(
        prepared={failed.identity: PreparedName(failed.original, failed.context, reason="模型未返回名称")}
    )

    result, collection = _translate(translator, [failed, remaining])

    assert provider.translation_calls == [("remaining",)]
    assert result.failed_count == 1
    assert result.success_count == 1
    assert collection.get(failed.identity).translation == ""
    assert collection.get(remaining.identity).translation == "雪漫城"


def test_prepared_name_cannot_bypass_candidate_persistence_failure(make_translator):
    class FailingCheckpoint(InMemoryTranslationCheckpointPort):
        def save(self, checkpoint):
            if checkpoint.candidates:
                raise OSError("candidate persistence unavailable")
            super().save(checkpoint)

    entry = _entry()
    translator, provider, _ = make_translator(
        prepared={entry.identity: PreparedName(entry.original, entry.context, "雪漫城")},
        checkpoint=FailingCheckpoint(),
    )

    result, collection = _translate(translator, [entry])

    assert provider.translation_calls == []
    assert collection.get(entry.identity).translation == ""
    assert result.entry_outcomes[entry.identity].status == "failed"
    assert result.success_count == 0
    assert "候选结果持久化失败" in result.entry_outcomes[entry.identity].reason


def test_legacy_term_repair_reference_cannot_bypass_failed_prepared_name(make_translator):
    entry = _entry(original="Travel to Whiterun.", context="BOOK:DESC")
    translator, provider, _ = make_translator(
        prepared={entry.identity: PreparedName(entry.original, entry.context, reason="早期预翻译失败")}, retrieval=True
    )
    provider.translation = "前往雪漫城。"

    result, collection = _translate(translator, [entry], term_repair_requirements={"name": {"Whiterun": "雪漫城"}})

    assert provider.translation_calls == []
    assert collection.get(entry.identity).translation == ""
    assert result.entry_outcomes[entry.identity].status == "failed"
    assert "早期预翻译失败" in result.entry_outcomes[entry.identity].reason


def test_legacy_term_repair_reference_does_not_retranslate_valid_prepared_name(make_translator):
    entry = _entry()
    translator, provider, _ = make_translator(
        prepared={entry.identity: PreparedName(entry.original, entry.context, "雪漫")}
    )

    result, collection = _translate(translator, [entry], term_repair_requirements={"name": {"Whiterun": "雪漫城"}})

    assert provider.translation_calls == []
    assert collection.get(entry.identity).translation == "雪漫"
    assert result.entry_outcomes[entry.identity].status == "succeeded"


def test_deferred_pretranslation_skips_seeder_and_generated_writes_but_commits_candidates(make_translator, monkeypatch):
    seeded, written = [], []
    monkeypatch.setattr("transbridge.ai_translator.existing_term_extractor.should_seed_existing_terms", lambda *_: True)

    class Seeder:
        def __init__(self, *_args, **_kwargs):
            seeded.append(True)

        def seed(self, *_args, **_kwargs):
            return SimpleNamespace(
                cancelled=False,
                added_count=0,
                error="",
                direct_added=0,
                text_added=0,
                conflicts=0,
                skipped_existing=0,
                conflict_records=(),
            )

    monkeypatch.setattr("transbridge.ai_translator.existing_term_extractor.ExistingTermSeeder", Seeder)
    translator, provider, _ = make_translator(defer=True, retrieval=True)
    monkeypatch.setattr(translator, "_save_generated_terms", lambda terms: written.extend(terms) or len(terms))
    entry = _entry()
    seeded_entry = replace(_entry("existing", original="Riverwood"), translation="溪木镇", stage=1)

    result, collection = _translate(translator, [entry, seeded_entry])

    assert provider.translation_calls == [("name",)]
    assert seeded == written == []
    assert result.new_dynamic_terms == 0
    assert collection.get(entry.identity).translation == "雪漫城"
    assert result.entry_outcomes[entry.identity].status == "succeeded"
    assert translator._in_flight_terms == {}


def test_regular_translation_keeps_generated_name_writes_enabled(make_translator, monkeypatch):
    written = []
    translator, provider, _ = make_translator(retrieval=True)
    monkeypatch.setattr(translator, "_save_generated_terms", lambda terms: written.extend(terms) or len(terms))
    entry = _entry()

    result, _ = _translate(translator, [entry])

    assert provider.translation_calls == [("name",)]
    assert written == [("Whiterun", "雪漫城", "auto_name", "NPC_:FULL")]
    assert result.new_dynamic_terms == 1
