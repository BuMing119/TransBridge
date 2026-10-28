from __future__ import annotations

import json
import re
import threading
from types import SimpleNamespace

import pytest

from transbridge.ai_translator import existing_term_extractor, noun_extractor, term_database
from transbridge.ai_translator.activity import ActivityKind, ActivityMessage, batch_message
from transbridge.ai_translator.translator import AutoTranslator, TranslatorConfig
from transbridge.application.translation import InMemoryTranslationCheckpointPort
from transbridge.application.translation.entry_alias import ai_entry_key
from transbridge.config.llm import LLMConfig
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection


@pytest.mark.parametrize("mode", ["failure", "retry"])
def test_translation_produces_typed_actionable_events_without_raw_provider_errors(monkeypatch, tmp_path, mode):
    class Terms:
        def load_all(self):
            pass

        def get_load_log(self):
            return ()

        def exact_match(self, originals):
            return {}

        def match_terms_scoped(self, *, entries, **kwargs):
            return SimpleNamespace(flat_terms={}, terms_by_entry={ai_entry_key(e): {} for e in entries})

        def match_terms_for_entry(self, candidate):
            return {}

    class Client:
        calls = 0

        def chat_stream(self, messages, max_tokens, callback):
            self.calls += 1
            if mode == "failure":
                raise RuntimeError("provider unavailable at https://private.example/secret")
            match = re.search(r"<translation_entries>\s*(.*?)\s*</translation_entries>", messages[-1]["content"], re.S)
            entries = json.loads(match.group(1))
            result = [] if self.calls == 1 else [{"entry_id": key, "translation": "你好"} for key in entries]
            response = json.dumps({"results": result})
            callback(response)
            return response

    monkeypatch.setattr(term_database, "TermDatabaseManager", lambda **kwargs: Terms())
    monkeypatch.setattr(noun_extractor, "NounExtractor", lambda *args, **kwargs: None)
    monkeypatch.setattr(existing_term_extractor, "should_seed_existing_terms", lambda *args: False)
    collection = TranslationEntryCollection([
        TranslationEntry(key, key, f"Hello {key}", "", 0, "BOOK:DESC") for key in ("one", "two")
    ])
    config = LLMConfig(retrieval_enabled=False, enable_post_process=False)
    engine = AutoTranslator(
        TranslatorConfig(config, str(tmp_path / "fixture.esp")),
        llm_client=Client(),
        candidate_checkpoint=InMemoryTranslationCheckpointPort(),
    )
    logs = []
    result = engine.translate(
        collection,
        target_entry_ids=None,
        progress_callback=lambda *args: None,
        stop_event=threading.Event(),
        log_callback=lambda index, text: logs.append(batch_message(index, text)),
    )
    wanted = ActivityKind.ERROR if mode == "failure" else ActivityKind.RETRY
    events = [event for event in logs if isinstance(event, ActivityMessage) and event.kind == wanted]
    assert events and all(event.batch is not None for event in events)
    assert all("重试" in event and "private.example" not in event for event in events)
    assert (result.failed_count, result.success_count) == ((2, 0) if mode == "failure" else (0, 2))
