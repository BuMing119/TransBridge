from __future__ import annotations

import json
import re
import threading
from types import SimpleNamespace

import pytest

from transbridge.ai_translator.translator import AutoTranslator, ProgressCheckpoint, TranslatorConfig
from transbridge.application.translation import InMemoryTranslationCheckpointPort
from transbridge.application.translation.entry_alias import ai_entry_key
from transbridge.config.llm import LLMConfig
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection


@pytest.mark.parametrize("strategy", ["proofread", "strict", "disabled"])
def test_translation_replaces_full_progress_before_postprocess_request(monkeypatch, tmp_path, strategy):
    from transbridge.ai_translator import existing_term_extractor, noun_extractor, term_database, translator

    events, logs = [], []
    elapsed = [0.0]

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
        def chat_stream(self, messages, max_tokens, callback):
            match = re.search(r"<translation_entries>\s*(.*?)\s*</translation_entries>", messages[-1]["content"], re.S)
            assert match
            response = json.dumps({
                "results": [{"entry_id": key, "translation": "你好"} for key in json.loads(match.group(1))]
            })
            callback(response)
            return response

        def chat(self, messages, max_tokens=0):
            assert any(event[:3] == ("translate", 1, 1) for event in events)
            payload = json.loads(messages[1]["content"])
            phase = payload.get("phase", "proofread")
            event_phase = {"refine": "refinement", "arbitrate": "arbitration"}.get(phase, phase)
            assert events[-1][:3] == (event_phase, 0, 1)
            elapsed[0] += 10
            if phase != "proofread":
                return json.dumps({
                    "results": [
                        {"entry_key": item["entry_key"], "value": "pass" if phase == "arbitrate" else "您好"}
                        for item in payload["entries"]
                    ]
                })
            return json.dumps({
                "results": [
                    {"entry_key": item["entry_key"], "final_translation": "您好"} for item in payload["entries"]
                ]
            })

        def cancel(self):
            pass

    monkeypatch.setattr(term_database, "TermDatabaseManager", lambda **kwargs: Terms())
    monkeypatch.setattr(noun_extractor, "NounExtractor", lambda *args, **kwargs: None)
    monkeypatch.setattr(existing_term_extractor, "should_seed_existing_terms", lambda *args: False)
    monkeypatch.setattr(ProgressCheckpoint, "save", lambda *args: None)
    monkeypatch.setattr(ProgressCheckpoint, "delete", lambda *args: None)
    monkeypatch.setattr(translator.time, "perf_counter", lambda: elapsed[0])
    collection = TranslationEntryCollection([TranslationEntry("one", "one", "Hello", "", 0, "BOOK:DESC")])
    config = LLMConfig(
        retrieval_enabled=False,
        enable_post_process=strategy != "disabled",
        pp_strategy="strict" if strategy == "strict" else "proofread",
        pp_enable_consistency_check=False,
        pp_enable_format_validation=False,
        pp_enable_quality_gate=False,
        pp_enable_refinement=True,
        pp_enable_polish=True,
        pp_enable_arbitration=True,
    )
    engine = AutoTranslator(
        TranslatorConfig(config, str(tmp_path / "fixture.esp")),
        llm_client=Client(),
        candidate_checkpoint=InMemoryTranslationCheckpointPort(),
    )
    result = engine.translate(
        collection,
        None,
        lambda c, t, m, *rest: events.append(("translate", c, t, m)),
        threading.Event(),
        stage_progress_callback=lambda *args: events.append(args),
        log_callback=lambda index, message: logs.append(message),
    )
    assert result.success_count == 1
    assert collection.get("one").translation == ("你好" if strategy == "disabled" else "您好")
    assert events[-2][0:3] == ("finalize", 0, 1)
    assert events[-1][0:3] == ("finalize", 1, 1)
    expected_elapsed = {"proofread": 10, "strict": 30, "disabled": 0}[strategy]
    assert logs[-1] == f"\n总耗时: {expected_elapsed:.2f}s"
